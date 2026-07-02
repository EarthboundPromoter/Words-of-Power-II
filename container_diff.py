"""
Root-1 container-diff (Unit 1) — capture for in-place container mutation.

The `Unit.__setattr__` interceptor (S22) sees attribute REBINDS only. The
tactical containers — `resists`, `tags`, the `*_bonuses` family, `cool_downs`,
`Spell.cur_charges` — are mutated IN PLACE (dict item writes, list appends),
which never touches `__setattr__`. This module holds referential value-copied
snapshots per unit and compares them at every boundary where the live cause can
change; each delta is recorded under the cause that was live for the just-closed
span. Enumeration of write sites is hopeless by census (~987 `.resists[`
occurrences game-wide, the mass in factory constructors) — the diff at causal
chokepoints is the only shape that generalizes to future content.

Design record: docs/UNIT1_ROOT1_CONTAINER_DIFF_BUILD_PLAN.md (gated S28).
Ledger authority: docs/capture_model/RATIFICATION_LEDGER.md, "Root 1 — LOCKED".

Owner-ruled invariants (S28):
  - FULL sweep: every snapshotted unit compared at every boundary — no
    owner-only fast path, no event-touched heuristic (both have verified
    counterexamples: essence-Reincarnation's re-cast writes resists on a
    target with zero events; DamageAssimilation writes the victim's resists
    from inside the attacker's action).
  - First observation = BASELINE, not change (no flood on level entry/load).
  - A routine engine tick (cool_downs -1 sweep, turns_to_death -1) that
    matches its signature EXACTLY is absorbed into the snapshot without a
    record; ANY deviation records in full (the ledger's predictable-tick
    optimization — the count stays exact, the noise stays out).
  - Zero-value entries equal absent keys (the game leaves explicit 0s behind:
    `+= 100` then `-= 100`; factory dicts carry explicit zeros).
  - The compare NEVER indexes live defaultdicts (index inserts keys); it
    iterates `.items()` only, and snapshots via iteration copies.
  - Records-only: nothing here speaks. Voice candidacies are composer-phase
    work (G-A..G-E verdicts in the ledger).

This file's pure core (store/compare/signatures) is game-import-free so the
lightweight tests stay cheap; the boundary wraps and journal coupling install
via install() (later build steps) and are self-gating like log_capture.
"""


# ----------------------------------------------------------------------
# Record kinds (plan D7) — one per domain, matching the ledger's per-gap
# verdict structure. All six join the producers' known-sets at install.
# ----------------------------------------------------------------------

KIND_RESISTS = 'resists_change'        # G-A
KIND_TAGS = 'tags_change'              # G-B
KIND_STAT_BONUS = 'stat_bonus_change'  # G-C (payload names the bonus domain)
KIND_CHARGES = 'charges_change'        # G-D (spell shelf)
KIND_COOLDOWN = 'cooldown_change'      # G-E
KIND_LIFESPAN = 'lifespan_change'      # turns_to_death

ALL_KINDS = (KIND_RESISTS, KIND_TAGS, KIND_STAT_BONUS, KIND_CHARGES,
             KIND_COOLDOWN, KIND_LIFESPAN)


# ----------------------------------------------------------------------
# Key naming — container keys are Tag objects (.name), spell classes
# (type), spell instances (.name), or plain strings. Payloads carry the
# derived names only (journal discipline: primitives, no live refs).
# ----------------------------------------------------------------------

def _key_name(k):
    if isinstance(k, str):
        return k
    if isinstance(k, type):
        return getattr(k, '__name__', str(k))
    name = getattr(k, 'name', None)
    if isinstance(name, str):
        return name
    return str(k)


# ----------------------------------------------------------------------
# Normalized snapshots — value-copies with zeros dropped, keys named.
# Built by ITERATION only (never indexes the live container: indexing a
# defaultdict inserts the key — the read-only guarantee would be broken
# by the observation itself).
# ----------------------------------------------------------------------

