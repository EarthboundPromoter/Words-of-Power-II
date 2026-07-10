"""
Equipment producer — composes the player-passives narrative from
equipment_tick chains.

Walks for equipment_tick chain roots (synthesized by
journal.patched_buff_advance when a Buff with buff_type=BUFF_TYPE_ITEM
ticks). Each chain represents one equipment item firing its on_advance
during the wizard's post-action buff advance (Unit.advance_buffs,
Level.py:3437 — after the cast and its spells have fully resolved).

Sits between the digest (player keypress narrative) and orphan (ambient
content) in the pipeline so the listener hears:

    critical → keypress narrative → gear narrative → ambient

Sub-cast equipment chains (Explosive Spore Manual → Combust Poison)
render in digest-style multi-section form. Direct-effect equipment
(Stone Mask, DamageAura) renders in flat per-effect form. See
`design_orphan_composer_phrasing.md` for the architectural context.

Mark precedence: respects crisis_v1 and digest_v1; stamps equipment_v1.
Orphan respects equipment_v1 in turn so the same record never renders
twice across the pipeline.

Why this is a separate producer rather than a section inside orphan:
equipment ticks are player-aligned (the wizard equipped them) and fire
AFTER the keypress chain resolves (Unit.advance_buffs, Level.py:3437),
but as their own chain roots — not descendants of the cast — so they
don't fit inside the digest's chain composition. Giving them their own
producer with priority 150 puts the gear narrative right after the
player's chosen action and before enemy turn content, which matches
the engine's actual resolution order: cast, then gear ticks, then the
enemy phase. (An earlier version of this docstring claimed ticks fire
BEFORE the keypress chain — wrong, corrected 2026-07-05; taxonomy A8.)
"""

from helpers import _pluralize, classify_resist_outcome, dedupe_unit_members
from orphan import (
    _build_index,
    _coord_list,
    _find_wizard_team,
    _gather_chain,
    _is_wizard_snap,
    _name_with_coord,
    _team_prefix,
)


EQUIPMENT_MARK = "equipment_v1"
PRIORITY_PLAYER_PASSIVES = 150


def _has_mark(record, mark):
    return mark in (record.get('marks') or [])


def _is_claimed_by_other(record):
    """True if a higher-precedence producer (crisis or digest) has
    claimed this record. Equipment respects both."""
    marks = record.get('marks') or []
    return 'crisis_v1' in marks or 'digest_v1' in marks


def _claim(record):
    marks = record.setdefault('marks', [])
    if EQUIPMENT_MARK not in marks:
        marks.append(EQUIPMENT_MARK)
        # Slice 0: feed the pipeline's double-claim watchdog.
        from journal import journal as _j
        _j.note_producer_mark(record)


# ======================================================================
# Equipment chain rendering
# ======================================================================


def _render_equipment_chain(chain, wizard_team, show_coords):
    """Dispatch equipment-chain rendering based on whether the equipment
    fired a sub-cast (Explosive Spore Manual → Combust Poison) or hit
    directly (Stone Mask, DamageAura).

    Sub-cast equipment uses the digest-style multi-section form: count-
    led headers parallel to player chains so the listener gets a
    structured breakdown. Direct-effect equipment keeps the flat per-
    target form since its effects are typically simpler (one target,
    one effect-kind per tick).

    Returns a list of strings — one composed multi-section line for
    sub-cast paths, multiple flat lines for direct paths. Empty list if
    the equipment produced no effects worth rendering."""
    if not chain:
        return []
    root = chain[0]
    if root.get('event_type') != 'equipment_tick':
        return []
    root_payload = root.get('payload') or {}
    buff_snap = root_payload.get('buff') or {}
    equipment_name = buff_snap.get('name') or 'Equipment'

    sub_cast_spell = None
    for rec in chain:
        if rec.get('event_type') == 'cast_begin':
            spell = (rec.get('payload') or {}).get('spell') or {}
            sub_cast_spell = spell.get('name')
            break

    if sub_cast_spell:
        lines = _render_equipment_subcast_chain(
            equipment_name, sub_cast_spell, chain, wizard_team, show_coords,
        )
    else:
        lines = _render_equipment_direct_chain(
            equipment_name, chain, wizard_team, show_coords,
        )

    # claim==render: only claim the chain when we actually rendered a line.
    # A chain that produced nothing (a no-op tick, or one whose only effects
    # were claimed by crisis) is left UNclaimed so orphan's standalone-death /
    # spawn passes can still surface any non-wizard death/spawn inside it
    # instead of it being silently swallowed (mirrors orphan's own discipline).
    # Records a higher-precedence producer owns are never re-marked.
    if lines:
        for rec in chain:
            if not _is_claimed_by_other(rec):
                _claim(rec)

    return lines


