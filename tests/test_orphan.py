# Tests for orphan.py producer.
# Run with: cd ~ && python -m pytest "<path_to_mod>/tests/test_orphan.py" -v

import sys
import os

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from orphan import (
    ORPHAN_MARK,
    PRIORITY_STANDARD_ORPHAN,
    _build_index,
    _build_action_signature,
    _coord_list,
    _gather_chain,
    _is_nonplayer_cast_root,
    _name_with_coord,
    _render_action_chain,
    _render_action_section,
    _render_collapsed_action,
    _render_status_ticks,
    _team_prefix,
    _OrphanProducer,
    _assemble_items,
    _item_spatial,
    _make_item,
    RANK_ENEMY_ACTION,
    RANK_ALLY_ACTION,
    RANK_STATUS,
    RANK_BARE,
)


# ---- Fixtures ----


def _tick_lines(records, idx, wizard_team=0, show_coords=True):
    """Extract the rendered text of each status-tick line-item. The section
    now returns (items, claimed); these unit tests assert on the text."""
    items, _claimed = _render_status_ticks(records, idx,
                                            wizard_team=wizard_team,
                                            show_coords=show_coords)
    return [it['text'] for it in items]


def _wizard_snap(team=0):
    return {
        'id': 100, 'name': 'Wizard', 'x': 10, 'y': 10,
        'cur_hp': 50, 'max_hp': 50, 'team': team, 'tier': 'wizard',
        'is_player_controlled': True,
        'is_boss': False, 'is_lair': False, 'parent_id': None,
    }


def _enemy_snap(uid=200, name='Aelf', x=5, y=5, tier='minion', team=1):
    return {
        'id': uid, 'name': name, 'x': x, 'y': y,
        'cur_hp': 18, 'max_hp': 18, 'team': team, 'tier': tier,
        'is_player_controlled': False,
        'is_boss': tier == 'boss',
        'is_lair': tier == 'spawner',
        'parent_id': None,
    }


def _ally_snap(uid=300, name='Goatia', x=8, y=8):
    return {
        'id': uid, 'name': name, 'x': x, 'y': y,
        'cur_hp': 20, 'max_hp': 20, 'team': 0, 'tier': 'minion',
        'is_player_controlled': False,
        'is_boss': False, 'is_lair': False, 'parent_id': None,
    }


def _enemy_cast_chain(seq_start, caster, spell_name, target, damage, dtype,
                      melee=False):
    """Build a non-player cast chain: cast_begin + EventOnPreDamaged +
    EventOnDamaged. Returns the list of records."""
    return [
        {
            'sequence': seq_start, 'parent': None,
            'event_type': 'cast_begin',
            'payload': {
                'caster': caster,
                'spell': {'name': spell_name, 'melee': melee,
                          'cur_charges': 1, 'max_charges': 1},
                'is_player': False,
                'pay_costs': True,
            },
            'marks': [],
        },
        {
            'sequence': seq_start + 1, 'parent': seq_start,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': target,
                'damage_pre_resist': damage, 'damage_post_resist': damage,
                'resisted': False,
                'damage_type': dtype,
                'source_name': spell_name,
            },
            'marks': [],
        },
        {
            'sequence': seq_start + 2, 'parent': seq_start,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': target,
                'damage': damage,
                'damage_type': dtype,
                'source_name': spell_name,
            },
            'marks': [],
        },
    ]


# ---- Helper predicate tests ----


def test_team_prefix_ally():
    ally = _ally_snap()
    assert _team_prefix(ally, wizard_team=0) == "Ally "


def test_team_prefix_enemy():
    enemy = _enemy_snap()
    assert _team_prefix(enemy, wizard_team=0) == ""


def test_team_prefix_no_wizard_team():
    ally = _ally_snap()
    assert _team_prefix(ally, wizard_team=None) == ""


def test_team_prefix_skips_wizard_self():
    """Wizard's own snapshot has is_player_controlled=True; should NOT
    get an Ally prefix even though team matches."""
    wiz = _wizard_snap()
    assert _team_prefix(wiz, wizard_team=0) == ""


def test_name_with_coord_on():
    enemy = _enemy_snap(name='Aelf', x=3, y=4)
    assert _name_with_coord(enemy, wizard_team=0, show_coords=True) == "Aelf (3,4)"


def test_name_with_coord_off():
    enemy = _enemy_snap(name='Aelf')
    assert _name_with_coord(enemy, wizard_team=0, show_coords=False) == "Aelf"


def test_name_with_ally_prefix_and_coord():
    ally = _ally_snap(name='Goatia', x=8, y=8)
    assert _name_with_coord(ally, wizard_team=0, show_coords=True) == "Ally Goatia (8,8)"


