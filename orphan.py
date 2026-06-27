"""
Orphan-window composer — composes the ambient/enemy-turn body of each
turn's utterance.

Two internal sections (no labels in output; line-form recognition does
the work):

1. Non-player actions — cast_begin chain roots with non-player casters.
   Includes melee (spell.melee=True branches the verb to "hit"). Enemies
   first, allies second within the section.
2. Status ticks — buff_tick chain roots producing DOT damage on non-wizard
   targets; EventOnBuffRemove fade events on non-wizard targets; EventOnUnfrozen.

Equipment passives previously rendered as section 1 here; they moved to
their own producer (equipment.py) so the gear narrative renders between
the digest's keypress narrative and orphan's ambient body. See
`design_orphan_composer_phrasing.md` for the architectural context.

Each line attributes its source by player-facing name so the listener
identifies the section by sentence shape, not by a header.

Mark precedence: crisis claims first, digest claims player-keypress
chains, equipment claims equipment_tick chains, orphan claims everything
else last. This producer respects all prior marks and stamps `orphan_v1`
on records it renders.
"""

from helpers import _pluralize


ORPHAN_MARK = "orphan_v1"
PRIORITY_STANDARD_ORPHAN = 200


def _has_mark(record, mark):
    return mark in (record.get('marks') or [])


def _is_claimed_by_other(record):
    """True if a higher-precedence producer (crisis, digest, equipment)
    has claimed this record. Orphan respects all three."""
    marks = record.get('marks') or []
    return (
        'crisis_v1' in marks
        or 'digest_v1' in marks
        or 'equipment_v1' in marks
    )


def _claim(record):
    marks = record.setdefault('marks', [])
    if ORPHAN_MARK not in marks:
        marks.append(ORPHAN_MARK)


def _is_wizard_snap(snap):
    return bool(snap and snap.get('is_player_controlled'))


# ----------------------------------------------------------------------
# Generic chain-gather — finds all records whose lineage roots in a
# given seed record. Same shape as digest.gather_chain_events but
# parameterized on the root predicate (digest uses "is player keypress
# cast_begin"; orphan uses different predicates per section).
# ----------------------------------------------------------------------


def _build_index(records):
    return {r['sequence']: r for r in records if 'sequence' in r}


def _walk_to_root(record, idx):
    """Walk parent links upward. Return the root record (parent=None)."""
    cur = record
    seen = set()
    while cur is not None:
        seq = cur.get('sequence')
        if seq is None or seq in seen:
            return None
        seen.add(seq)
        parent_seq = cur.get('parent')
        if parent_seq is None:
            return cur
        cur = idx.get(parent_seq)
    return None


def _gather_chain(records, root, idx):
    """Collect every record whose lineage roots in `root`, in sequence order."""
    if root is None:
        return []
    root_seq = root.get('sequence')
    if root_seq is None:
        return []
    chain = []
    for rec in records:
        walked = _walk_to_root(rec, idx)
        if walked is not None and walked.get('sequence') == root_seq:
            chain.append(rec)
    return chain


# ----------------------------------------------------------------------
# Coord formatter — reads cfg.show_coordinates lazily so the producer
# stays config-aware without taking a hard dependency at import time.
# ----------------------------------------------------------------------


def _coord_str(unit_snap, show_coords):
    """Format a coord suffix '(x,y)' when show_coords is True; empty
    string otherwise (coords-off rendering would use directional form
    via helpers, but that's parked per design_orphan_composer_phrasing.md
    until coords-off is more actively exercised)."""
    if not show_coords:
        return ""
    x = unit_snap.get('x') if unit_snap else None
    y = unit_snap.get('y') if unit_snap else None
    if x is None or y is None:
        return ""
    return f" ({x},{y})"


def _team_prefix(unit_snap, wizard_team):
    """Return 'Ally ' for player-team units, '' for everything else.
    Per `feedback_ally_designation_mandatory.md` allies always get the
    prefix; enemies use no prefix (default)."""
    if not unit_snap:
        return ""
    team = unit_snap.get('team')
    if wizard_team is not None and team == wizard_team:
        # Avoid double-prefixing the wizard itself; the wizard's own snapshot
        # has is_player_controlled=True, and crisis/orphan never apply
        # 'Ally' to the wizard.
        if unit_snap.get('is_player_controlled'):
            return ""
        return "Ally "
    return ""