def _snap_flat(d):
    """{key: int} -> {name: int}, zero entries dropped."""
    out = {}
    if d:
        for k, v in d.items():
            if v:
                out[_key_name(k)] = v
    return out


def _snap_nested(d):
    """{outer: {attr: int}} -> {outer_name: {attr: int}}, zeros and empty
    inner dicts dropped."""
    out = {}
    if d:
        for outer, inner in d.items():
            if not inner:
                continue
            snap_inner = {}
            for attr, amt in inner.items():
                if amt:
                    snap_inner[_key_name(attr)] = amt
            if snap_inner:
                out[_key_name(outer)] = snap_inner
    return out


def _snap_tags(tags):
    """Tag list -> set of names. Membership is what matters (the game
    guards every append with a `not in` check; order is render-only)."""
    if not tags:
        return set()
    return {_key_name(t) for t in tags}


# ----------------------------------------------------------------------
# Compares — walk live vs stored, allocation-free on the clean path,
# zero == absent throughout. Return {} / empty when no real change.
# ----------------------------------------------------------------------

def _diff_flat(live, stored):
    """-> {name: (before, after)} for real changes only."""
    delta = {}
    seen = set()
    if live:
        for k, v in live.items():
            nk = _key_name(k)
            seen.add(nk)
            before = stored.get(nk, 0)
            if v != before:
                # v may be an explicit zero the stored snapshot never kept;
                # before==0 handles both absent and zero uniformly.
                if v or before:
                    delta[nk] = (before, v)
    for nk, before in stored.items():
        if nk not in seen and before:
            delta[nk] = (before, 0)
    return delta


def _diff_nested(live, stored):
    """-> {outer_name: {attr: (before, after)}} for real changes only."""
    delta = {}
    seen_outer = set()
    if live:
        for outer, inner in live.items():
            no = _key_name(outer)
            seen_outer.add(no)
            d = _diff_flat(inner, stored.get(no, {}))
            if d:
                delta[no] = d
    for no, inner in stored.items():
        if no not in seen_outer and inner:
            d = {attr: (before, 0) for attr, before in inner.items() if before}
            if d:
                delta[no] = d
    return delta


def _diff_tags(live, stored):
    """-> {'added': [names], 'removed': [names]} or {}."""
    live_set = _snap_tags(live)
    if live_set == stored:
        return {}
    return {
        'added': sorted(live_set - stored),
        'removed': sorted(stored - live_set),
    }


# ----------------------------------------------------------------------
# Routine-tick signatures (plan D5) — the two engine per-turn decrements
# that would otherwise emit a record per unit per turn. A delta matching
# its signature EXACTLY is absorbed (snapshot advances, no record); any
# deviation fails the signature and records in full. Per-domain,
# per-unit, per-sweep — never cross-domain.
# ----------------------------------------------------------------------

def _is_routine_cooldown_tick(delta):
    """Level.py:2108 rebuilds cool_downs as {spell: cd-1 for ... if cd > 1}:
    every surviving entry drops by exactly 1 (to >= 1), entries at 1 vanish
    (before 1 -> after 0), nothing appears."""
    if not delta:
        return False
    for name, (before, after) in delta.items():
        if not (isinstance(before, int) and isinstance(after, int)):
            return False
        if after == before - 1 and after >= 1:
            continue
        if before == 1 and after == 0:
            continue
        return False
    return True


def _is_routine_lifespan_tick(before, after):
    """Level.py:2167-2168: turns_to_death -= 1 (kill fires at <= 0)."""
    return (isinstance(before, int) and isinstance(after, int)
            and after == before - 1)


# ----------------------------------------------------------------------
# The store — referential, value-copied, baseline-not-change.
# ----------------------------------------------------------------------