def test_coord_list_skips_when_off():
    members = [_enemy_snap(uid=1, x=3, y=4), _enemy_snap(uid=2, x=5, y=6)]
    assert _coord_list(members, show_coords=False) == ""


def test_coord_list_renders_when_on():
    members = [_enemy_snap(uid=1, x=3, y=4), _enemy_snap(uid=2, x=5, y=6)]
    assert _coord_list(members, show_coords=True) == " at (3,4), (5,6)"


def test_is_nonplayer_cast_root_enemy():
    rec = {
        'event_type': 'cast_begin', 'parent': None,
        'payload': {'is_player': False, 'pay_costs': True},
    }
    assert _is_nonplayer_cast_root(rec) is True


def test_is_nonplayer_cast_root_player_skipped():
    rec = {
        'event_type': 'cast_begin', 'parent': None,
        'payload': {'is_player': True, 'pay_costs': True},
    }
    assert _is_nonplayer_cast_root(rec) is False


def test_is_nonplayer_cast_root_with_parent_skipped():
    rec = {
        'event_type': 'cast_begin', 'parent': 5,
        'payload': {'is_player': False, 'pay_costs': True},
    }
    assert _is_nonplayer_cast_root(rec) is False


# ---- _render_action_chain ----


def test_action_chain_single_target_cast():
    """Enemy cast at a NON-wizard target renders normally."""
    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Aelf', x=3, y=4),
        'Lightning Bolt', _enemy_snap(uid=999, name='Bone Shambler', x=12, y=12),
        damage=6, dtype='Lightning',
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line == "Aelf (3,4) cast Lightning Bolt at Bone Shambler (12,12), 6 Lightning."


def _mark_crisis(chain):
    """Stamp crisis_v1 on the wizard-target damage records — crisis runs
    first in the pipeline, so by orphan's turn it owns wizard damage."""
    for rec in chain:
        p = rec.get('payload') or {}
        if rec.get('event_type') == 'EventOnDamaged' \
                and (p.get('target') or {}).get('is_player_controlled'):
            rec.setdefault('marks', []).append('crisis_v1')
    return chain


def test_action_chain_wizard_only_attack_dropped():
    """B2: an enemy cast whose ONLY effect was crisis-claimed wizard damage is
    dropped from the orphan body (crisis owns it) — no double-narration."""
    chain = _mark_crisis(_enemy_cast_chain(
        10, _enemy_snap(name='Aelf', x=3, y=4),
        'Lightning Bolt', _wizard_snap(),
        damage=6, dtype='Lightning',
    ))
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line is None


def test_action_chain_wizard_damage_rendered_when_crisis_off():
    """claim==render: if crisis did NOT claim the wizard hit (crisis disabled),
    orphan must still render it rather than drop it silently."""
    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Aelf', x=3, y=4),
        'Lightning Bolt', _wizard_snap(),
        damage=6, dtype='Lightning',
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line == "Aelf (3,4) cast Lightning Bolt at Wizard (10,10), 6 Lightning."


def test_action_chain_single_target_melee():
    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Bone Shambler', x=8, y=8),
        'Melee', _enemy_snap(uid=999, name='Goblin', x=9, y=9),
        damage=4, dtype='Physical', melee=True,
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=True)
    # Melee uses "hit" verb, no spell name.
    assert line == "Bone Shambler (8,8) hit Goblin (9,9), 4 Physical."


def test_action_chain_no_damage():
    chain = [{
        'sequence': 10, 'parent': None,
        'event_type': 'cast_begin',
        'payload': {
            'caster': _enemy_snap(name='Aelf', x=3, y=4),
            'spell': {'name': 'Lightning Bolt', 'melee': False},
            'is_player': False,
        },
        'marks': [],
    }]
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line == "Aelf (3,4) cast Lightning Bolt."