def _name_with_coord(unit_snap, wizard_team, show_coords):
    """Render a unit reference as '[Ally ]Name (x,y)' for use in line text."""
    if not unit_snap:
        return "Unknown"
    name = unit_snap.get('name') or 'Unknown'
    return f"{_team_prefix(unit_snap, wizard_team)}{name}{_coord_str(unit_snap, show_coords)}"


def _find_wizard_team(records):
    """Walk records looking for a player-controlled snapshot to discover
    the wizard's team value. Used for ally classification in line
    rendering. None if no player-controlled unit appears in the records."""
    for r in records:
        payload = r.get('payload') or {}
        for key in ('caster', 'target', 'unit', 'user', 'owner'):
            snap = payload.get(key)
            if snap and snap.get('is_player_controlled'):
                team = snap.get('team')
                if team is not None:
                    return team
    return None


# ----------------------------------------------------------------------
# Equipment-passives section moved to equipment.py — its own producer
# now handles equipment_tick chains at priority 150 (between digest and
# this orphan composer). The shared helpers above (_build_index,
# _gather_chain, _name_with_coord, _team_prefix, etc.) are imported by
# equipment.py.
# ----------------------------------------------------------------------


def _coord_list(members, show_coords):
    """Format the trailing coord list for multi-target lines, respecting
    show_coords. Returns ' at (x1,y1), (x2,y2), ...' or '' if coords off."""
    if not show_coords:
        return ""
    coords = [(m.get('x'), m.get('y')) for m in members
              if m.get('x') is not None and m.get('y') is not None]
    if not coords:
        return ""
    return " at " + ", ".join(f"({x},{y})" for x, y in coords)


# ======================================================================
# Section 2: Non-player actions (enemy + ally casts and melee)
# ======================================================================
#
# Walks for cast_begin chain roots with non-player casters. Each chain
# is one cast event (single target or multi-target AoE). Cross-chain
# collapse only for single-target chains (per the agreed asymmetry rule).
#
# Render: caster-leading. spell.melee branches the verb to "hit" (no
# spell name in the line); cast renders "X cast Spell at Y, N Dtype."
# Sub-ordering: enemies first, allies second.
# ======================================================================


def _is_nonplayer_cast_root(record):
    """True if record is a cast_begin with a non-player caster and
    parent=None (chain root, not a proc)."""
    if record.get('event_type') != 'cast_begin':
        return False
    if record.get('parent') is not None:
        return False
    payload = record.get('payload') or {}
    if payload.get('is_player'):
        return False
    return True


def _build_action_signature(chain):
    """Build an equivalence-class signature for a non-player action chain.

    Three signature shapes:
    - Single-target damage: (caster_name, caster_tier, spell_name, melee,
        'damage', target_id, dtype, damage_post_resist).
    - Movement (no damage, caster moved): (caster_name, caster_tier,
        spell_name, melee, 'movement', None, None, None). Cross-chain
        collapse groups same-spell same-caster-type movements; the
        renderer preserves each caster's start/end coords as from→to
        pairs.
    - Multi-target damage / mixed / unrecognized: returns None to skip
        cross-chain collapse (each chain renders as its own line).
    """
    if not chain:
        return None
    root = chain[0]
    payload = root.get('payload') or {}
    caster = payload.get('caster') or {}
    spell = payload.get('spell') or {}
    spell_name = spell.get('name')
    melee = bool(spell.get('melee'))
    caster_tier = caster.get('tier', 'minion')
    caster_name = caster.get('name')
    caster_id = caster.get('id')

    damage_events = [
        r for r in chain if r.get('event_type') == 'EventOnDamaged'
    ]
    moved_events = [
        r for r in chain if r.get('event_type') == 'EventOnMoved'
    ]

    # Movement-only chain: cast that moved the caster but did no damage.
    # Same-spell same-caster-type movements collapse; per-caster destinations
    # surface in the rendered line.
    caster_moved = any(
        ((m.get('payload') or {}).get('unit') or {}).get('id') == caster_id
        for m in moved_events
    )
    if not damage_events and caster_moved:
        return (caster_name, caster_tier, spell_name, melee,
                'movement', None, None, None)

    if len(damage_events) != 1:
        # Zero or multi-target damage: skip cross-chain collapse.
        return None

    dpayload = damage_events[0].get('payload') or {}
    target = dpayload.get('target') or {}
    target_id = target.get('id')
    dtype = dpayload.get('damage_type')
    damage = dpayload.get('damage')

    return (caster_name, caster_tier, spell_name, melee,
            'damage', target_id, dtype, damage)


