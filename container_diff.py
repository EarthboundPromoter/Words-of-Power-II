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
# Unit 5 D3: per-tile flavor (chasm_type + tileset) — rendered (sprites,
# chasm skins) AND rule-read (lava/swamp chasm mechanics, Spells.py:3347/
# 10324/16663), yet written both via set_tileset/set_group_tileset AND by
# direct tile-attr writes with no chokepoint. The method hooks catch the
# former; the snapshot sweep below (riding the same boundaries as the unit
# diff) catches the latter with bracketed attribution intact.
KIND_TILE_FLAVOR = 'tile_flavor_change'

ALL_KINDS = (KIND_RESISTS, KIND_TAGS, KIND_STAT_BONUS, KIND_CHARGES,
             KIND_COOLDOWN, KIND_LIFESPAN, KIND_TILE_FLAVOR)


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


# Dirty-marking wrappers (plan REV B, D1/D2). Module-level import is safe
# here: dirty_containers is mod-local pure Python (collections only), so the
# pure-logic tests stay cheap and nothing game-side loads early.
import dirty_containers as _dirty

# The container attrs wrapped on EVERY unit. Wizard-only domains (flat and
# nested bonus families) are deliberately NOT wrapped: the drained sweep
# always compares the wizard (D5), which covers them completely — including
# the nested double-subscript writes no outer wrapper can see.
_WRAPPED_ATTRS = ('resists', 'cool_downs', 'tags')


def _wrap_unit_containers(unit):
    """Install dirty-marking twins on a unit's watched containers.
    Idempotent and owner-aware (D2): already-owned wrappers are skipped;
    a wrapper owned by a DIFFERENT unit (the Grey Goo donation shape) is
    re-owned IN PLACE — object identity is preserved because the game may
    intend the alias. Assignment bypasses the (journal-patched)
    Unit.__setattr__ via object.__setattr__: no records, no marks, and no
    interaction with the step-3 rebind watch. Pure observation setup — any
    failure leaves the plain container in place (full-sweep-era behavior)."""
    oid = id(unit)
    from collections import defaultdict
    for name in _WRAPPED_ATTRS:
        try:
            cur = getattr(unit, name, None)
            if cur is None:
                continue
            if isinstance(cur, _dirty._DirtyMarkMixin):
                if cur._owner_id != oid:
                    cur._owner_id = oid
                continue
            if isinstance(cur, defaultdict):
                wrapped = _dirty.DirtyDefaultDict(cur.default_factory, cur)
            elif isinstance(cur, dict):
                wrapped = _dirty.DirtyDict(cur)
            elif isinstance(cur, list):
                wrapped = _dirty.DirtyList(cur)
            else:
                continue
            _dirty.own(wrapped, oid)
            object.__setattr__(unit, name, wrapped)
        except Exception:
            pass


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
            # First sight (or id reuse): baseline AND wrap. Wrapping rides
            # the same moment as the snapshot so "seeded" always implies
            # "marked from here on" (D2).
            _wrap_unit_containers(unit)
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


# ----------------------------------------------------------------------
# The tile-flavor snapshot (Unit 5 D3). {(x, y): (chasm_type, tileset)}
# for the LIVE level only — sweeps can be handed a GEN level mid-play
# (buff_apply during next-level generation), which must neither diff nor
# disturb the snapshot. First observation of the live level = baseline.
# 324 tuple compares per sweep at 18x18 — proportionally small next to
# the all-units container compare.
# ----------------------------------------------------------------------

_flavor_snapshot = {}
_flavor_level = None


def _tile_flavor(tile):
    return (getattr(tile, 'chasm_type', None), getattr(tile, 'tileset', None))


def _diff_flavor(level):
    """-> list of (x, y, before_pair, after_pair) for changed tiles on the
    live level; [] on baseline, non-live level, or no change. Snapshot
    advances to current truth for every reported tile."""
    global _flavor_level
    from journal import journal
    if level is None or level is not journal._level:
        return []
    tiles = getattr(level, 'tiles', None)
    if not tiles:
        return []
    if _flavor_level is not level:
        _flavor_snapshot.clear()
        for col in tiles:
            for t in col:
                _flavor_snapshot[(t.x, t.y)] = _tile_flavor(t)
        _flavor_level = level
        return []
    out = []
    for col in tiles:
        for t in col:
            cur = _tile_flavor(t)
            key = (t.x, t.y)
            prev = _flavor_snapshot.get(key)
            if prev == cur:
                continue
            _flavor_snapshot[key] = cur
            if prev is None:
                continue      # unseen tile: baseline, not change
            out.append((key[0], key[1], prev, cur))
    return out


def _flavor_note_hooked_change(level, x, y, cur):
    """Method hooks (set_tileset/set_group_tileset) advance the snapshot
    inline as they record, so the boundary sweep never double-reports a
    hooked change. A not-yet-seeded level stays untouched — the first sweep
    baselines with post-change truth."""
    if _flavor_level is level:
        _flavor_snapshot[(x, y)] = cur


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