def test_action_chain_ally_caster_prefixed():
    chain = _enemy_cast_chain(
        10, _ally_snap(name='Goatia', x=8, y=8),
        'Smite', _enemy_snap(name='Bone Shambler'),
        damage=6, dtype='Holy',
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line == "Ally Goatia (8,8) cast Smite at Bone Shambler (5,5), 6 Holy."


# ---- _render_collapsed_action (cross-chain collapse) ----


def test_collapsed_action_three_aelves_at_enemy():
    """Three Aelves all cast Lightning Bolt at a non-wizard target — collapse."""
    target = _enemy_snap(uid=999, name='Bone Shambler', x=10, y=10)
    items = []
    for i, x in enumerate([3, 4, 5]):
        chain = _enemy_cast_chain(
            10 + i * 3, _enemy_snap(uid=200 + i, name='Aelf', x=x, y=4),
            'Lightning Bolt', target, damage=6, dtype='Lightning',
        )
        items.append((chain[0], chain))
    line = _render_collapsed_action(items, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line == ("3 Aelves at (3,4), (4,4), (5,4) cast Lightning Bolt at "
                    "Bone Shambler (10,10), 6 Lightning each.")


def test_collapsed_action_three_aelves_at_wizard_dropped():
    """B2: a collapsed group of crisis-claimed identical hits on the wizard is
    crisis's — the orphan body drops it."""
    target = _wizard_snap()
    items = []
    for i, x in enumerate([3, 4, 5]):
        chain = _mark_crisis(_enemy_cast_chain(
            10 + i * 3, _enemy_snap(uid=200 + i, name='Aelf', x=x, y=4),
            'Lightning Bolt', target, damage=6, dtype='Lightning',
        ))
        items.append((chain[0], chain))
    line = _render_collapsed_action(items, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line is None


# ---- _build_action_signature ----


def test_signature_single_target_chain_returns_tuple():
    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Aelf'),
        'Lightning Bolt', _wizard_snap(),
        damage=6, dtype='Lightning',
    )
    sig = _build_action_signature(chain)
    assert sig is not None
    # (caster_name, caster_tier, spell_name, melee, kind, target_id,
    #  target_name, dtype, damage)
    assert sig == ('Aelf', 'minion', 'Lightning Bolt', False, 'damage',
                   100, 'Wizard', 'Lightning', 6)


def _movement_chain(seq_start, caster, spell_name, dest_x, dest_y):
    """Build a movement-via-cast chain: cast_begin + EventOnSpellCast +
    EventOnMoved (caster moves to destination)."""
    cid = caster['id']
    return [
        {
            'sequence': seq_start, 'parent': None,
            'event_type': 'cast_begin',
            'payload': {
                'caster': caster,
                'spell': {'name': spell_name, 'melee': False,
                          'cur_charges': 0, 'max_charges': 0},
                'is_player': False, 'pay_costs': True,
            },
            'marks': [],
        },
        {
            'sequence': seq_start + 1, 'parent': seq_start,
            'event_type': 'EventOnSpellCast',
            'payload': {
                'caster': caster,
                'spell': {'name': spell_name, 'melee': False},
                'pay_costs': True,
            },
            'marks': [],
        },
        {
            'sequence': seq_start + 2, 'parent': seq_start,
            'event_type': 'EventOnMoved',
            'payload': {
                'unit': {**caster, 'x': dest_x, 'y': dest_y},
                'teleport': True,
            },
            'marks': [],
        },
    ]


def test_signature_movement_chain():
    """Movement-via-cast: caster moves, no damage. Signature collapses
    same-caster-type same-spell movements."""
    chain = _movement_chain(
        10, _enemy_snap(uid=200, name='Horned Toad', x=21, y=23),
        'Frog Hop', dest_x=22, dest_y=22,
    )
    sig = _build_action_signature(chain)
    assert sig == ('Horned Toad', 'minion', 'Frog Hop', False,
                   'movement', None, None, None)


def test_movement_chain_renders_with_destination():
    """Single movement chain renders with start (caster pre-move) and
    end (post-move) coords when movement_verbose=True."""
    chain = _movement_chain(
        10, _enemy_snap(uid=200, name='Horned Toad', x=21, y=23),
        'Frog Hop', dest_x=22, dest_y=22,
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line == "Horned Toad (21,23) cast Frog Hop, moved to (22,22)."


def test_movement_chain_coords_off():
    """movement_verbose=True + show_coords=False: no coords at all,
    just 'moved' acknowledgment."""
    chain = _movement_chain(
        10, _enemy_snap(uid=200, name='Horned Toad', x=21, y=23),
        'Frog Hop', dest_x=22, dest_y=22,
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=False, movement_verbose=True)
    assert line == "Horned Toad cast Frog Hop, moved."


def test_movement_chain_compact_default():
    """movement_verbose=False (default): destination preserved, start
    coord dropped from caster identifier. Cuts noise without losing the
    cause-and-endpoint information."""
    chain = _movement_chain(
        10, _enemy_snap(uid=200, name='Horned Toad', x=21, y=23),
        'Frog Hop', dest_x=22, dest_y=22,
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=True, movement_verbose=False)
    # No "(21,23)" pre-move coord; destination "(22,22)" preserved.
    assert line == "Horned Toad cast Frog Hop, moved to (22,22)."


def test_movement_chain_compact_coords_off():
    """movement_verbose=False + show_coords=False: bare 'moved' line."""
    chain = _movement_chain(
        10, _enemy_snap(uid=200, name='Horned Toad', x=21, y=23),
        'Frog Hop', dest_x=22, dest_y=22,
    )
    line = _render_action_chain(chain, wizard_team=0, show_coords=False, movement_verbose=False)
    assert line == "Horned Toad cast Frog Hop, moved."


def test_movement_collapsed_compact_drops_destinations():
    """Multi-collapse with movement_verbose=False: per-caster
    destinations dropped, line says only N casters cast Spell, moved."""
    items = []
    starts = [(21, 23), (17, 10), (13, 2)]
    ends = [(22, 22), (20, 9), (15, 5)]
    for i, ((sx, sy), (dx, dy)) in enumerate(zip(starts, ends)):
        chain = _movement_chain(
            10 + i * 3,
            _enemy_snap(uid=200 + i, name='Horned Toad', x=sx, y=sy),
            'Frog Hop', dest_x=dx, dest_y=dy,
        )
        items.append((chain[0], chain))
    line = _render_collapsed_action(items, wizard_team=0, show_coords=True, movement_verbose=False)
    assert line == "3 Horned Toads cast Frog Hop, moved."


def test_movement_collapsed_three_toads():
    """Three Horned Toads each frog-hop to a different destination —
    collapsed line preserves each from→to pair."""
    items = []
    starts = [(21, 23), (17, 10), (13, 2)]
    ends = [(22, 22), (20, 9), (15, 5)]
    for i, ((sx, sy), (dx, dy)) in enumerate(zip(starts, ends)):
        chain = _movement_chain(
            10 + i * 3,
            _enemy_snap(uid=200 + i, name='Horned Toad', x=sx, y=sy),
            'Frog Hop', dest_x=dx, dest_y=dy,
        )
        items.append((chain[0], chain))
    line = _render_collapsed_action(items, wizard_team=0, show_coords=True, movement_verbose=True)
    assert line == ("3 Horned Toads cast Frog Hop: (21,23) to (22,22),"
                    " (17,10) to (20,9), (13,2) to (15,5).")


def test_action_section_collapses_movement_across_chains():
    """Producer-level: multiple movement chains for same-type casters
    same-spell collapse via signature into one line in the section."""
    p = _OrphanProducer()

    def noop(_): pass

    starts = [(21, 23), (17, 10)]
    ends = [(22, 22), (20, 9)]
    records = []
    for i, ((sx, sy), (dx, dy)) in enumerate(zip(starts, ends)):
        records.extend(_movement_chain(
            10 + i * 3,
            _enemy_snap(uid=200 + i, name='Horned Toad', x=sx, y=sy),
            'Frog Hop', dest_x=dx, dest_y=dy,
        ))
    section = p.fire(records, show_coords=True, movement_verbose=True, log_fn=noop, telemetry=None)
    text = section[1]
    # One collapsed line, not two.
    assert text.count("cast Frog Hop") == 1
    assert "(21,23) to (22,22)" in text
    assert "(17,10) to (20,9)" in text


def test_signature_multi_target_chain_returns_none():
    """AoE chain with multiple damage events — skip cross-chain collapse."""
    base = _enemy_cast_chain(
        10, _enemy_snap(name='Aelf'),
        'Chain Lightning', _enemy_snap(uid=300, name='Goblin', x=6, y=6),
        damage=5, dtype='Lightning',
    )
    # Add a second damage event for a different target.
    base.append({
        'sequence': 13, 'parent': 10,
        'event_type': 'EventOnDamaged',
        'payload': {
            'target': _enemy_snap(uid=301, name='Goblin', x=7, y=7),
            'damage': 5, 'damage_type': 'Lightning',
            'source_name': 'Chain Lightning',
        },
        'marks': [],
    })
    sig = _build_action_signature(base)
    assert sig is None


# ---- _render_status_ticks ----


def test_status_ticks_dot_damage_on_enemy():
    """Goblin Poisoned, ticks for 1 Poison this turn."""
    target = _enemy_snap(name='Goblin', x=3, y=4)
    records = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'buff_tick',
            'payload': {
                'buff': {'name': 'Poisoned', 'turns_left': 3,
                         'stack_type': 1},
                'owner': target,
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': target,
                'damage': 1, 'damage_type': 'Poison',
                'source_name': 'Poisoned',
                'source_turns_left': 3,
            },
            'marks': [],
        },
    ]
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == ["Goblin (3,4) Poisoned: 1 Poison, 3 turns left."]


def _bleed_stack(seq, target, turns):
    """A buff_tick root + its EventOnDamaged child for one Bleed stack."""
    return [
        {
            'sequence': seq, 'parent': None, 'event_type': 'buff_tick',
            'payload': {
                'buff': {'name': 'Bleed', 'turns_left': turns,
                         'stack_type': 2},
                'owner': target,
            },
            'marks': [],
        },
        {
            'sequence': seq + 1, 'parent': seq, 'event_type': 'EventOnDamaged',
            'payload': {
                'target': target, 'damage': 3, 'damage_type': 'Physical',
                'source_name': 'Bleed', 'source_turns_left': turns,
            },
            'marks': [],
        },
    ]


def test_status_ticks_dot_stacks_sum_on_single_target():
    """Three Bleed stacks on one Goblin sum to the true per-turn total, and
    report the longest remaining duration."""
    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    records = []
    for i, turns in enumerate((4, 3, 2)):
        records.extend(_bleed_stack(10 + i * 10, g, turns))
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == ["Goblin (3,4) Bleed: 9 Physical, 4 turns left."]


def test_status_ticks_dot_stacks_collapse_across_targets():
    """Two Goblins each carrying two Bleed stacks (6 each) collapse to one
    line — not four, and not a phantom target count."""
    records = []
    seq = 10
    for uid, x in ((200, 3), (201, 5)):
        g = _enemy_snap(uid=uid, name='Goblin', x=x, y=4)
        records.extend(_bleed_stack(seq, g, 3))
        records.extend(_bleed_stack(seq + 2, g, 3))
        seq += 10
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert len(lines) == 1
    assert lines[0].startswith("2 Goblins")
    assert "Bleed: 6 Physical each" in lines[0]


def test_status_ticks_skips_wizard_dot():
    """Wizard DOT tick is crisis territory; orphan skips."""
    records = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'buff_tick',
            'payload': {
                'buff': {'name': 'Poisoned', 'turns_left': 3,
                         'stack_type': 1},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': _wizard_snap(),
                'damage': 1, 'damage_type': 'Poison',
                'source_name': 'Poisoned',
                'source_turns_left': 3,
            },
            'marks': [],
        },
    ]
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == []


def test_status_ticks_buff_fade_on_enemy():
    """Aelf's Petrified fades naturally — 'Aelf (3,4) Petrified faded.'"""
    records = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'EventOnBuffRemove',
            'payload': {
                'target': _enemy_snap(name='Aelf', x=3, y=4),
                'buff': {'name': 'Petrified', 'turns_left': 0,
                         'stack_type': 0},
            },
            'marks': [],
        },
    ]
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == ["Aelf (3,4) Petrified faded."]


def test_status_ticks_buff_fade_skips_unit_removed():
    """is_unit_removed is buff cleanup on death — not a fade we narrate."""
    records = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'EventOnBuffRemove',
            'payload': {
                'target': _enemy_snap(name='Aelf'),
                'buff': {'name': 'Petrified', 'turns_left': 0},
                'is_unit_removed': True,
            },
            'marks': [],
        },
    ]
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == []


def test_status_ticks_unfreeze():
    records = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'EventOnUnfrozen',
            'payload': {
                'target': _enemy_snap(name='Goblin', x=3, y=4),
                'damage_type': 'Fire',
            },
            'marks': [],
        },
    ]
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == ["Goblin (3,4) Frozen broke."]


# ---- Producer integration ----


def test_producer_returns_priority_orphan():
    p = _OrphanProducer()

    def noop(_): pass

    section = p.fire([], show_coords=True, movement_verbose=True, log_fn=noop, telemetry=None)
    assert section[0] == PRIORITY_STANDARD_ORPHAN