def _render_action_chain(chain, wizard_team, show_coords, movement_verbose):
    """Render a single non-player action chain as one line.
    Picks the verb and target form based on the chain's content."""
    if not chain:
        return None
    root = chain[0]
    payload = root.get('payload') or {}
    caster = payload.get('caster') or {}
    spell = payload.get('spell') or {}
    spell_name = spell.get('name') or 'attack'
    melee = bool(spell.get('melee'))
    caster_id = caster.get('id')

    # Find damage events; group by target for multi-target rendering.
    damage_events = [
        r for r in chain if r.get('event_type') == 'EventOnDamaged'
    ]

    # Movement-via-cast: chain has EventOnMoved on the caster but no
    # damage (Frog Hop, Dash, Blink-style enemy spells). Verbose flag
    # controls whether the caster's pre-move starting position is
    # included; destination is preserved either way.
    if not damage_events:
        moved_events = [
            r for r in chain if r.get('event_type') == 'EventOnMoved'
        ]
        for moved in moved_events:
            unit_snap = (moved.get('payload') or {}).get('unit') or {}
            if unit_snap.get('id') != caster_id:
                continue
            dest_x = unit_snap.get('x')
            dest_y = unit_snap.get('y')
            if dest_x is None or dest_y is None:
                break
            if movement_verbose:
                # Verbose: include caster start coord.
                caster_str = _name_with_coord(caster, wizard_team, show_coords)
                if show_coords:
                    return f"{caster_str} cast {spell_name}, moved to ({dest_x},{dest_y})."
                return f"{caster_str} cast {spell_name}, moved."
            # Compact: drop caster start coord; keep destination.
            name = caster.get('name') or 'Unknown'
            prefix = _team_prefix(caster, wizard_team)
            if show_coords:
                return f"{prefix}{name} cast {spell_name}, moved to ({dest_x},{dest_y})."
            return f"{prefix}{name} cast {spell_name}, moved."
        # No caster movement found — fall through to plain cast line.
        caster_str = _name_with_coord(caster, wizard_team, show_coords)
        if melee:
            return f"{caster_str} attacked."
        return f"{caster_str} cast {spell_name}."

    # Damage chain: caster always shown with coord (existing behavior).
    caster_str = _name_with_coord(caster, wizard_team, show_coords)

    if len(damage_events) == 1:
        dpayload = damage_events[0].get('payload') or {}
        target = dpayload.get('target') or {}
        target_str = _name_with_coord(target, wizard_team, show_coords)
        damage = dpayload.get('damage', 0)
        dtype = dpayload.get('damage_type')
        dtype_str = f" {dtype}" if dtype else ""
        if melee:
            return f"{caster_str} hit {target_str}, {damage}{dtype_str}."
        return f"{caster_str} cast {spell_name} at {target_str}, {damage}{dtype_str}."

    # Multi-target AoE chain. Group by (target_name, dtype, damage).
    groups = {}
    order = []
    for d in damage_events:
        dpayload = d.get('payload') or {}
        target = dpayload.get('target') or {}
        key = (target.get('name'), target.get('tier'),
               dpayload.get('damage_type'), dpayload.get('damage'))
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(target)

    parts = []
    for key in order:
        target_name, _tier, dtype, damage = key
        members = groups[key]
        dtype_str = f" {dtype}" if dtype else ""
        if len(members) == 1:
            target_str = _name_with_coord(members[0], wizard_team, show_coords)
            parts.append(f"{target_str}, {damage}{dtype_str}")
        else:
            plural = _pluralize(target_name or 'enemy')
            prefix = _team_prefix(members[0], wizard_team)
            coords = _coord_list(members, show_coords)
            parts.append(
                f"{len(members)} {prefix}{plural}{coords}, {damage}{dtype_str}"
            )
    targets_clause = "; ".join(parts)
    if melee:
        return f"{caster_str} hit {targets_clause}."
    return f"{caster_str} cast {spell_name} at {targets_clause}."