# The unattributed-drift alarm (plan D6) — the Root-1 analogue of the
# oracle's unknown-template alarm. A delta with NO mechanism bracket and
# NO causal parent means a write path escaped every boundary; recurring
# sources are missing-boundary findings. Dev-only: flushed at the turn
# boundary through the telemetry seam (shipped installs pass None and
# this stays inert). Deduped per (domain, unit-name) per realm — the
# expected sources until Units 2/3 land (pickup / equipment-trigger /
# craft effects) must not flood the JSONL every turn.
_drift_seen = set()
_drift_pending = []

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
                # 'unattributed' = neither a causal parent NOR a mechanism
                # bracket claims this delta (D6 drift). A bracket-tagged
                # record without a tree parent still knows its mechanism
                # ("during buff X's apply") — attributed, just rootless.
                orphaned = parent is None and bracket is None
                journal.record(domain_kind(domain), {
                    'unit': snap,
                    'domain': domain,
                    'changes': changes,
                    'bracket': bracket,
                    'detail': detail,
                    'unattributed': orphaned,
                }, parent)
                if orphaned:
                    _note_drift(domain, snap.get('name'))
    player = getattr(level, 'player_unit', None)
    if player is not None:
        for (spell_name, before, after) in store.diff_spells(player):
            parent = parent_fn()
            orphaned = parent is None and bracket is None
            journal.record(KIND_CHARGES, {
                'unit': _snapshot_unit(player),
                'spell': spell_name,
                'before': before,
                'after': after,
                'bracket': bracket,
                'detail': detail,
                'unattributed': orphaned,
            }, parent)
            if orphaned:
                _note_drift('charges', spell_name)
    # Unit 5 D3: tile-flavor deltas — catches the DIRECT chasm_type/tileset
    # writes (water->swamp conversions, corruption skins) under the cause
    # live for the just-closed span, same as every other domain.
    for (x, y, before, after) in _diff_flavor(level):
        parent = parent_fn()
        orphaned = parent is None and bracket is None
        journal.record(KIND_TILE_FLAVOR, {
            'x': x,
            'y': y,
            'chasm_type_before': before[0],
            'chasm_type_after': after[0],
            'tileset_before': before[1],
            'tileset_after': after[1],
            'via': 'sweep',
            'bracket': bracket,
            'detail': detail,
            'unattributed': orphaned,
        }, parent)
        if orphaned:
            _note_drift('tile_flavor', "%s,%s" % (x, y))


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


def _note_drift(domain, name):
    key = (domain, name)
    if key in _drift_seen:
        return
    _drift_seen.add(key)
    _drift_pending.append({'domain': domain, 'unit': name})


def reseed():
    """Level entry / post-load boundary: drop all snapshots. The first
    sweep after a reseed baselines everything silently (no flood)."""
    global _pending_ctx, _flavor_level
    _pending_ctx = None
    _drift_seen.clear()
    _drift_pending.clear()
    _flavor_snapshot.clear()
    _flavor_level = None
    store.reseed()