_FLAT_DOMAINS = ('resists', 'cool_downs')
_WIZARD_FLAT_DOMAINS = ('global_bonuses', 'global_bonuses_pct')
_WIZARD_NESTED_DOMAINS = ('spell_bonuses', 'spell_bonuses_pct',
                          'tag_bonuses', 'tag_bonuses_pct')

# domain -> record kind
_DOMAIN_KINDS = {
    'resists': KIND_RESISTS,
    'tags': KIND_TAGS,
    'cool_downs': KIND_COOLDOWN,
    'turns_to_death': KIND_LIFESPAN,
    'global_bonuses': KIND_STAT_BONUS,
    'global_bonuses_pct': KIND_STAT_BONUS,
    'spell_bonuses': KIND_STAT_BONUS,
    'spell_bonuses_pct': KIND_STAT_BONUS,
    'tag_bonuses': KIND_STAT_BONUS,
    'tag_bonuses_pct': KIND_STAT_BONUS,
}


def _unit_snapshot(unit, wizard):
    snap = {
        'resists': _snap_flat(getattr(unit, 'resists', None)),
        'tags': _snap_tags(getattr(unit, 'tags', None)),
        'cool_downs': _snap_flat(getattr(unit, 'cool_downs', None)),
        'turns_to_death': getattr(unit, 'turns_to_death', None),
    }
    if wizard:
        for dom in _WIZARD_FLAT_DOMAINS:
            snap[dom] = _snap_flat(getattr(unit, dom, None))
        for dom in _WIZARD_NESTED_DOMAINS:
            snap[dom] = _snap_nested(getattr(unit, dom, None))
    return snap


class ContainerStore:
    """{unit -> normalized snapshot} + the wizard's spell shelf.

    Keyed by id() with the live ref held alongside: the ref keeps the id
    stable while the entry lives, and an id reused after remove_obj dropped
    its entry simply baselines as new. Reseeded (cleared) at the level /
    post-load boundary — the first sweep after a reseed baselines everything
    silently.
    """

    def __init__(self):
        self._units = {}    # id(unit) -> (unit, snapshot)
        self._spells = {}   # id(spell) -> (spell, cur_charges)

    def reseed(self):
        self._units.clear()
        self._spells.clear()

    def drop_unit(self, unit):
        self._units.pop(id(unit), None)

    # -- units --

    def diff_unit(self, unit):
        """-> list of (domain, delta) for real, non-routine changes; [] on
        baseline (first observation) and on no-change. Snapshot advances to
        current truth either way."""
        wizard = bool(getattr(unit, 'is_player_controlled', False))
        entry = self._units.get(id(unit))
        if entry is None or entry[0] is not unit:
            self._units[id(unit)] = (unit, _unit_snapshot(unit, wizard))
            return []

        stored = entry[1]
        out = []
        dirty = False

        d = _diff_flat(getattr(unit, 'resists', None), stored['resists'])
        if d:
            out.append(('resists', d))
            dirty = True

        d = _diff_tags(getattr(unit, 'tags', None), stored['tags'])
        if d:
            out.append(('tags', d))
            dirty = True

        d = _diff_flat(getattr(unit, 'cool_downs', None),
                       stored['cool_downs'])
        if d:
            dirty = True
            if not _is_routine_cooldown_tick(d):
                out.append(('cool_downs', d))

        ttd_before = stored['turns_to_death']
        ttd_after = getattr(unit, 'turns_to_death', None)
        if ttd_after != ttd_before:
            dirty = True
            if not _is_routine_lifespan_tick(ttd_before, ttd_after):
                out.append(('turns_to_death',
                            {'turns_to_death': (ttd_before, ttd_after)}))

        if wizard:
            for dom in _WIZARD_FLAT_DOMAINS:
                d = _diff_flat(getattr(unit, dom, None), stored.get(dom, {}))
                if d:
                    out.append((dom, d))
                    dirty = True
            for dom in _WIZARD_NESTED_DOMAINS:
                d = _diff_nested(getattr(unit, dom, None),
                                 stored.get(dom, {}))
                if d:
                    out.append((dom, d))
                    dirty = True

        if dirty:
            self._units[id(unit)] = (unit, _unit_snapshot(unit, wizard))
        return out

    # -- the spell shelf (wizard's spells; ~20 ints) --

    def diff_spells(self, player):
        """-> list of (spell_name, before, after) for changed charges; new
        spells baseline silently, vanished spells drop silently (membership
        churn is shop-menu territory, not this unit's)."""
        spells = getattr(player, 'spells', None) or ()
        out = []
        seen = set()
        for spell in spells:
            sid = id(spell)
            seen.add(sid)
            cur = getattr(spell, 'cur_charges', None)
            entry = self._spells.get(sid)
            if entry is None or entry[0] is not spell:
                self._spells[sid] = (spell, cur)
                continue
            if cur != entry[1]:
                out.append((getattr(spell, 'name', None)
                            or type(spell).__name__, entry[1], cur))
                self._spells[sid] = (spell, cur)
        for sid in [s for s in self._spells if s not in seen]:
            del self._spells[sid]
        return out