def _render_action_section(records, idx, wizard_team, show_coords,
                            movement_verbose):
    """Render the non-player actions section. Returns (lines, claimed_records).

    Walks for non-player cast_begin roots. Single-target chains collapse
    across actors via signature; multi-target chains stay separate.
    Within the section, enemies first, allies second."""
    roots = [r for r in records if _is_nonplayer_cast_root(r)
             and not _is_claimed_by_other(r) and not _has_mark(r, ORPHAN_MARK)]
    if not roots:
        return [], []

    chains_by_sig = {}
    chain_order = []
    standalone_chains = []
    claimed = []

    for root in roots:
        chain = _gather_chain(records, root, idx)
        sig = _build_action_signature(chain)
        if sig is None:
            standalone_chains.append((root, chain))
        else:
            if sig not in chains_by_sig:
                chain_order.append(sig)
                chains_by_sig[sig] = []
            chains_by_sig[sig].append((root, chain))

    def _is_ally(root):
        payload = root.get('payload') or {}
        caster = payload.get('caster') or {}
        team = caster.get('team')
        return wizard_team is not None and team == wizard_team

    enemy_lines = []
    ally_lines = []

    for sig in chain_order:
        items = chains_by_sig[sig]
        first_root, first_chain = items[0]
        is_ally = _is_ally(first_root)
        if len(items) == 1:
            line = _render_action_chain(first_chain, wizard_team,
                                         show_coords, movement_verbose)
            if line:
                (ally_lines if is_ally else enemy_lines).append(line)
        else:
            line = _render_collapsed_action(items, wizard_team,
                                             show_coords, movement_verbose)
            if line:
                (ally_lines if is_ally else enemy_lines).append(line)
        for root, chain in items:
            for rec in chain:
                _claim(rec)
                claimed.append(rec)

    for root, chain in standalone_chains:
        is_ally = _is_ally(root)
        line = _render_action_chain(chain, wizard_team, show_coords,
                                     movement_verbose)
        if line:
            (ally_lines if is_ally else enemy_lines).append(line)
        for rec in chain:
            _claim(rec)
            claimed.append(rec)

    return enemy_lines + ally_lines, claimed


def _render_collapsed_action(items, wizard_team, show_coords, movement_verbose):
    """Render a collapsed group of N identical-signature chains as one
    line. Dispatches on signature kind (damage vs movement).

    Damage form: '3 Aelves at (3,4),(4,4),(5,5) cast Lightning Bolt at
    Wizard, 6 Lightning each.'

    Movement form depends on movement_verbose:
    - Verbose ON:  '5 Horned Toads cast Frog Hop: (21,23) to (22,22),
                    (17,10) to (20,9), ...' (full from→to pairs, max
                    spatial detail).
    - Verbose OFF: '5 Horned Toads cast Frog Hop, moved.' (compact
                    noise-reduced form, default).
    """
    if not items:
        return None
    first_root, first_chain = items[0]
    payload = first_root.get('payload') or {}
    caster = payload.get('caster') or {}
    spell = payload.get('spell') or {}
    spell_name = spell.get('name') or 'attack'
    melee = bool(spell.get('melee'))
    caster_name = caster.get('name') or 'Unknown'
    casters = [item[0].get('payload', {}).get('caster', {}) for item in items]
    plural = _pluralize(caster_name)
    prefix = _team_prefix(casters[0], wizard_team)

    # Movement collapsed form. Verbose flag dispatches between full
    # from→to pairs and compact "moved" form.
    damage_events = [
        r for r in first_chain if r.get('event_type') == 'EventOnDamaged'
    ]
    if not damage_events:
        if not movement_verbose:
            # Compact form: no per-caster destinations.
            return f"{len(items)} {prefix}{plural} cast {spell_name}, moved."
        # Verbose form: list all from→to pairs.
        pairs = []
        for root_rec, chain in items:
            cpayload = root_rec.get('payload') or {}
            ccaster = cpayload.get('caster') or {}
            cid = ccaster.get('id')
            sx, sy = ccaster.get('x'), ccaster.get('y')
            dest_x, dest_y = None, None
            for moved in chain:
                if moved.get('event_type') != 'EventOnMoved':
                    continue
                unit_snap = (moved.get('payload') or {}).get('unit') or {}
                if unit_snap.get('id') == cid:
                    dest_x = unit_snap.get('x')
                    dest_y = unit_snap.get('y')
                    break
            if (sx is None or sy is None or dest_x is None or dest_y is None):
                continue
            if show_coords:
                pairs.append(f"({sx},{sy}) to ({dest_x},{dest_y})")
            else:
                pairs.append("moved")
        if not pairs:
            return None
        casters_str = f"{len(items)} {prefix}{plural}"
        if show_coords:
            return f"{casters_str} cast {spell_name}: " + ", ".join(pairs) + "."
        return f"{casters_str} cast {spell_name}, moved."

    # Damage collapsed form (existing behavior).
    dpayload = damage_events[0].get('payload') or {}
    target = dpayload.get('target') or {}
    damage = dpayload.get('damage', 0)
    dtype = dpayload.get('damage_type')
    dtype_str = f" {dtype}" if dtype else ""

    coords = _coord_list(casters, show_coords)
    casters_str = f"{len(items)} {prefix}{plural}{coords}"

    target_str = _name_with_coord(target, wizard_team, show_coords)
    if melee:
        return f"{casters_str} hit {target_str}, {damage}{dtype_str} each."
    return (
        f"{casters_str} cast {spell_name} at {target_str},"
        f" {damage}{dtype_str} each."
    )