def _render_equipment_subcast_chain(equipment_name, sub_cast_spell, chain,
                                     wizard_team, show_coords):
    """Render an equipment-fired sub-cast chain in digest-style multi-
    section form. Reuses the digest's section composers (Killed,
    Surviving, Spawned, Debuffs, Buffs, Side) since they're pure-data
    chain walkers; equipment context appears as a leading clause.

    Adds a Resisted section unique to the equipment-cast scenario for
    targets where EventOnPreDamaged fired with full resist (no
    corresponding EventOnDamaged from the game).

    Example output:
        'Explosive Spore Manual cast Combust Poison. 2 killed: 2 Orcs at
         (13,5), (14,5): Combust Poison 30 Fire. 1 resisted: Hell Hound (15,5).'

    Empty output when the equipment fired the sub-cast but produced no
    rendered effects — matches the autofire-silence convention."""
    from digest import (
        compose_buffs_applied_section,
        compose_debuffs_applied_section,
        compose_killed_section,
        compose_side_section,
        compose_spawned_section,
        compose_surviving_section,
    )

    parts = [f"{equipment_name} cast {sub_cast_spell}."]

    killed = compose_killed_section(chain)
    surviving = compose_surviving_section(chain)
    resisted = _compose_resisted_section(chain, wizard_team, show_coords)
    debuffs = compose_debuffs_applied_section(chain)
    buffs = compose_buffs_applied_section(chain)
    spawned = compose_spawned_section(chain)
    # compose_side_section renders heals/buffs ON the wizard from this
    # chain. For equipment-fired sub-casts that target the wizard
    # (HealAura tick, self-buff equipment), this surfaces them under the
    # familiar "Side. Heals: ..." form. Equipment-applied debuffs on
    # wizard are crisis-claimed and not in this chain by mark precedence.
    side = compose_side_section(chain)

    if killed:
        parts.append(killed)
    if surviving:
        parts.append(surviving)
    if resisted:
        parts.append(resisted)
    if debuffs:
        parts.append(debuffs)
    if buffs:
        parts.append(buffs)
    if spawned:
        parts.append(spawned)
    if side:
        parts.append(side)

    # Empty firing: equipment cast a spell but nothing rendered. Per the
    # autofire-silence convention, silence the line.
    if len(parts) == 1:
        return []

    return [" ".join(parts)]


def _compose_resisted_section(chain, wizard_team, show_coords):
    """Compose the Resisted/Immune sections: targets with EventOnPreDamaged
    where no corresponding EventOnDamaged fired (game's deal_damage
    returns 0 before raising EventOnDamaged for fully-blocked hits).

    Splits by `classify_resist_outcome` into 'immune' (resist >= 100)
    and 'resisted' (resist < 100 but damage rounded to 0). The
    distinction matters: immune targets cannot be damaged by this type
    at all; resisted targets took 0 from this particular roll but
    aren't structurally invulnerable.

    Returns the combined section string ('N immune: ... N resisted: ...'
    or empty), with single-target outcomes rendered as bare lines per
    the digest's drop-header-on-singular convention."""
    damaged_ids = set()
    for rec in chain:
        if rec.get('event_type') != 'EventOnDamaged':
            continue
        tid = (rec.get('payload') or {}).get('target', {}).get('id')
        if tid is not None:
            damaged_ids.add(tid)

    immune = []
    resisted = []
    seen_ids = set()
    for rec in chain:
        if rec.get('event_type') != 'EventOnPreDamaged':
            continue
        payload = rec.get('payload') or {}
        target = payload.get('target') or {}
        if _is_wizard_snap(target):
            continue
        tid = target.get('id')
        if tid is None or tid in seen_ids:
            continue
        if tid in damaged_ids:
            continue
        outcome = classify_resist_outcome(
            payload.get('damage_pre_resist'),
            payload.get('damage_post_resist'),
            payload.get('target_resist_pct'),
        )
        if outcome == 'immune':
            seen_ids.add(tid)
            immune.append(target)
        elif outcome == 'resisted':
            seen_ids.add(tid)
            resisted.append(target)

    if not immune and not resisted:
        return ""

    parts = []
    if immune:
        parts.append(_format_resist_outcome_section(
            immune, 'immune', wizard_team, show_coords))
    if resisted:
        parts.append(_format_resist_outcome_section(
            resisted, 'resisted', wizard_team, show_coords))
    return " ".join(parts)