store = ContainerStore()


def domain_kind(domain):
    return _DOMAIN_KINDS[domain]


# ----------------------------------------------------------------------
# The sweep — journal-coupled. Runs at every boundary where the live
# cause can change (plan D2); every delta records under the cause live
# for the just-closed span via the journal's normal parenting. The sweep
# is pure observation behind an exception guard: a bug here loses a
# record, never touches the game (the wraps below call the original
# unconditionally).
# ----------------------------------------------------------------------

_installed = False
_log_fn = None
_failed_notes = set()      # once-per-site failure-note dedupe

# The suspended cast window (plan D2 boundary 4a, window-defer semantics).
# The engine clears current_cast_context between steps of the same queued
# generator (the per-step finally); that restore SUSPENDS the window rather
# than closing it — _pending_ctx remembers whose window is open so the next
# boundary attributes the span correctly. Set/cleared ONLY by: a ctx switch
# (a DIFFERENT cast arrives), an action push (a new action definitively
# interrupts the window), the turn boundary, and reseed.
_pending_ctx = None


def _note_failure(site, exc):
    if site in _failed_notes:
        return
    _failed_notes.add(site)
    if _log_fn:
        try:
            _log_fn(f"[ContainerDiff] sweep failed at {site}: {exc!r}")
        except Exception:
            pass


def _span_parent_seq():
    """Parent for a delta record = the cause live for the just-closed span:
    the cause-stack top (nested actions/events) -> the SUSPENDED cast window
    (pending) -> the LIVE cast context (mid-step spans) -> None (drift).
    Extends journal._current_parent_seq with the pending-window rung."""
    from journal import journal
    if journal.cause_stack:
        return journal.cause_stack[-1]['sequence']
    pend = _pending_ctx
    if pend is not None:
        cb = getattr(pend, '_cast_begin', None)
        if cb is not None:
            return cb['sequence']
    lvl = journal._level
    if lvl is not None:
        ctx = getattr(lvl, 'current_cast_context', None)
        if ctx is not None:
            cb = getattr(ctx, '_cast_begin', None)
            if cb is not None:
                return cb['sequence']
    return None


def _emit_deltas(level, bracket, detail, parent_fn):
    from journal import journal, _snapshot_unit
    units = getattr(level, 'units', None)
    if units:
        for unit in list(units):
            deltas = store.diff_unit(unit)
            if not deltas:
                continue
            snap = _snapshot_unit(unit)
            for domain, changes in deltas:
                parent = parent_fn()
                journal.record(domain_kind(domain), {
                    'unit': snap,
                    'domain': domain,
                    'changes': changes,
                    'bracket': bracket,
                    'detail': detail,
                    'unattributed': parent is None,
                }, parent)
    player = getattr(level, 'player_unit', None)
    if player is not None:
        for (spell_name, before, after) in store.diff_spells(player):
            parent = parent_fn()
            journal.record(KIND_CHARGES, {
                'unit': _snapshot_unit(player),
                'spell': spell_name,
                'before': before,
                'after': after,
                'bracket': bracket,
                'detail': detail,
                'unattributed': parent is None,
            }, parent)


