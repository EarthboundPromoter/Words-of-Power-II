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

from helpers import (
    _pluralize, source_attributed_line, chebyshev_distance,
    format_spawn_locality,
)


ORPHAN_MARK = "orphan_v1"
PRIORITY_STANDARD_ORPHAN = 200

# Line-item ranks (the orphan body's sub-structure, P1/P6): enemy actions
# lead, then ally actions, then status/ambient lines (DOTs, fades, deaths,
# spawns, cloud ticks, buff-apply onsets), then bare-root procs. Within each
# rank the producer orders by proximity to the wizard.
RANK_ENEMY_ACTION = 0
RANK_ALLY_ACTION = 1
RANK_STATUS = 2
RANK_BARE = 3

# A line-item that has no usable spatial anchor sorts to the far end.
_FAR_DISTANCE = 10 ** 6
_OUT_OF_SIGHT = "Out of sight."


def _make_item(rank, anchors, text):
    """A composed orphan line plus the spatial metadata needed to order it.

    `anchors` is the list of unit snapshots the line is "about" (the caster
    for an action line, the target for a status line, all members for a
    collapsed line). The producer reads `x`/`y` and the capture-time
    `can_see_wizard` off the nearest anchor to compute the (in-sight,
    distance) sort key. `text` is the already-rendered sentence(s)."""
    return {'rank': rank, 'anchors': [a for a in anchors if a], 'text': text}


def _item_spatial(item, wx, wy):
    """Return (in_los, distance) for a line-item from its NEAREST anchor.

    Nearest = smallest Chebyshev distance to the wizard (P1: the closest
    member of a collapsed group is the most tactically relevant). in_los is
    that nearest anchor's capture-time can_see_wizard (None -> out of sight).
    No usable anchor -> (out of sight, far)."""
    best_d = None
    best_los = False
    for a in item.get('anchors') or []:
        d = chebyshev_distance(a.get('x'), a.get('y'), wx, wy)
        dk = d if d is not None else _FAR_DISTANCE
        if best_d is None or dk < best_d:
            best_d = dk
            best_los = bool(a.get('can_see_wizard'))
    if best_d is None:
        return (False, _FAR_DISTANCE)
    return (best_los, best_d)


def _assemble_items(items, wizard_pos, los_grouping):
    """Order the composed line-items and join them into the orphan body.

    With no wizard position (no spatial frame — e.g. between levels, or the
    pure-text unit tests), fall back to a stable rank-sort that reproduces
    the historical enemy->ally->status->bare order verbatim; no LoS gate.

    With a wizard position, sort by (not in_los, rank, distance) so in-sight
    leads, the sub-structure holds within each sight half, and proximity
    orders within each rank. The 'Out of sight.' gate (R2) is then placed per
    `los_grouping`: section (one global gate, default), block (per-rank gate),
    or line (per-line tag)."""
    items = [it for it in items if it.get('text')]
    if not items:
        return ""
    if wizard_pos is None:
        ordered = sorted(items, key=lambda it: it['rank'])
        return " ".join(it['text'] for it in ordered)

    wx, wy = wizard_pos
    decorated = []
    for it in items:
        in_los, dist = _item_spatial(it, wx, wy)
        decorated.append((not in_los, it['rank'], dist, it['text']))
    decorated.sort(key=lambda d: (d[0], d[1], d[2]))

    if los_grouping == 'line':
        return _assemble_line(decorated)
    if los_grouping == 'block':
        return _assemble_block(decorated)
    return _assemble_section(decorated)


def _assemble_section(decorated):
    """One global 'Out of sight.' gate between the in-sight and out-of-sight
    halves (R2 default — fewest words, best P8 early-exit)."""
    in_parts = [d[3] for d in decorated if not d[0]]
    out_parts = [d[3] for d in decorated if d[0]]
    parts = []
    if in_parts:
        parts.append(" ".join(in_parts))
    if out_parts:
        parts.append(_OUT_OF_SIGHT)
        parts.append(" ".join(out_parts))
    return " ".join(parts).strip()