def test_producer_orders_enemies_first_then_allies():
    """Enemy and ally cast in same turn — enemy line should come first."""
    p = _OrphanProducer()

    def noop(_): pass

    target = _enemy_snap(uid=999, name='Bone Shambler', x=12, y=12)
    # Enemy targets a NON-wizard (an ally) so its cast line renders — a
    # wizard-only attack would be dropped (B2) and defeat the ordering check.
    enemy_chain = _enemy_cast_chain(
        10, _enemy_snap(name='Aelf', x=3, y=4),
        'Lightning Bolt', _ally_snap(uid=400, name='Spark Spirit', x=9, y=9),
        damage=6, dtype='Lightning',
    )
    ally_chain = _enemy_cast_chain(
        20, _ally_snap(name='Goatia', x=8, y=8),
        'Smite', target, damage=4, dtype='Holy',
    )
    records = enemy_chain + ally_chain
    section = p.fire(records, show_coords=True, movement_verbose=True, log_fn=noop, telemetry=None)
    text = section[1]
    aelf_pos = text.find("Aelf")
    goatia_pos = text.find("Goatia")
    assert aelf_pos != -1
    assert goatia_pos != -1
    # Enemy line precedes ally line.
    assert aelf_pos < goatia_pos


def test_producer_skips_records_claimed_by_crisis():
    """A damage event on the wizard already claimed by crisis (mark
    set externally before orphan fires) is not re-rendered by orphan."""
    p = _OrphanProducer()

    def noop(_): pass

    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Aelf'),
        'Lightning Bolt', _wizard_snap(),
        damage=6, dtype='Lightning',
    )
    # Mark the cast root as crisis-claimed (simulating crisis having
    # foregrounded this damage already).
    chain[0]['marks'] = ['crisis_v1']
    section = p.fire(chain, show_coords=True, movement_verbose=True, log_fn=noop, telemetry=None)
    # Orphan respects the crisis claim and doesn't render the chain.
    # (The damage event on wizard is also crisis-claimed in real flow,
    # but here we just check the cast root respect.)
    # Note: this test only verifies the gate; the actual mark-precedence
    # behavior is enforced via the pipeline-level crisis-runs-first ordering.
    assert "Aelf" not in section[1]