def sweep(level, bracket=None, detail=None, site='?', parent_fn=None):
    """Full sweep of the level (plan D1: every snapshotted unit + the spell
    shelf, no owner-only fast path). bracket names the mechanism of the
    JUST-CLOSED span for exit sweeps ('buff_apply', 'equip', ...); entry
    sweeps pass None — their deltas belong to the enclosing span, whose
    identity is the record's parent, not a mechanism tag. parent_fn
    overrides parent resolution (the ctx-switch sweep pins the CLOSING
    window's cast explicitly — the live context already points at the new
    one by store-first time)."""
    if level is None:
        return
    try:
        _emit_deltas(level, bracket, detail, parent_fn or _span_parent_seq)
    except Exception as e:
        _note_failure(site, e)


def reseed():
    """Level entry / post-load boundary: drop all snapshots. The first
    sweep after a reseed baselines everything silently (no flood)."""
    global _pending_ctx
    _pending_ctx = None
    store.reseed()


def turn_boundary(level):
    """The mod turn boundary (plan D2 boundary 7, the fire_pipeline block):
    closes the turn's final span — including any still-suspended cast
    window. This is where the routine engine ticks (cool_downs sweep,
    turns_to_death decrement — both outside every bracket) get absorbed by
    their signatures, and where any unbracketed drift surfaces as honest
    unattributed records."""
    global _pending_ctx
    sweep(level, site='turn_boundary')
    _pending_ctx = None


# ----------------------------------------------------------------------
# Boundary wraps (plan D2, boundaries 1 / 5 / 6) — the synchronous set.
# Entry sweep closes the outer span; the original runs untouched; exit
# sweep (in finally) closes this bracket's span with its mechanism tag.
# Boundaries 2/3/4 (tick, cast root, cast windows) hook the journal's
# own push/pop and the current_cast_context property in later steps.
# ----------------------------------------------------------------------

def install(log_fn=None):
    """Wrap the synchronous boundary methods. Self-gating: verifies the
    RW3 shapes first and declines cleanly (mod runs diff-less) when absent
    — the RW2 backport story. Idempotent; joins the shared, unrestored,
    whole-process monkeypatch category (no teardown)."""
    global _installed, _log_fn
    if _installed:
        return True
    if log_fn is not None:
        _log_fn = log_fn

    try:
        import Level
    except ImportError:
        _decline("game Level module not importable")
        return False

    buff_cls = getattr(Level, 'Buff', None)
    unit_cls = getattr(Level, 'Unit', None)
    level_cls = getattr(Level, 'Level', None)
    needed = (
        getattr(buff_cls, 'apply', None),
        getattr(buff_cls, 'unapply', None),
        getattr(unit_cls, 'equip', None),
        getattr(unit_cls, 'unequip', None),
        getattr(level_cls, 'remove_obj', None),
    )
    if not all(callable(f) for f in needed):
        _decline("RW3 buff/equip/remove shapes not found")
        return False

    original_buff_apply = buff_cls.apply
    original_buff_unapply = buff_cls.unapply
    original_unit_equip = unit_cls.equip
    original_unit_unequip = unit_cls.unequip
    original_remove_obj = level_cls.remove_obj

    def patched_buff_apply(self, owner):
        lvl = getattr(owner, 'level', None)
        sweep(lvl, site='buff_apply:entry')
        try:
            return original_buff_apply(self, owner)
        finally:
            sweep(lvl, bracket='buff_apply',
                  detail=_buff_detail(self), site='buff_apply:exit')

    def patched_buff_unapply(self):
        lvl = getattr(getattr(self, 'owner', None), 'level', None)
        sweep(lvl, site='buff_unapply:entry')
        try:
            return original_buff_unapply(self)
        finally:
            sweep(lvl, bracket='buff_unapply',
                  detail=_buff_detail(self), site='buff_unapply:exit')

    def patched_unit_equip(self, item):
        lvl = getattr(self, 'level', None)
        sweep(lvl, site='equip:entry')
        try:
            return original_unit_equip(self, item)
        finally:
            sweep(lvl, bracket='equip',
                  detail=_item_detail(item), site='equip:exit')

    def patched_unit_unequip(self, item):
        lvl = getattr(self, 'level', None)
        sweep(lvl, site='unequip:entry')
        try:
            return original_unit_unequip(self, item)
        finally:
            sweep(lvl, bracket='unequip',
                  detail=_item_detail(item), site='unequip:exit')

    def patched_remove_obj(self, obj):
        # Final-state sweep BEFORE the removal (deltas belong to the
        # enclosing span — e.g. the killing cast), then drop the snapshot.
        sweep(self, site='remove_obj')
        try:
            return original_remove_obj(self, obj)
        finally:
            try:
                store.drop_unit(obj)
            except Exception:
                pass

    buff_cls.apply = patched_buff_apply
    buff_cls.unapply = patched_buff_unapply
    unit_cls.equip = patched_unit_equip
    unit_cls.unequip = patched_unit_unequip
    level_cls.remove_obj = patched_remove_obj

    _install_span_sweeps()
    _install_ctx_property(level_cls)

    _installed = True
    if _log_fn:
        try:
            _log_fn("[ContainerDiff] boundary wraps installed")
        except Exception:
            pass
    return True