# ======================================================================
# Section 3: Status ticks (DOTs, fades, unfreeze)
# ======================================================================


def _render_status_ticks(records, idx, wizard_team, show_coords):
    """Compose status tick lines: DOT damage, buff fades, unfreeze events,
    all on non-wizard targets. Returns (lines, claimed_records)."""
    lines = []
    claimed = []

    # DOT ticks: walk for buff_tick chain roots. Each chain may contain
    # damage events whose source is the buff itself.
    buff_tick_roots = [
        r for r in records
        if r.get('event_type') == 'buff_tick'
        and not _is_claimed_by_other(r)
        and not _has_mark(r, ORPHAN_MARK)
    ]

    # Aggregate DOT damage in two passes. A single target can carry several
    # stacks of the same DOT (STACK_INTENSITY — e.g. Bleed), each ticking its
    # own EventOnDamaged in one turn. Pass 1 sums damage per target (and keeps
    # the longest remaining duration) so a 3-stack Bleed reads "9 Physical",
    # not three "3 Physical" lines or a phantom "3 targets". Pass 2 groups
    # targets by their per-target total for the "N enemies ... each" collapse.
    per_target = {}   # (buff_name, target_id, dtype) -> accumulator
    pt_order = []

    for root in buff_tick_roots:
        chain = _gather_chain(records, root, idx)
        for rec in chain:
            if rec.get('event_type') != 'EventOnDamaged':
                continue
            payload = rec.get('payload') or {}
            target = payload.get('target') or {}
            if _is_wizard_snap(target):
                # Crisis claims wizard damage; orphan skips.
                continue
            buff_name = payload.get('source_name')
            if not buff_name:
                continue
            damage = payload.get('damage') or 0
            dtype = payload.get('damage_type')
            turns = payload.get('source_turns_left') or 0
            key = (buff_name, target.get('id'), dtype)
            acc = per_target.get(key)
            if acc is None:
                pt_order.append(key)
                per_target[key] = {
                    'target': target, 'buff_name': buff_name,
                    'dtype': dtype, 'damage': damage, 'turns': turns,
                }
            else:
                acc['damage'] += damage
                if turns > acc['turns']:
                    acc['turns'] = turns
        for rec in chain:
            _claim(rec)
            claimed.append(rec)

    dot_groups = {}
    dot_order = []
    for key in pt_order:
        acc = per_target[key]
        t = acc['target']
        sig = (acc['buff_name'], t.get('name'), t.get('tier'),
               acc['dtype'], acc['damage'], acc['turns'])
        if sig not in dot_groups:
            dot_order.append(sig)
            dot_groups[sig] = []
        dot_groups[sig].append(t)

    for sig in dot_order:
        buff_name, target_name, _tier, dtype, damage, turns = sig
        members = dot_groups[sig]
        dtype_str = f" {dtype}" if dtype else ""
        turns_str = f", {turns} turns left" if turns and turns > 0 else ""
        if len(members) == 1:
            target_str = _name_with_coord(members[0], wizard_team, show_coords)
            lines.append(
                f"{target_str} {buff_name}: {damage}{dtype_str}{turns_str}."
            )
        else:
            plural = _pluralize(target_name or 'enemy')
            prefix = _team_prefix(members[0], wizard_team)
            coords = _coord_list(members, show_coords)
            lines.append(
                f"{len(members)} {prefix}{plural}{coords} {buff_name}:"
                f" {damage}{dtype_str} each{turns_str}."
            )

    # Buff fades on non-wizard targets — natural duration expiry.
    fade_roots = [
        r for r in records
        if r.get('event_type') == 'EventOnBuffRemove'
        and not _is_claimed_by_other(r)
        and not _has_mark(r, ORPHAN_MARK)
    ]
    fade_groups = {}
    fade_order = []
    for rec in fade_roots:
        payload = rec.get('payload') or {}
        target = payload.get('target') or {}
        if _is_wizard_snap(target):
            continue
        if payload.get('is_unit_removed'):
            # Buff cleanup on death — not a "fade" worth narrating.
            _claim(rec)
            claimed.append(rec)
            continue
        buff = payload.get('buff') or {}
        bname = buff.get('name')
        if not bname:
            _claim(rec)
            claimed.append(rec)
            continue
        sig = (bname, target.get('name'), target.get('tier'))
        if sig not in fade_groups:
            fade_order.append(sig)
            fade_groups[sig] = []
        fade_groups[sig].append(target)
        _claim(rec)
        claimed.append(rec)

    for sig in fade_order:
        bname, target_name, _tier = sig
        members = fade_groups[sig]
        if len(members) == 1:
            target_str = _name_with_coord(members[0], wizard_team, show_coords)
            lines.append(f"{target_str} {bname} faded.")
        else:
            plural = _pluralize(target_name or 'enemy')
            prefix = _team_prefix(members[0], wizard_team)
            coords = _coord_list(members, show_coords)
            lines.append(
                f"{len(members)} {prefix}{plural}{coords} {bname} faded."
            )

    # Unfreeze events on non-wizard targets.
    unfreeze_records = [
        r for r in records
        if r.get('event_type') == 'EventOnUnfrozen'
        and not _is_claimed_by_other(r)
        and not _has_mark(r, ORPHAN_MARK)
    ]
    for rec in unfreeze_records:
        payload = rec.get('payload') or {}
        target = payload.get('target') or {}
        if _is_wizard_snap(target):
            continue
        target_str = _name_with_coord(target, wizard_team, show_coords)
        lines.append(f"{target_str} Frozen broke.")
        _claim(rec)
        claimed.append(rec)

    return lines, claimed