# ---- §4.2 embedded foreign-source damage (reactive gear during enemy cast) ----


def test_action_chain_foreign_proc_attributed_to_its_source():
    caster = _enemy_snap(name='Aelf')
    imp = _enemy_snap(uid=202, name='Imp', x=7, y=7)
    ogre = _enemy_snap(uid=201, name='Ogre', x=6, y=6)
    chain = [
        {'sequence': 1, 'parent': None, 'event_type': 'cast_begin',
         'payload': {'caster': caster,
                     'spell': {'name': 'Lightning Bolt', 'melee': False},
                     'is_player': False, 'pay_costs': True}, 'marks': []},
        {'sequence': 2, 'parent': 1, 'event_type': 'EventOnDamaged',
         'payload': {'target': imp, 'damage': 6, 'damage_type': 'Lightning',
                     'source_name': 'Lightning Bolt'}, 'marks': []},
        # Player's reactive gear ("Thorns") fires during the cast, hitting Ogre.
        {'sequence': 3, 'parent': 2, 'event_type': 'EventOnDamaged',
         'payload': {'target': ogre, 'damage': 3, 'damage_type': 'Physical',
                     'source_name': 'Thorns', 'source_owner_name': 'Wizard',
                     'source_is_buff': True, 'source_buff_type': 3},
         'marks': []},
    ]
    line = _render_action_chain(chain, wizard_team=0, show_coords=False,
                                movement_verbose=False)
    # Caster line names only its own spell + own target.
    assert "Aelf cast Lightning Bolt at Imp, 6 Lightning." in line
    # Foreign proc attributed to Thorns, not folded into Lightning Bolt.
    assert "Wizard deals 3 Physical to Ogre with Thorns." in line


def test_action_chain_no_foreign_unchanged():
    # A pure enemy cast (own damage only) renders exactly as before.
    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Aelf'), 'Lightning Bolt',
        _enemy_snap(uid=205, name='Imp', x=7, y=7),
        damage=6, dtype='Lightning')
    line = _render_action_chain(chain, wizard_team=0, show_coords=False,
                                movement_verbose=False)
    assert line == "Aelf cast Lightning Bolt at Imp, 6 Lightning."
    assert "with" not in line  # no foreign clause appended


# ---- §4.3 bare-root procs (gear firing outside any chain) ----