def _format_resist_outcome_section(targets, label, wizard_team, show_coords):
    """Render an immune-or-resisted outcome group. Tier-1 individuated;
    tier-2 grouped by target name. Single application drops the count
    header. Multi uses 'N {label}: ...'."""
    classes = []
    by_name = {}
    for target in targets:
        tier = target.get('tier', 'minion')
        if tier in ('boss', 'spawner', 'wizard'):
            classes.append({'target': target, 'count': 1, 'members': [target]})
        else:
            key = target.get('name')
            existing = by_name.get(key)
            if existing is None:
                cls = {'target': target, 'count': 1, 'members': [target]}
                classes.append(cls)
                by_name[key] = cls
            else:
                existing['count'] += 1
                existing['members'].append(target)

    total = sum(cls['count'] for cls in classes)
    lines = []
    for cls in classes:
        if cls['count'] == 1:
            target_str = _name_with_coord(cls['target'], wizard_team, show_coords)
            lines.append(f"{target_str}.")
        else:
            plural = _pluralize(cls['target'].get('name') or 'enemy')
            prefix = _team_prefix(cls['members'][0], wizard_team)
            coords = _coord_list(cls['members'], show_coords)
            lines.append(f"{cls['count']} {prefix}{plural}{coords}.")

    if total == 1:
        # Bare line with the outcome word: "Hell Hound (15,5) immune."
        bare = lines[0].rstrip('.')
        return f"{bare} {label}."
    return f"{total} {label}: " + " ".join(lines)


