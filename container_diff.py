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