def turn_boundary(level, telemetry_mod=None):
    """The mod turn boundary (plan D2 boundary 7, the fire_pipeline block):
    closes the turn's final span — including any still-suspended cast
    window. This is where the routine engine ticks (cool_downs sweep,
    turns_to_death decrement — both outside every bracket) get absorbed by
    their signatures, and where any unbracketed drift surfaces as honest
    unattributed records. Drift alarms flush here through the telemetry
    seam (dev-only; None/no-sentinel -> inert)."""
    global _pending_ctx
    sweep(level, site='turn_boundary')
    _pending_ctx = None
    if _drift_pending:
        try:
            enabled = getattr(telemetry_mod, 'is_enabled', None)
            if callable(enabled) and enabled():
                for item in _drift_pending:
                    telemetry_mod.emit('container_drift', **item)
        except Exception as e:
            _note_failure('drift_flush', e)
        finally:
            _drift_pending.clear()


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
    # Seed-on-add (plan D2.1, gate-amended) — getattr-gated like the Unit 5
    # hooks so the RW2 backport declines per-hook.
    original_add_obj = getattr(level_cls, 'add_obj', None)
    # Unit 5 D3: getattr-gated (RW2 backport declines per-hook).
    original_set_tileset = getattr(level_cls, 'set_tileset', None)
    original_set_group_tileset = getattr(level_cls, 'set_group_tileset', None)

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

    def patched_add_obj(self, obj, x, y):
        # Seed-and-wrap the moment a unit enters play (D2.1). Closes the
        # bootstrap gap the gate found: under the drained sweep nothing
        # else observes a new unit until its own pre_advance or the turn
        # boundary, so an in-place write in that window would fold into a
        # late baseline instead of recording. Baselining here means writes
        # from the very next moment on produce real, correctly-parented
        # deltas. Original runs FIRST — seeding is post-placement
        # observation, exception-guarded, never in the engine's way.
        result = original_add_obj(self, obj, x, y)
        try:
            if isinstance(obj, unit_cls):
                store.diff_unit(obj)
        except Exception:
            pass
        return result

    def _record_flavor_method_changes(level, pre, via):
        # pre = [(x, y, chasm_type_before, tileset_before)]. Emit only for
        # tiles the method actually changed; advance the snapshot inline so
        # the boundary sweep never double-reports (D3 dedup, gate-confirmed
        # airtight for the make_* primitives, pinned here for these hooks).
        try:
            from journal import journal
            for (x, y, ct_b, ts_b) in pre:
                t = level.tiles[x][y]
                cur = _tile_flavor(t)
                if cur == (ct_b, ts_b):
                    continue
                parent = _span_parent_seq()
                journal.record(KIND_TILE_FLAVOR, {
                    'x': x,
                    'y': y,
                    'chasm_type_before': ct_b,
                    'chasm_type_after': cur[0],
                    'tileset_before': ts_b,
                    'tileset_after': cur[1],
                    'via': via,
                    'bracket': None,
                    'detail': None,
                    'unattributed': parent is None,
                }, parent)
                _flavor_note_hooked_change(level, x, y, cur)
        except Exception as e:
            _note_failure(via, e)

    def patched_set_tileset(self, tileset, chasm_type):
        # Whole-level flavor write (Level.py:4355). Live-gated: gen-time
        # calls (LevelGen.py:491, Vaults.py:242) pass through untouched.
        from journal import journal
        if self is not journal._level:
            return original_set_tileset(self, tileset, chasm_type)
        pre = None
        try:
            pre = [(t.x, t.y) + _tile_flavor(t)
                   for col in self.tiles for t in col]
        except Exception:
            pre = None
        try:
            return original_set_tileset(self, tileset, chasm_type)
        finally:
            if pre is not None:
                _record_flavor_method_changes(self, pre, 'set_tileset')

    def patched_set_group_tileset(self, points, tileset, chasm_type):
        # Point-list flavor write (Level.py:4361) — the runtime lava-spread
        # shape. points may be a generator: the non-live path passes it
        # through untouched; the live path materializes it once (the
        # original iterates it exactly once — semantics preserved).
        from journal import journal
        if self is not journal._level:
            return original_set_group_tileset(self, points, tileset,
                                              chasm_type)
        try:
            pts = list(points)
        except Exception:
            return original_set_group_tileset(self, points, tileset,
                                              chasm_type)
        pre = None
        try:
            pre = [(p.x, p.y) + _tile_flavor(self.tiles[p.x][p.y])
                   for p in pts]
        except Exception:
            pre = None
        try:
            return original_set_group_tileset(self, pts, tileset, chasm_type)
        finally:
            if pre is not None:
                _record_flavor_method_changes(self, pre,
                                              'set_group_tileset')

    buff_cls.apply = patched_buff_apply
    buff_cls.unapply = patched_buff_unapply
    unit_cls.equip = patched_unit_equip
    unit_cls.unequip = patched_unit_unequip
    level_cls.remove_obj = patched_remove_obj
    if original_add_obj is not None:
        level_cls.add_obj = patched_add_obj
    if original_set_tileset is not None:
        level_cls.set_tileset = patched_set_tileset
    if original_set_group_tileset is not None:
        level_cls.set_group_tileset = patched_set_group_tileset

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

# Unit 2: the Root-2 cause-marker kinds are sweep boundaries too — deltas
# inside a marker window attribute to the marker (RiftResidue's bonus
# write lands under its component_effect instead of unattributed drift).
# They are NOT action kinds: a marker is a transient window inside the
# turn's flow, so it must never clear the suspended cast window the way
# a new tick/cast (a definitive turn action) does.
_MARKER_KINDS = frozenset((
    'item_pickup', 'equipment_trigger', 'craft', 'component_effect',
    # Unit 3: the reactive-proc marker (leg 4) — a lazily-materialized
    # window around a reacting buff's handler; sweeps like the other
    # markers, never clears the suspended cast window.
    'reactive_proc',
))

_SWEEP_KINDS = _ACTION_KINDS | _MARKER_KINDS


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
                    and record.get('event_type') in _SWEEP_KINDS):
                # The sweep first (parent resolution may claim the suspended
                # window's span); then a new ACTION (never a mere marker)
                # definitively interrupts any suspended cast window.
                sweep(journal._level, site='span:push')
                if record.get('event_type') in _ACTION_KINDS:
                    _pending_ctx = None
        except Exception as e:
            _note_failure('span:push', e)
        return original_push(record)

    def patched_pop():
        try:
            stack = journal.cause_stack
            if stack:
                kind = stack[-1].get('event_type')
                if kind in _SWEEP_KINDS:
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