def _render_equipment_direct_chain(equipment_name, chain,
                                    wizard_team, show_coords):
    """Render a non-sub-cast equipment chain in flat per-effect form.
    Equipment that hits directly via on_advance code (Stone Mask
    petrify, DamageAura damage, HealAura heal) uses this path; one line
    per effect-kind / per target-class."""
    sub_cast_spell = None  # explicit None for the format helpers' signatures

    # Targets that died in this chain — verb dispatch flips from "hit"
    # to "killed" for damage groups whose target_id is in this set.
    dead_ids = set()
    for rec in chain:
        if rec.get('event_type') != 'EventOnDeath':
            continue
        tid = (rec.get('payload') or {}).get('target', {}).get('id')
        if tid is not None:
            dead_ids.add(tid)

    lines = []
    damage_groups = {}
    damage_order = []
    heal_groups = {}
    heal_order = []
    buff_applies = []
    spawns = []
    fully_resisted = []
    seen_resist_ids = set()

    for rec in chain[1:]:
        et = rec.get('event_type')
        payload = rec.get('payload') or {}
        if et == 'EventOnDamaged':
            target = payload.get('target') or {}
            if _is_wizard_snap(target):
                continue
            tid = target.get('id')
            verb = 'killed' if tid in dead_ids else 'hit'
            key = (
                target.get('name'), target.get('tier'),
                payload.get('damage_type'), payload.get('damage'),
                verb,
            )
            if key not in damage_groups:
                damage_order.append(key)
                damage_groups[key] = []
            damage_groups[key].append(target)
        elif et == 'EventOnPreDamaged':
            target = payload.get('target') or {}
            if _is_wizard_snap(target):
                continue
            tid = target.get('id')
            if tid is None or tid in seen_resist_ids:
                continue
            outcome = classify_resist_outcome(
                payload.get('damage_pre_resist'),
                payload.get('damage_post_resist'),
                payload.get('target_resist_pct'),
            )
            if outcome in ('immune', 'resisted'):
                seen_resist_ids.add(tid)
                # Stash with outcome word so the renderer can use the
                # appropriate verb per target.
                fully_resisted.append((target, outcome))
        elif et == 'EventOnHealed':
            target = payload.get('target') or {}
            if _is_wizard_snap(target):
                lines.append(
                    f"{equipment_name} healed"
                    f" {_name_with_coord(target, wizard_team, show_coords)},"
                    f" {payload.get('heal_amount', 0)} HP."
                )
                continue
            key = (target.get('name'), target.get('tier'), payload.get('heal_amount'))
            if key not in heal_groups:
                heal_order.append(key)
                heal_groups[key] = []
            heal_groups[key].append(target)
        elif et == 'EventOnBuffApply':
            # A wizard-facing buff/debuff crisis already owns (e.g. a gear tick
            # that curses the wizard) is claimed at the child record by crisis,
            # not at the equipment_tick root — so skip it here to avoid speaking
            # it twice (crisis "Wizard cursed" + equipment "applied ...").
            if _is_claimed_by_other(rec):
                continue
            target = payload.get('target') or {}
            if _is_wizard_snap(target):
                buff = payload.get('buff') or {}
                bname = buff.get('name')
                turns = buff.get('turns_left')
                if bname:
                    if turns and turns > 0:
                        lines.append(
                            f"{equipment_name} applied {bname}"
                            f" to Wizard, {turns} turns."
                        )
                    else:
                        lines.append(
                            f"{equipment_name} applied {bname} to Wizard."
                        )
                continue
            buff_applies.append((target, payload.get('buff') or {}))
        elif et == 'EventOnUnitAdded':
            unit_snap = payload.get('unit') or {}
            if not unit_snap.get('name'):
                continue
            spawns.append(unit_snap)

    for key in damage_order:
        target_name, target_tier, dtype, damage, verb = key
        # One unit hit N times is repetition, not N units (the 2026-07-02
        # multiplicity class) — multi-hit units get their own "N hits"
        # clause; distinct units keep the collective form.
        deduped = dedupe_unit_members(damage_groups[key])
        singles = [m for m, c in deduped if c == 1]
        if singles:
            line = _format_equipment_damage_line(
                equipment_name, sub_cast_spell, verb, target_name,
                target_tier, dtype, damage, singles, wizard_team,
                show_coords,
            )
            if line:
                lines.append(line)
        dtype_str = f" {dtype}" if dtype else ""
        for m, c in deduped:
            if c == 1:
                continue
            target_str = _name_with_coord(m, wizard_team, show_coords)
            lines.append(
                f"{equipment_name} {verb} {target_str}, {c} hits,"
                f" {damage}{dtype_str} each.")

    for target, outcome in fully_resisted:
        target_str = _name_with_coord(target, wizard_team, show_coords)
        lines.append(f"{equipment_name} {target_str} {outcome}.")

    for key in heal_order:
        target_name, target_tier, heal_amount = key
        deduped = dedupe_unit_members(heal_groups[key])
        singles = [m for m, c in deduped if c == 1]
        if singles:
            line = _format_equipment_heal_line(
                equipment_name, sub_cast_spell, target_name, target_tier,
                heal_amount, singles, wizard_team, show_coords,
            )
            if line:
                lines.append(line)
        for m, c in deduped:
            if c == 1:
                continue
            target_str = _name_with_coord(m, wizard_team, show_coords)
            lines.append(
                f"{equipment_name} healed {target_str}, {c} times,"
                f" {heal_amount} HP each.")

    for target, buff in buff_applies:
        bname = buff.get('name')
        turns = buff.get('turns_left')
        if not bname:
            continue
        target_str = _name_with_coord(target, wizard_team, show_coords)
        # "applied {Name} to {target}" — grammatical for every buff name. The
        # earlier "{equipment} {name.lower()} {target}" verb form broke on
        # non-past-tense names ("Stone Mask petrify Goblin").
        if turns and turns > 0:
            lines.append(
                f"{equipment_name} applied {bname} to {target_str}, {turns} turns.")
        else:
            lines.append(f"{equipment_name} applied {bname} to {target_str}.")

    if spawns:
        lines.extend(_format_equipment_spawn_lines(
            equipment_name, sub_cast_spell, spawns, wizard_team, show_coords
        ))

    return lines


def _format_equipment_damage_line(equipment_name, sub_cast_spell, verb,
                                   target_name, target_tier,
                                   dtype, damage, members, wizard_team, show_coords):
    """Format a direct-equipment damage line. Verb is 'hit' (target
    survived) or 'killed' (target died in chain)."""
    dtype_str = f" {dtype}" if dtype else ""
    cast_clause = f" cast {sub_cast_spell}," if sub_cast_spell else ""
    if len(members) == 1:
        target_str = _name_with_coord(members[0], wizard_team, show_coords)
        return f"{equipment_name}{cast_clause} {verb} {target_str}, {damage}{dtype_str}."
    plural = _pluralize(target_name or 'enemy')
    coords = _coord_list(members, show_coords)
    return (
        f"{equipment_name}{cast_clause} {verb} {len(members)} {plural}{coords},"
        f" {damage}{dtype_str} each."
    )