def _assemble_block(decorated):
    """A gate before each rank's out-of-sight remainder, keeping each
    sub-section (enemy / ally / status / bare) self-contained."""
    by_rank = {}
    rank_order = []
    for out_flag, rank, _dist, text in decorated:
        if rank not in by_rank:
            rank_order.append(rank)
            by_rank[rank] = ([], [])
        (by_rank[rank][1] if out_flag else by_rank[rank][0]).append(text)
    rank_order.sort()
    parts = []
    for rank in rank_order:
        in_b, out_b = by_rank[rank]
        parts.extend(in_b)
        if out_b:
            parts.append(_OUT_OF_SIGHT)
            parts.extend(out_b)
    return " ".join(parts).strip()


def _assemble_line(decorated):
    """Each out-of-sight line carries its own tag (pairs with per-line
    direction once coords-off rendering lands)."""
    parts = []
    for out_flag, _rank, _dist, text in decorated:
        parts.append(f"{text} {_OUT_OF_SIGHT}" if out_flag else text)
    return " ".join(parts).strip()


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

    # target name is in the key alongside id() as insurance: CPython can reuse
    # a freed unit's id() within a turn (spawn-die-spawn), and a same-name
    # discriminator keeps a reallocated id from mis-collapsing two distinct
    # targets into one line.
    return (caster_name, caster_tier, spell_name, melee,
            'damage', target_id, target.get('name'), dtype, damage)


def _deaths_in_chain(chain):
    """Map target id -> death target snapshot for EventOnDeath records in a
    chain. A non-player death rides the line that describes its cause (R1
    death model, P12 causal fidelity): the producer capstones the damage
    clause whose target died rather than emitting a separate 'N killed' tier."""
    deaths = {}
    for r in chain:
        if r.get('event_type') != 'EventOnDeath':
            continue
        t = (r.get('payload') or {}).get('target') or {}
        tid = t.get('id')
        if tid is not None:
            deaths[tid] = t
    return deaths


def _killed_suffix(n):
    """The death capstone appended to a damage clause: ', killed' for one
    death, ', N killed' for several (the count is the tactical magnitude)."""
    if n <= 0:
        return ""
    if n == 1:
        return ", killed"
    return f", {n} killed"


def _chain_spawns(chain):
    """Spawned-unit snapshots (EventOnUnitAdded) in a chain, excluding the
    wizard and Soul Jars (the latter has its own dedicated UX path). These
    ride their cause line as a spawn capstone."""
    out = []
    for r in chain:
        if r.get('event_type') != 'EventOnUnitAdded':
            continue
        u = (r.get('payload') or {}).get('unit') or {}
        if _is_wizard_snap(u):
            continue
        if 'Soul Jar' in (u.get('name') or ''):
            continue
        out.append(u)
    return out


def _spawn_groups(units):
    """Group spawned units by (name, team), first-seen order preserved."""
    groups = {}
    order = []
    for u in units:
        key = (u.get('name'), u.get('team'))
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(u)
    return [(k[0], k[1], groups[k]) for k in order]


def _render_spawn_phrase(units, wizard_team, show_coords, wizard_pos,
                         spawn_coord_cap):
    """Render spawned units as 'N [Ally ]Type spawned {locality}' clauses,
    grouped by (name, team) and joined by '; '. Always states count + type
    (the wave size is the tactical magnitude); locality is scale-tiered via
    the shared format_spawn_locality (exact coords up to the cap, else a
    top-two-direction summary). Returns '' when there are no spawns."""
    if not units:
        return ""
    wx, wy = wizard_pos if wizard_pos else (None, None)
    parts = []
    for name, team, members in _spawn_groups(units):
        is_ally = wizard_team is not None and team == wizard_team
        prefix = "Ally " if is_ally else ""
        count = len(members)
        type_str = (name or 'unit') if count == 1 else _pluralize(name or 'unit')
        locality = format_spawn_locality(members, wx, wy, show_coords,
                                         spawn_coord_cap)
        parts.append(f"{count} {prefix}{type_str} spawned{locality}")
    return "; ".join(parts)