# ----------------------------------------------------------------------
# Boundary 4a — the current_cast_context property (owner-ruled Option A,
# S28). The engine assigns this attribute around every queued-generator
# step (Level.py:3320-3323) and every inline run (4209-4213) — the engine
# announces its own cast-window transitions. The property observes them.
#
# Safety invariants (plan D2 / gate-confirmed):
#   - STORE FIRST: the setter lands the value in the instance __dict__
#     before any mod code runs; observation is exception-guarded. A bug
#     here can only lose a sweep, never disturb the cast flow.
#   - SAME KEY: storage is __dict__['current_cast_context'] — the exact
#     key Level.__getstate__ copies and nulls (Level.py:2924/2928), so
#     pickle output is byte-identical to vanilla and no mod-named slot
#     (which could carry live spell/unit refs) ever enters a save.
#   - Window-defer: a restore to None SUSPENDS the window (pending); the
#     sweep fires only when a DIFFERENT cast arrives — consecutive steps
#     of one generator form one window; interleaved generators still
#     close against their own casts.
# ----------------------------------------------------------------------

def _install_ctx_property(level_cls):
    def _ctx_get(self):
        return self.__dict__.get('current_cast_context')

    def _ctx_set(self, value):
        # STORE FIRST — zero mod code before this line (named invariant).
        self.__dict__['current_cast_context'] = value
        try:
            _on_ctx_set(self, value)
        except Exception as e:
            _note_failure('ctx:set', e)

    level_cls.current_cast_context = property(_ctx_get, _ctx_set)


def _on_ctx_set(level, value):
    global _pending_ctx
    if value is None or value is _pending_ctx:
        # None = the engine's per-step restore: SUSPEND, window stays open.
        # Same ctx = the same generator's next step: the window continues.
        return
    old = _pending_ctx
    _pending_ctx = None
    if old is not None:
        # A different cast arrived: close the suspended window. Parent is
        # pinned to the CLOSING cast explicitly — the live context already
        # points at the new cast (store-first), and the stack is empty
        # between generator steps.
        cb = getattr(old, '_cast_begin', None)
        cb_seq = cb['sequence'] if cb is not None else None
        sweep(level, bracket='cast_window', detail=_ctx_detail(old),
              site='ctx:switch', parent_fn=lambda: cb_seq)
    else:
        # No suspended window (first cast of a batch): close whatever span
        # was open, WITHOUT the live-ctx fallback (it would misattribute
        # the pre-cast span to the cast that just arrived).
        from journal import journal
        stack_seq = (journal.cause_stack[-1]['sequence']
                     if journal.cause_stack else None)
        sweep(level, site='ctx:open', parent_fn=lambda: stack_seq)
    _pending_ctx = value