def _format_equipment_heal_line(equipment_name, sub_cast_spell,
                                 target_name, target_tier,
                                 heal_amount, members, wizard_team, show_coords):
    """Format a direct-equipment heal line."""
    cast_clause = f" cast {sub_cast_spell}," if sub_cast_spell else ""
    if len(members) == 1:
        target_str = _name_with_coord(members[0], wizard_team, show_coords)
        return f"{equipment_name}{cast_clause} healed {target_str}, {heal_amount} HP."
    plural = _pluralize(target_name or 'unit')
    prefix = _team_prefix(members[0], wizard_team)
    coords = _coord_list(members, show_coords)
    return (
        f"{equipment_name}{cast_clause} healed {len(members)} {prefix}{plural}{coords},"
        f" {heal_amount} HP each."
    )


def _format_equipment_spawn_lines(equipment_name, sub_cast_spell,
                                   spawns, wizard_team, show_coords):
    """Render spawn lines for direct-equipment-spawned units."""
    cast_clause = f" cast {sub_cast_spell}," if sub_cast_spell else ""
    groups = {}
    order = []
    for unit in spawns:
        key = (unit.get('name'), unit.get('team'))
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(unit)

    lines = []
    for key in order:
        name, _team = key
        members = groups[key]
        if len(members) == 1:
            target_str = _name_with_coord(members[0], wizard_team, show_coords)
            lines.append(f"{equipment_name}{cast_clause} spawned {target_str}.")
        else:
            plural = _pluralize(name or 'unit')
            prefix = _team_prefix(members[0], wizard_team)
            coords = _coord_list(members, show_coords)
            lines.append(
                f"{equipment_name}{cast_clause} spawned {len(members)} {prefix}{plural}{coords}."
            )
    return lines


# ======================================================================
# Producer
# ======================================================================


class _EquipmentProducer:
    """Stateful across calls: tracks the highest journal sequence
    processed. Fires once per turn boundary. Returns a tagged section
    for the unified emitter."""

    def __init__(self):
        self._last_processed_seq = -1

    def fire(self, journal_records, show_coords, log_fn, telemetry=None,
             shared_index=None):
        """Compose the equipment section for this turn boundary.

        Returns:
            (priority, text) tuple. text is empty if no equipment
            content this turn.
        """
        if not journal_records:
            return (PRIORITY_PLAYER_PASSIVES, "")

        # Slice 0: tail by this producer's own sequence cursor (never a
        # list offset — build law 1); shared index replaces the per-fire
        # full-list index build and wizard-team scan. shared_index=None
        # (tests, standalone use) keeps the old local builds verbatim.
        from journal import tail_after as _tail_after
        tail = _tail_after(journal_records, self._last_processed_seq)
        new_records = [
            r for r in tail if r.get('sequence') is not None
        ]
        if tail:
            self._last_processed_seq = max(
                self._last_processed_seq, tail[-1].get('sequence', -1))

        if not new_records:
            return (PRIORITY_PLAYER_PASSIVES, "")

        if shared_index is not None:
            idx = shared_index.by_seq
            wizard_team = shared_index.wizard_team
        else:
            idx = _build_index(journal_records)
            wizard_team = _find_wizard_team(journal_records)

        roots = [
            r for r in new_records
            if r.get('event_type') == 'equipment_tick'
            and not _is_claimed_by_other(r)
            and not _has_mark(r, EQUIPMENT_MARK)
        ]

        lines = []
        for root in roots:
            # Full-history chain membership (this producer's shipped
            # scope — orphan's gathers are window-scoped, this one is
            # not; plan §2b). The children-map walk kills the
            # per-root full-list scan that made equipment the most
            # expensive producer while composing nothing.
            if shared_index is not None:
                from journal import gather_descendants as _gd
                chain = _gd(root, shared_index)
            else:
                chain = _gather_chain(journal_records, root, idx)
            new_lines = _render_equipment_chain(chain, wizard_team, show_coords)
            lines.extend(new_lines)

        text = " ".join(lines).strip()

        if telemetry is not None:
            try:
                telemetry.emit(
                    'equipment_emit',
                    chain_count=len(roots),
                    line_count=len(lines),
                    output=text,
                    empty=not bool(text),
                )
            except Exception:
                pass

        if text:
            log_fn(f"[Equipment] composed: {text}")

        return (PRIORITY_PLAYER_PASSIVES, text)


# Module-level singleton — there is exactly one equipment producer per session.
producer = _EquipmentProducer()