def _render_foreign_damage(records, wizard_team, show_coords, dead_ids=None):
    """Render damage whose source differs from the enclosing chain's caster
    (e.g. a player's reactive gear proc firing DURING an enemy cast)
    attributed to its OWN source, via the game-convention helper — instead
    of being folded into the caster's spell line. Wizard-targeted foreign
    damage belongs to crisis, so it is skipped here. A foreign hit that
    killed its target carries the death capstone."""
    dead_ids = dead_ids or set()
    lines = []
    for r in records:
        p = r.get('payload') or {}
        target = p.get('target') or {}
        if _is_wizard_snap(target):
            continue
        target_str = _name_with_coord(target, wizard_team, show_coords)
        line = source_attributed_line(
            'damage',
            amount=p.get('damage'),
            dtype=p.get('damage_type'),
            target_name=target_str,
            source_name=p.get('source_name'),
            source_owner_name=p.get('source_owner_name'),
            source_is_buff=p.get('source_is_buff'),
            source_buff_type=p.get('source_buff_type'),
        )
        if line:
            killed = ", killed" if target.get('id') in dead_ids else ""
            lines.append(line + killed + ".")
    return lines


def _render_action_chain(chain, wizard_team, show_coords, movement_verbose,
                         wizard_pos=None, spawn_coord_cap=5):
    """Render a single non-player action chain. Normally one line; if the
    chain carries embedded foreign-source damage (a reactive gear proc fired
    during this cast), those hits are attributed to their own source and
    appended as separate sentences. An on-cast summon appends a spawn
    capstone ('... 3 Ash Imps spawned at (...).')."""
    if not chain:
        return None
    root = chain[0]
    payload = root.get('payload') or {}
    caster = payload.get('caster') or {}
    spell = payload.get('spell') or {}
    spell_name = spell.get('name') or 'attack'
    melee = bool(spell.get('melee'))
    caster_id = caster.get('id')
    caster_name = caster.get('name')

    # Partition damage: the cast's OWN damage carries source_name == the
    # cast's spell (or the caster); anything else is a foreign proc that
    # must be attributed to its own source, not folded into the spell line.
    # Wizard-targeted damage belongs to crisis (claim==render) — exclude it
    # from BOTH buckets so the orphan body never re-narrates a wizard hit.
    own_damage = []
    foreign_damage = []
    wizard_damage_seen = False
    for r in chain:
        if r.get('event_type') != 'EventOnDamaged':
            continue
        p = r.get('payload') or {}
        if _is_wizard_snap(p.get('target') or {}):
            # Exclude wizard damage ONLY when crisis actually claimed it
            # (claim==render). If crisis is disabled, the hit is unclaimed and
            # orphan must still render it rather than drop it silently.
            if _is_claimed_by_other(r):
                wizard_damage_seen = True
                continue
        sname = p.get('source_name')
        if sname is None or sname == spell_name or sname == caster_name:
            own_damage.append(r)
        else:
            foreign_damage.append(r)

    # B2: a cast whose ONLY effect was wizard damage (crisis's) and that did
    # not also move the caster is fully redundant with the crisis line — drop
    # it. (A cast that repositioned the caster still renders its movement; an
    # AoE that also hit non-wizard targets renders those.)
    caster_moved = any(
        r.get('event_type') == 'EventOnMoved'
        and ((r.get('payload') or {}).get('unit') or {}).get('id') == caster_id
        for r in chain
    )
    if wizard_damage_seen and not own_damage and not foreign_damage \
            and not caster_moved:
        return None

    # Deaths in this chain ride their cause line (the own-damage clause, or a
    # foreign-proc clause). A death whose target took no rendered damage in
    # this chain (e.g. a status/transformation death triggered by the cast)
    # falls through to a short standalone "died" sentence.
    deaths = _deaths_in_chain(chain)
    dead_ids = set(deaths)

    main = _render_cast_line(chain, caster, spell_name, melee, caster_id,
                             own_damage, wizard_team, show_coords,
                             movement_verbose, dead_ids)
    foreign_lines = _render_foreign_damage(
        foreign_damage, wizard_team, show_coords, dead_ids)

    covered = set()
    for r in own_damage + foreign_damage:
        tid = ((r.get('payload') or {}).get('target') or {}).get('id')
        if tid is not None:
            covered.add(tid)
    death_lines = [
        f"{_name_with_coord(snap, wizard_team, show_coords)} died."
        for tid, snap in deaths.items()
        if tid not in covered and not _is_wizard_snap(snap)
    ]

    # On-cast summon capstone — names the wave the cast produced (closes the
    # long-standing "enemy summons unnarrated" gap). Only when the cast line
    # itself rendered, so the spawn rides a visible cause.
    spawn_lines = []
    if main:
        spawn_phrase = _render_spawn_phrase(
            _chain_spawns(chain), wizard_team, show_coords, wizard_pos,
            spawn_coord_cap)
        if spawn_phrase:
            spawn_lines.append(spawn_phrase + ".")

    parts = [p for p in ([main] + foreign_lines + death_lines + spawn_lines)
             if p]
    if not parts:
        return None
    return " ".join(parts)