# ======================================================================
# Producer
# ======================================================================


class _OrphanProducer:
    """Stateful across calls: tracks the highest journal sequence
    processed. Fires once per turn boundary. Returns a tagged section
    for the unified emitter."""

    def __init__(self):
        self._last_processed_seq = -1

    def fire(self, journal_records, show_coords, movement_verbose,
              log_fn, telemetry=None):
        """Compose the orphan section for this turn boundary.

        Args:
            journal_records: list of journal record dicts (typically
                journal.records).
            show_coords: bool — config.show_coordinates value.
            movement_verbose: bool — config.movement_verbose value.
                When False (default), movement-via-cast chains render
                compactly (caster name + spell + destination only).
                When True, full from→to pairs preserved.
            log_fn: callable(str) for diagnostic logging.
            telemetry: optional telemetry module reference.

        Returns:
            (priority, text) tuple. text is empty if no orphan content.
        """
        if not journal_records:
            return (PRIORITY_STANDARD_ORPHAN, "")

        new_records = [
            r for r in journal_records
            if r.get('sequence') is not None
            and r.get('sequence') > self._last_processed_seq
        ]

        max_seq = max((r.get('sequence', -1) for r in journal_records), default=-1)
        self._last_processed_seq = max(self._last_processed_seq, max_seq)

        if not new_records:
            return (PRIORITY_STANDARD_ORPHAN, "")

        idx = _build_index(journal_records)
        wizard_team = _find_wizard_team(journal_records)

        action_lines, action_claimed = _render_action_section(
            new_records, idx, wizard_team, show_coords, movement_verbose
        )
        tick_lines, tick_claimed = _render_status_ticks(
            new_records, idx, wizard_team, show_coords
        )

        all_lines = action_lines + tick_lines
        text = " ".join(all_lines).strip()

        if telemetry is not None:
            try:
                telemetry.emit(
                    'orphan_emit',
                    action_lines=len(action_lines),
                    status_tick_lines=len(tick_lines),
                    output=text,
                    empty=not bool(text),
                )
            except Exception:
                pass

        if text:
            log_fn(f"[Orphan] composed: {text}")

        return (PRIORITY_STANDARD_ORPHAN, text)


# Module-level singleton — there is exactly one orphan producer per session.
producer = _OrphanProducer()