def _ctx_detail(ctx):
    spell = getattr(ctx, 'spell', None)
    return {'spell': getattr(spell, 'name', None)
            or (type(spell).__name__ if spell is not None else None)}


# Cause kinds whose push/pop marks an ACTION span (plan D2 boundaries
# 2/3/4). Event raises (EventOn*) push/pop too but are NOT boundaries —
# a reactive write during a raise attributes to the enclosing action
# (the ledger's granularity; event-grade sweeping would triple the
# sweep count for no ratified gain).
_ACTION_KINDS = frozenset((
    'buff_tick', 'equipment_tick', 'cloud_tick', 'cast_begin',
))


def _install_span_sweeps():
    """Boundaries 2/3/4 in one seam: the journal's own cause machinery.

    - journal.push/pop wraps (kind-filtered to action causes): the tick
      roots (buff/equipment/cloud) and execute_cast's cast_begin span.
    - the _wrap_with_cause span hooks: per-step windows of every
      direct-queue_spell generator (channels, the ~150 content sites,
      reaction-queued casts — the ⟨GATE⟩ 4b carrier). The hooks skip
      action-kind causes (the push/pop wrap already sweeps those) so no
      span sweeps twice.

    Sweeps read the level from journal._level, which the mod's hooks
    maintain at every action root (execute_cast, queue_spell, and the
    tick wraps). Entry sweeps close the OUTER span (before the push, old
    stack top parents); exit sweeps close the action's own span (before
    the pop, the action still parents).
    """
    import journal as journal_module
    from journal import journal

    original_push = journal.push
    original_pop = journal.pop

    def patched_push(record):
        global _pending_ctx
        try:
            if (record is not None
                    and record.get('event_type') in _ACTION_KINDS):
                # The sweep first (parent resolution may claim the suspended
                # window's span), then the new action definitively interrupts
                # any suspended cast window.
                sweep(journal._level, site='span:push')
                _pending_ctx = None
        except Exception as e:
            _note_failure('span:push', e)
        return original_push(record)

    def patched_pop():
        try:
            stack = journal.cause_stack
            if stack:
                kind = stack[-1].get('event_type')
                if kind in _ACTION_KINDS:
                    sweep(journal._level, bracket=kind, site='span:pop')
        except Exception as e:
            _note_failure('span:pop', e)
        return original_pop()

    journal.push = patched_push
    journal.pop = patched_pop

    def _span_enter(cause):
        try:
            if cause is None or cause.get('event_type') in _ACTION_KINDS:
                return
            sweep(journal._level, site='span:enter')
        except Exception as e:
            _note_failure('span:enter', e)

    def _span_exit(cause):
        try:
            if cause is None or cause.get('event_type') in _ACTION_KINDS:
                return
            sweep(journal._level, bracket=cause.get('event_type'),
                  site='span:exit')
        except Exception as e:
            _note_failure('span:exit', e)

    journal_module.span_enter_hook = _span_enter
    journal_module.span_exit_hook = _span_exit


def _buff_detail(buff):
    return {'buff': getattr(buff, 'name', None) or type(buff).__name__}


def _item_detail(item):
    return {'item': getattr(item, 'name', None) or type(item).__name__}


def _decline(reason):
    if _log_fn:
        try:
            _log_fn(f"[ContainerDiff] install declined: {reason} — "
                    "mod runs container-diff-less")
        except Exception:
            pass