def _render_cast_line(chain, caster, spell_name, melee, caster_id,
                      damage_events, wizard_team, show_coords,
                      movement_verbose, dead_ids=None):
    """The caster's own action line (verb + targets), over the chain's OWN
    damage events. A target that died carries the death capstone (', killed'
    single / ', N killed' for an AoE group). dead_ids is the set of unit ids
    that died in this chain."""
    dead_ids = dead_ids or set()
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
        killed = _killed_suffix(1 if target.get('id') in dead_ids else 0)
        if melee:
            return f"{caster_str} hit {target_str}, {damage}{dtype_str}{killed}."
        return f"{caster_str} cast {spell_name} at {target_str}, {damage}{dtype_str}{killed}."

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
        killed = _killed_suffix(
            sum(1 for m in members if m.get('id') in dead_ids))
        if len(members) == 1:
            target_str = _name_with_coord(members[0], wizard_team, show_coords)
            parts.append(f"{target_str}, {damage}{dtype_str}{killed}")
        else:
            plural = _pluralize(target_name or 'enemy')
            prefix = _team_prefix(members[0], wizard_team)
            coords = _coord_list(members, show_coords)
            parts.append(
                f"{len(members)} {prefix}{plural}{coords}, {damage}{dtype_str}{killed}"
            )
    targets_clause = "; ".join(parts)
    if melee:
        return f"{caster_str} hit {targets_clause}."
    return f"{caster_str} cast {spell_name} at {targets_clause}."