def test_bare_proc_damage_claimed_and_voiced():
    p = _OrphanProducer()
    def noop(_): pass
    bare = {'sequence': 5, 'parent': None, 'event_type': 'EventOnDamaged',
            'payload': {'target': _enemy_snap(name='Ogre'), 'damage': 3,
                        'damage_type': 'Fire',
                        'source_name': 'Searing Eye Stone',
                        'source_owner_name': 'Wizard',
                        'source_is_buff': True, 'source_buff_type': 3},
            'marks': []}
    section = p.fire([bare], show_coords=False, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "Wizard deals 3 Fire to Ogre with Searing Eye Stone." in section[1]
    assert ORPHAN_MARK in bare['marks']


def test_bare_proc_heal_claimed_and_voiced():
    p = _OrphanProducer()
    def noop(_): pass
    bare = {'sequence': 5, 'parent': None, 'event_type': 'EventOnHealed',
            'payload': {'target': _ally_snap(name='Goatia'), 'heal_amount': 4,
                        'source_name': 'Wild Healing Staff',
                        'source_owner_name': 'Wizard',
                        'source_is_buff': True, 'source_buff_type': 3},
            'marks': []}
    section = p.fire([bare], show_coords=False, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "Wizard heals Goatia for 4 with Wild Healing Staff." in section[1]
    assert ORPHAN_MARK in bare['marks']


def test_bare_proc_on_wizard_skipped():
    # Wizard-targeted bare effects belong to crisis; orphan skips them.
    p = _OrphanProducer()
    def noop(_): pass
    bare = {'sequence': 5, 'parent': None, 'event_type': 'EventOnDamaged',
            'payload': {'target': _wizard_snap(), 'damage': 3,
                        'damage_type': 'Fire', 'source_name': 'X',
                        'source_owner_name': 'Goblin',
                        'source_is_buff': False, 'source_buff_type': None},
            'marks': []}
    section = p.fire([bare], show_coords=False, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == ""
    assert ORPHAN_MARK not in bare['marks']


def test_bare_proc_already_claimed_skipped():
    p = _OrphanProducer()
    def noop(_): pass
    bare = {'sequence': 5, 'parent': None, 'event_type': 'EventOnDamaged',
            'payload': {'target': _enemy_snap(name='Ogre'), 'damage': 3,
                        'damage_type': 'Fire', 'source_name': 'X',
                        'source_owner_name': 'Wizard',
                        'source_is_buff': True, 'source_buff_type': 3},
            'marks': ['digest_v1']}
    section = p.fire([bare], show_coords=False, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == ""
    assert ORPHAN_MARK not in bare['marks']


def test_bare_section_ignores_parented_damage():
    # A damage event WITH a parent is not a bare root; the bare section
    # leaves it to its chain's handling.
    p = _OrphanProducer()
    def noop(_): pass
    rec = {'sequence': 5, 'parent': 4, 'event_type': 'EventOnDamaged',
           'payload': {'target': _enemy_snap(name='Ogre'), 'damage': 3,
                       'damage_type': 'Fire', 'source_name': 'X',
                       'source_owner_name': 'Wizard',
                       'source_is_buff': True, 'source_buff_type': 3},
           'marks': []}
    section = p.fire([rec], show_coords=False, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "Ogre" not in section[1]


# ---- Stage B: proximity + line-of-sight ordering (R2) ----


def _anchor(x, y, los=True):
    """A minimal anchor snapshot carrying just the spatial fields the
    ordering layer reads."""
    return {'x': x, 'y': y, 'can_see_wizard': los}


def test_item_spatial_uses_nearest_anchor():
    """A collapsed line's spatial key comes from its NEAREST member."""
    wx, wy = 10, 10
    # far member (dist 5) out of sight, near member (dist 1) in sight.
    item = _make_item(RANK_ENEMY_ACTION,
                      [_anchor(5, 5, los=False), _anchor(9, 9, los=True)],
                      "two casters")
    in_los, dist = _item_spatial(item, wx, wy)
    assert dist == 1
    assert in_los is True


def test_item_spatial_missing_coords_sorts_far():
    item = _make_item(RANK_STATUS, [{'x': None, 'y': None}], "no position")
    in_los, dist = _item_spatial(item, 10, 10)
    assert in_los is False
    assert dist >= 10 ** 6


def test_assemble_no_wizard_pos_is_rank_order():
    """No spatial frame -> stable rank order, no 'Out of sight.' gate
    (legacy behavior, byte-identical assembly)."""
    items = [
        _make_item(RANK_BARE, [_anchor(1, 1)], "bare."),
        _make_item(RANK_STATUS, [_anchor(1, 1)], "status."),
        _make_item(RANK_ENEMY_ACTION, [_anchor(1, 1)], "enemy."),
        _make_item(RANK_ALLY_ACTION, [_anchor(1, 1)], "ally."),
    ]
    out = _assemble_items(items, wizard_pos=None, los_grouping='section')
    assert out == "enemy. ally. status. bare."


def test_assemble_in_sight_sorted_by_distance():
    """Within a rank, nearer subject leads (P1)."""
    items = [
        _make_item(RANK_ENEMY_ACTION, [_anchor(2, 2)], "far enemy."),   # dist 8
        _make_item(RANK_ENEMY_ACTION, [_anchor(9, 9)], "near enemy."),  # dist 1
    ]
    out = _assemble_items(items, wizard_pos=(10, 10), los_grouping='section')
    assert out == "near enemy. far enemy."


def test_assemble_rank_then_distance():
    """Rank dominates distance: a far enemy action still precedes a near
    status line (the sub-structure holds within the in-sight half)."""
    items = [
        _make_item(RANK_STATUS, [_anchor(9, 9)], "near status."),       # dist 1
        _make_item(RANK_ENEMY_ACTION, [_anchor(2, 2)], "far enemy."),   # dist 8
    ]
    out = _assemble_items(items, wizard_pos=(10, 10), los_grouping='section')
    assert out == "far enemy. near status."


def test_assemble_section_gate():
    """Out-of-sight lines land behind ONE 'Out of sight.' gate (default)."""
    items = [
        _make_item(RANK_ENEMY_ACTION, [_anchor(9, 9, los=True)], "in enemy."),
        _make_item(RANK_ENEMY_ACTION, [_anchor(2, 2, los=False)], "out enemy."),
        _make_item(RANK_STATUS, [_anchor(8, 8, los=False)], "out status."),
    ]
    out = _assemble_items(items, wizard_pos=(10, 10), los_grouping='section')
    assert out == "in enemy. Out of sight. out enemy. out status."


def test_assemble_block_gate_per_rank():
    """block mode gates the out-of-sight remainder within each rank."""
    items = [
        _make_item(RANK_ENEMY_ACTION, [_anchor(9, 9, los=True)], "in enemy."),
        _make_item(RANK_ENEMY_ACTION, [_anchor(2, 2, los=False)], "out enemy."),
        _make_item(RANK_STATUS, [_anchor(8, 8, los=True)], "in status."),
        _make_item(RANK_STATUS, [_anchor(1, 1, los=False)], "out status."),
    ]
    out = _assemble_items(items, wizard_pos=(10, 10), los_grouping='block')
    assert out == ("in enemy. Out of sight. out enemy. "
                   "in status. Out of sight. out status.")


def test_assemble_line_gate_per_line():
    """line mode tags each out-of-sight line individually; in-before-out
    still holds."""
    items = [
        _make_item(RANK_ENEMY_ACTION, [_anchor(9, 9, los=True)], "in enemy."),
        _make_item(RANK_ENEMY_ACTION, [_anchor(2, 2, los=False)], "out enemy."),
    ]
    out = _assemble_items(items, wizard_pos=(10, 10), los_grouping='line')
    assert out == "in enemy. out enemy. Out of sight."


def test_assemble_no_out_lines_no_gate():
    """All in-sight -> no 'Out of sight.' anywhere."""
    items = [
        _make_item(RANK_ENEMY_ACTION, [_anchor(9, 9, los=True)], "in a."),
        _make_item(RANK_STATUS, [_anchor(8, 8, los=True)], "in b."),
    ]
    out = _assemble_items(items, wizard_pos=(10, 10), los_grouping='section')
    assert "Out of sight" not in out
    assert out == "in a. in b."


def test_assemble_none_los_treated_out_of_sight():
    """A None can_see_wizard (undeterminable at capture) sorts out of sight."""
    items = [
        _make_item(RANK_ENEMY_ACTION, [_anchor(9, 9, los=True)], "in."),
        _make_item(RANK_ENEMY_ACTION,
                   [{'x': 2, 'y': 2, 'can_see_wizard': None}], "unknown."),
    ]
    out = _assemble_items(items, wizard_pos=(10, 10), los_grouping='section')
    assert out == "in. Out of sight. unknown."


def test_producer_proximity_orders_and_gates():
    """End-to-end: producer fed wizard_pos orders by proximity and gates the
    out-of-sight enemy behind 'Out of sight.'"""
    p = _OrphanProducer()

    def noop(_): pass

    near = _enemy_snap(uid=201, name='Ogre', x=9, y=9)
    near['can_see_wizard'] = True
    far_seen = _enemy_snap(uid=202, name='Aelf', x=4, y=4)
    far_seen['can_see_wizard'] = True
    hidden = _enemy_snap(uid=203, name='Imp', x=2, y=2)
    hidden['can_see_wizard'] = False
    ally_t = _ally_snap(uid=400, name='Wolf', x=8, y=8)

    records = (
        _enemy_cast_chain(10, far_seen, 'Fireball', ally_t, 5, 'Fire')
        + _enemy_cast_chain(20, near, 'Spark', ally_t, 3, 'Lightning')
        + _enemy_cast_chain(30, hidden, 'Dark Bolt', ally_t, 4, 'Dark')
    )
    section = p.fire(records, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None, wizard_pos=(10, 10))
    text = section[1]
    # In-sight, nearest first: Ogre (dist 1) before Aelf (dist 6).
    assert text.index("Ogre") < text.index("Aelf")
    # Hidden Imp is behind the gate.
    assert "Out of sight." in text
    assert text.index("Aelf") < text.index("Out of sight.") < text.index("Imp")