def _render_action_section(records, idx, wizard_team, show_coords,
                            movement_verbose, wizard_pos=None,
                            spawn_coord_cap=5):
    """Render the non-player actions section. Returns (items, claimed_records).

    Walks for non-player cast_begin roots. Single-target chains collapse
    across actors via signature; multi-target chains stay separate. Each
    rendered line is wrapped as a line-item with rank (enemy 0 / ally 1) and
    the caster(s) as spatial anchors; the producer orders the items by
    proximity. Returns [] when there are no non-player actions."""
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

    out_items = []

    def _rank(is_ally):
        return RANK_ALLY_ACTION if is_ally else RANK_ENEMY_ACTION

    for sig in chain_order:
        group = chains_by_sig[sig]
        first_root, first_chain = group[0]
        is_ally = _is_ally(first_root)
        if len(group) == 1:
            line = _render_action_chain(first_chain, wizard_team,
                                         show_coords, movement_verbose,
                                         wizard_pos, spawn_coord_cap)
            anchors = [(first_chain[0].get('payload') or {}).get('caster')]
        else:
            line = _render_collapsed_action(group, wizard_team,
                                             show_coords, movement_verbose)
            anchors = [(item[0].get('payload') or {}).get('caster')
                       for item in group]
        if line:
            out_items.append(_make_item(_rank(is_ally), anchors, line))
        for root, chain in group:
            for rec in chain:
                # claim==render: never re-mark a record a higher-precedence
                # producer (crisis) already owns — the wizard-damage child of
                # an enemy cast is crisis's, not ours.
                if not _is_claimed_by_other(rec):
                    _claim(rec)
                    claimed.append(rec)

    for root, chain in standalone_chains:
        is_ally = _is_ally(root)
        line = _render_action_chain(chain, wizard_team, show_coords,
                                     movement_verbose, wizard_pos,
                                     spawn_coord_cap)
        if line:
            anchors = [(chain[0].get('payload') or {}).get('caster')]
            out_items.append(_make_item(_rank(is_ally), anchors, line))
        for rec in chain:
            if not _is_claimed_by_other(rec):
                _claim(rec)
                claimed.append(rec)

    return out_items, claimed


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
    # B2: a collapsed group of identical hits on the wizard is crisis's —
    # drop it from the orphan body (crisis collapses the same hits itself),
    # but only when crisis actually claimed it (claim==render; crisis-off
    # falls through to render here).
    if _is_wizard_snap(target) and _is_claimed_by_other(damage_events[0]):
        return None
    damage = dpayload.get('damage', 0)
    dtype = dpayload.get('damage_type')
    dtype_str = f" {dtype}" if dtype else ""

    coords = _coord_list(casters, show_coords)
    casters_str = f"{len(items)} {prefix}{plural}{coords}"

    # The collapse signature keys on a single target id, so every chain in the
    # group hit the same unit; one death capstone covers them all.
    dead = any(target.get('id') in _deaths_in_chain(chain)
               for _root, chain in items)
    killed = ", killed" if dead else ""

    target_str = _name_with_coord(target, wizard_team, show_coords)
    if melee:
        return f"{casters_str} hit {target_str}, {damage}{dtype_str} each{killed}."
    return (
        f"{casters_str} cast {spell_name} at {target_str},"
        f" {damage}{dtype_str} each{killed}."
    )


# ======================================================================
# Section 3: Status ticks (DOTs, fades, unfreeze)
# ======================================================================


def _render_status_ticks(records, idx, wizard_team, show_coords):
    """Compose status tick lines: DOT damage, buff fades, unfreeze events,
    all on non-wizard targets. Returns (items, claimed_records) — each line
    wrapped as a rank-STATUS line-item anchored on its target(s)."""
    out_items = []
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
    dot_dead_ids = set()  # targets a DOT killed this turn (death capstone)

    for root in buff_tick_roots:
        chain = _gather_chain(records, root, idx)
        dot_dead_ids.update(_deaths_in_chain(chain))
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
            # claim==render: a buff-tick chain on the wizard has its damage
            # child owned by crisis; don't double-mark it.
            if not _is_claimed_by_other(rec):
                _claim(rec)
                claimed.append(rec)

    # A DOT that killed its target capstones the tick line; `died` enters the
    # sig so a dead target never merges with a still-living one carrying the
    # same damage (the death is the salient distinction, and the killed unit
    # has no remaining duration to renotify).
    dot_groups = {}
    dot_order = []
    for key in pt_order:
        acc = per_target[key]
        t = acc['target']
        died = t.get('id') in dot_dead_ids
        sig = (acc['buff_name'], t.get('name'), t.get('tier'),
               acc['dtype'], acc['damage'], acc['turns'], died)
        if sig not in dot_groups:
            dot_order.append(sig)
            dot_groups[sig] = []
        dot_groups[sig].append(t)

    for sig in dot_order:
        buff_name, target_name, _tier, dtype, damage, turns, died = sig
        members = dot_groups[sig]
        dtype_str = f" {dtype}" if dtype else ""
        # A killed target has no live duration left, so suppress the countdown
        # in favor of the death capstone.
        turns_str = ("" if died
                     else (f", {turns} turns left" if turns and turns > 0 else ""))
        if len(members) == 1:
            killed = _killed_suffix(1 if died else 0)
            target_str = _name_with_coord(members[0], wizard_team, show_coords)
            out_items.append(_make_item(RANK_STATUS, [members[0]],
                f"{target_str} {buff_name}: {damage}{dtype_str}{turns_str}{killed}."))
        else:
            killed = _killed_suffix(len(members) if died else 0)
            plural = _pluralize(target_name or 'enemy')
            prefix = _team_prefix(members[0], wizard_team)
            coords = _coord_list(members, show_coords)
            out_items.append(_make_item(RANK_STATUS, members,
                f"{len(members)} {prefix}{plural}{coords} {buff_name}:"
                f" {damage}{dtype_str} each{turns_str}{killed}."))

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
            out_items.append(_make_item(RANK_STATUS, [members[0]],
                f"{target_str} {bname} faded."))
        else:
            plural = _pluralize(target_name or 'enemy')
            prefix = _team_prefix(members[0], wizard_team)
            coords = _coord_list(members, show_coords)
            out_items.append(_make_item(RANK_STATUS, members,
                f"{len(members)} {prefix}{plural}{coords} {bname} faded."))

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
        out_items.append(_make_item(RANK_STATUS, [target],
            f"{target_str} Frozen broke."))
        _claim(rec)
        claimed.append(rec)

    return out_items, claimed


# ======================================================================
# Section 4: causeless deaths (the death-capstone fallback)
# ======================================================================


def _render_standalone_deaths(records, wizard_team, show_coords):
    """Deaths with no rendered cause line this turn — a transformation /
    dismissal (the game's silent kill) or a death whose cause the journal
    didn't capture. In-chain deaths ride their cause line as a capstone (the
    action/status sections claim those records first); only what's left
    reaches here, rendered as a short standalone 'X died.' so an ambient death
    is never silent. Wizard death is crisis/digest territory and is skipped.
    Returns (items, claimed_records)."""
    out_items = []
    claimed = []
    for r in records:
        if r.get('event_type') != 'EventOnDeath':
            continue
        if _is_claimed_by_other(r) or _has_mark(r, ORPHAN_MARK):
            continue
        t = (r.get('payload') or {}).get('target') or {}
        if _is_wizard_snap(t):
            continue
        name_str = _name_with_coord(t, wizard_team, show_coords)
        out_items.append(_make_item(RANK_STATUS, [t], f"{name_str} died."))
        _claim(r)
        claimed.append(r)
    return out_items, claimed


def _render_spawns(records, idx, wizard_team, show_coords, wizard_pos,
                   spawn_coord_cap):
    """Producer-level spawn pass for ambient spawns NOT already capstoned on a
    cast line — spawn-on-death (from a DOT/cloud chain), generator / on_advance
    auras, and causeless adds. On-cast summons ride their own cast line (their
    chain root is an orphan-claimed cast_begin) and are skipped here. Player /
    gear spawns are owned by digest/crisis/equipment and skipped. One status
    line per (name, team) group, anchored on its members for proximity
    ordering. Returns (items, claimed_records)."""
    out_items = []
    claimed = []
    death_ids = {
        ((r.get('payload') or {}).get('target') or {}).get('id')
        for r in records if r.get('event_type') == 'EventOnDeath'
    }
    pending = []
    for r in records:
        if r.get('event_type') != 'EventOnUnitAdded':
            continue
        if _is_claimed_by_other(r):
            continue
        root = _walk_to_root(r, idx)
        if (root is not None and root.get('event_type') == 'cast_begin'
                and _has_mark(root, ORPHAN_MARK)):
            # Capstoned on its cast line by the action section.
            continue
        u = (r.get('payload') or {}).get('unit') or {}
        if _is_wizard_snap(u) or 'Soul Jar' in (u.get('name') or ''):
            continue
        _claim(r)
        claimed.append(r)
        # A unit that spawned then died the same turn already reads via its
        # death line; don't also announce it as a fresh spawn.
        if u.get('id') in death_ids:
            continue
        pending.append(u)

    for name, team, members in _spawn_groups(pending):
        phrase = _render_spawn_phrase(members, wizard_team, show_coords,
                                      wizard_pos, spawn_coord_cap)
        if phrase:
            out_items.append(_make_item(RANK_STATUS, members, phrase + "."))
    return out_items, claimed


# ======================================================================
# Producer
# ======================================================================


def _render_bare_effect_section(records, wizard_team, show_coords):
    """Render bare-root damage/heal effects that no chain claimed — e.g. a
    reactive gear proc that fired outside any cast/buff-advance context, so
    its EventOnDamaged/EventOnHealed has parent=None and is owned by no
    producer. Without this they fall through the whole pipeline and are
    silently lost. Wizard-targeted effects belong to crisis and are skipped.
    Source-attributed via the game-convention helper. Returns
    (items, claimed_records) — rank-BARE line-items anchored on their target."""
    out_items = []
    claimed = []
    for r in records:
        et = r.get('event_type')
        if et not in ('EventOnDamaged', 'EventOnHealed'):
            continue
        if r.get('parent') is not None:
            continue
        if _is_claimed_by_other(r) or _has_mark(r, ORPHAN_MARK):
            continue
        p = r.get('payload') or {}
        target = p.get('target') or {}
        if _is_wizard_snap(target):
            continue
        target_str = _name_with_coord(target, wizard_team, show_coords)
        if et == 'EventOnDamaged':
            line = source_attributed_line(
                'damage', amount=p.get('damage'), dtype=p.get('damage_type'),
                target_name=target_str, source_name=p.get('source_name'),
                source_owner_name=p.get('source_owner_name'),
                source_is_buff=p.get('source_is_buff'),
                source_buff_type=p.get('source_buff_type'))
        else:  # EventOnHealed
            line = source_attributed_line(
                'heal', amount=p.get('heal_amount'), dtype=None,
                target_name=target_str, source_name=p.get('source_name'),
                source_owner_name=p.get('source_owner_name'),
                source_is_buff=p.get('source_is_buff'),
                source_buff_type=p.get('source_buff_type'))
        if line:
            out_items.append(_make_item(RANK_BARE, [target], line + "."))
            _claim(r)
            claimed.append(r)
    return out_items, claimed


class _OrphanProducer:
    """Stateful across calls: tracks the highest journal sequence
    processed. Fires once per turn boundary. Returns a tagged section
    for the unified emitter."""

    def __init__(self):
        self._last_processed_seq = -1

    def fire(self, journal_records, show_coords, movement_verbose,
              log_fn, telemetry=None, wizard_pos=None,
              los_grouping='section', spawn_coord_cap=5):
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
            wizard_pos: (x, y) of the wizard, or None. When provided, the
                composed lines are proximity/line-of-sight ordered (R2) and
                gated by `los_grouping`. None → stable rank order, no LoS gate
                (no spatial frame; legacy behavior, used between levels and by
                the pure-text unit tests).
            los_grouping: 'section' (default) | 'block' | 'line' — where the
                'Out of sight.' transition(s) are spoken.
            spawn_coord_cap: int — the spawn-locality coord/cluster threshold
                (R1 spawn rendering; threaded to the spawn formatter).

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

        action_items, action_claimed = _render_action_section(
            new_records, idx, wizard_team, show_coords, movement_verbose,
            wizard_pos, spawn_coord_cap
        )
        tick_items, tick_claimed = _render_status_ticks(
            new_records, idx, wizard_team, show_coords
        )
        # Bare-root procs (gear that fired outside any chain) — claimed last,
        # after action/status sections have taken their chains.
        bare_items, _bare_claimed = _render_bare_effect_section(
            new_records, wizard_team, show_coords
        )
        # Causeless deaths the cause sections didn't already capstone.
        death_items, _death_claimed = _render_standalone_deaths(
            new_records, wizard_team, show_coords
        )
        # Spawns not capstoned on a cast line (spawn-on-death, generators,
        # causeless adds). Runs after the action section so on-cast summons
        # are already marked.
        spawn_items, _spawn_claimed = _render_spawns(
            new_records, idx, wizard_team, show_coords, wizard_pos,
            spawn_coord_cap
        )

        all_items = (action_items + tick_items + bare_items + death_items
                     + spawn_items)
        text = _assemble_items(all_items, wizard_pos, los_grouping)

        if telemetry is not None:
            try:
                telemetry.emit(
                    'orphan_emit',
                    action_lines=len(action_items),
                    status_tick_lines=len(tick_items),
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
