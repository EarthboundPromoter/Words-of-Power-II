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
    _render_shield_changes,
    _render_shield_blocks,
    _render_team_changes,
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
    # (caster_name, caster_tier, spell_name, melee, is_channel, kind,
    #  target_id, target_name, dtype, damage)
    assert sig == ('Aelf', 'minion', 'Lightning Bolt', False, False,
                   'damage', 100, 'Wizard', 'Lightning', 6)


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
    assert sig == ('Horned Toad', 'minion', 'Frog Hop', False, False,
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


# ---- Stage C: death capstones (R1) ----


def _death_rec(seq, parent, target):
    return {'sequence': seq, 'parent': parent, 'event_type': 'EventOnDeath',
            'payload': {'target': target, 'killing_damage': None,
                        'killing_dtype': None, 'killing_source': None},
            'marks': []}


def test_cast_kill_capstones_the_cast_line():
    """An enemy cast that kills its target rides a ', killed' capstone."""
    goblin = _enemy_snap(uid=300, name='Goblin', x=5, y=5)
    chain = _enemy_cast_chain(10, _enemy_snap(name='Aelf', x=3, y=4),
                              'Lightning Bolt', goblin, 6, 'Lightning')
    chain.append(_death_rec(13, 10, goblin))
    line = _render_action_chain(chain, wizard_team=0, show_coords=True,
                                movement_verbose=False)
    assert line == "Aelf (3,4) cast Lightning Bolt at Goblin (5,5), 6 Lightning, killed."


def test_dot_death_capstones_tick_and_drops_duration():
    """A DOT that kills capstones the tick line and suppresses the countdown
    (the dead unit has no remaining duration)."""
    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    records = _bleed_stack(10, g, 2)
    records.append(_death_rec(12, 10, g))
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == ["Goblin (3,4) Bleed: 3 Physical, killed."]


def test_standalone_causeless_death_renders_died():
    """A death with no rendered cause (silent transformation/dismissal) gets
    a short standalone 'died' line."""
    p = _OrphanProducer()

    def noop(_): pass

    dead = _enemy_snap(uid=500, name='Imp', x=6, y=6)
    rec = {'sequence': 10, 'parent': None, 'event_type': 'EventOnDeath',
           'payload': {'target': dead, 'is_silent_kill': True}, 'marks': []}
    section = p.fire([rec], show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == "Imp (6,6) died."


def test_aoe_multi_kill_counts_deaths():
    """An AoE that kills several of one group reads ', N killed'."""
    g1 = _enemy_snap(uid=301, name='Goblin', x=5, y=5)
    g2 = _enemy_snap(uid=302, name='Goblin', x=6, y=5)
    g3 = _enemy_snap(uid=303, name='Goblin', x=7, y=5)
    caster = _enemy_snap(name='Aelf', x=1, y=1)
    chain = [
        {'sequence': 10, 'parent': None, 'event_type': 'cast_begin',
         'payload': {'caster': caster,
                     'spell': {'name': 'Fireball', 'melee': False},
                     'is_player': False, 'pay_costs': True}, 'marks': []},
    ]
    seq = 11
    for g in (g1, g2, g3):
        chain.append({'sequence': seq, 'parent': 10,
                      'event_type': 'EventOnDamaged',
                      'payload': {'target': g, 'damage': 9,
                                  'damage_type': 'Fire',
                                  'source_name': 'Fireball'}, 'marks': []})
        seq += 1
    # Two of the three die.
    chain.append(_death_rec(seq, 10, g1))
    chain.append(_death_rec(seq + 1, 10, g2))
    line = _render_action_chain(chain, wizard_team=0, show_coords=True,
                                movement_verbose=False)
    assert "3 Goblins" in line
    assert "9 Fire, 2 killed." in line


# ---- Stage C: spawn rendering (R1) ----


def _spawn_rec(seq, parent, unit):
    return {'sequence': seq, 'parent': parent, 'event_type': 'EventOnUnitAdded',
            'payload': {'unit': unit}, 'marks': []}


def test_on_cast_summon_capstones_cast_line():
    """An enemy summon spell names the wave it produced on its cast line."""
    p = _OrphanProducer()

    def noop(_): pass

    caster = _enemy_snap(name='Ash Fiend', x=3, y=4)
    imp1 = _enemy_snap(uid=601, name='Ash Imp', x=4, y=4)
    imp2 = _enemy_snap(uid=602, name='Ash Imp', x=5, y=3)
    chain = [
        {'sequence': 10, 'parent': None, 'event_type': 'cast_begin',
         'payload': {'caster': caster,
                     'spell': {'name': 'Summon Ash Imps', 'melee': False},
                     'is_player': False, 'pay_costs': True}, 'marks': []},
        _spawn_rec(11, 10, imp1),
        _spawn_rec(12, 10, imp2),
    ]
    section = p.fire(chain, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == ("Ash Fiend (3,4) cast Summon Ash Imps. "
                          "2 Ash Imps spawned at (4,4), (5,3).")


def test_causeless_spawn_standalone():
    """A spawn with no rendered cause (generator/on_advance) gets a standalone
    spawn line."""
    p = _OrphanProducer()

    def noop(_): pass

    fly = _enemy_snap(uid=700, name='Fly', x=6, y=6)
    rec = _spawn_rec(10, None, fly)
    section = p.fire([rec], show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == "1 Fly spawned at (6,6)."


def test_spawn_on_dot_death_renders_both_lines():
    """A Bag of Bugs killed by a DOT: the tick line capstones the death; the
    spawned Flies render as their own status line."""
    p = _OrphanProducer()

    def noop(_): pass

    bag = _enemy_snap(uid=800, name='Bag of Bugs', x=5, y=5)
    records = _bleed_stack(10, bag, 1)  # buff_tick + EventOnDamaged
    records.append(_death_rec(12, 10, bag))
    fly1 = _enemy_snap(uid=801, name='Fly', x=5, y=6)
    fly2 = _enemy_snap(uid=802, name='Fly', x=6, y=5)
    records.append(_spawn_rec(13, 10, fly1))
    records.append(_spawn_rec(14, 10, fly2))
    section = p.fire(records, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    text = section[1]
    assert "Bag of Bugs (5,5) Bleed: 3 Physical, killed." in text
    assert "2 Flies spawned at (5,6), (6,5)." in text


def test_large_wave_uses_directional_locality():
    """Beyond the coord cap, locality is a top-two-direction summary, not a
    coord list."""
    p = _OrphanProducer()

    def noop(_): pass

    caster = _enemy_snap(name='Summoner', x=10, y=10)
    chain = [
        {'sequence': 10, 'parent': None, 'event_type': 'cast_begin',
         'payload': {'caster': caster,
                     'spell': {'name': 'Swarm', 'melee': False},
                     'is_player': False, 'pay_costs': True}, 'marks': []},
    ]
    # 7 bats all to the north of the wizard at (10,10).
    seq = 11
    for i in range(7):
        chain.append(_spawn_rec(seq, 10,
                     _enemy_snap(uid=900 + i, name='Bat', x=10, y=2 - (i % 3))))
        seq += 1
    section = p.fire(chain, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None, wizard_pos=(10, 10),
                     spawn_coord_cap=5)
    text = section[1]
    # Past the cap: a directional summary, not a coord list.
    assert "7 Bats spawned, north." in text
    assert "Bats spawned at (" not in text


# ---- Stage C: cloud_tick fold (R1) ----


def _cloud_tick_chain(seq, cloud_name, target, damage, dtype, dur=3):
    """A cloud_tick root + its EventOnDamaged child on a non-wizard target."""
    return [
        {'sequence': seq, 'parent': None, 'event_type': 'cloud_tick',
         'payload': {'cloud_name': cloud_name, 'x': target.get('x'),
                     'y': target.get('y'), 'duration_before_tick': dur,
                     'duration_after_tick': dur - 1}, 'marks': []},
        {'sequence': seq + 1, 'parent': seq, 'event_type': 'EventOnDamaged',
         'payload': {'target': target, 'damage': damage, 'damage_type': dtype,
                     'source_name': cloud_name}, 'marks': []},
    ]


def test_cloud_tick_on_enemy_folds_like_dot():
    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    records = _cloud_tick_chain(10, 'Storm Cloud', g, 4, 'Lightning')
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == ["Goblin (3,4) in Storm Cloud: 4 Lightning."]


def test_cloud_tick_collapses_across_targets():
    records = []
    seq = 10
    for uid, x in ((200, 3), (201, 5)):
        g = _enemy_snap(uid=uid, name='Goblin', x=x, y=4)
        records.extend(_cloud_tick_chain(seq, 'Blizzard', g, 4, 'Ice'))
        seq += 10
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert len(lines) == 1
    assert lines[0].startswith("2 Goblins")
    assert "in Blizzard: 4 Ice each" in lines[0]


def test_cloud_tick_kill_capstones():
    g = _enemy_snap(uid=200, name='Imp', x=3, y=4)
    records = _cloud_tick_chain(10, 'Storm Cloud', g, 9, 'Lightning')
    records.append(_death_rec(12, 10, g))
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == ["Imp (3,4) in Storm Cloud: 9 Lightning, killed."]


def test_cloud_tick_skips_wizard():
    """Wizard-tile cloud is crisis territory; orphan skips it."""
    records = _cloud_tick_chain(10, 'Storm Cloud', _wizard_snap(), 4, 'Lightning')
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert lines == []


# ---- Stage C: non-wizard buff-apply onset gate (R1) ----


def _buff_apply_rec(seq, parent, target, name, buff_type=2, turns=2,
                    stack_after=1, **flags):
    payload = {'target': target,
               'buff': {'name': name, 'turns_left': turns,
                        'buff_type': buff_type, 'stack_type': 0},
               'stack_count_after': stack_after}
    payload.update(flags)
    return {'sequence': seq, 'parent': parent, 'event_type': 'EventOnBuffApply',
            'payload': payload, 'marks': []}


def test_buff_apply_debuff_onset():
    p = _OrphanProducer()

    def noop(_): pass

    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    rec = _buff_apply_rec(10, None, g, 'Frozen', buff_type=2, turns=2)
    section = p.fire([rec], show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == "Goblin (3,4) Frozen, 2 turns."


def test_buff_apply_bless_says_gained():
    p = _OrphanProducer()

    def noop(_): pass

    o = _enemy_snap(uid=201, name='Ogre', x=5, y=5)
    rec = _buff_apply_rec(10, None, o, 'Haste', buff_type=1, turns=5)
    section = p.fire([rec], show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == "Ogre (5,5) gained Haste, 5 turns."


def test_buff_apply_intensity_restack_suppressed():
    """stack_count_after > 1 is an intensity re-stack; magnitude rides the DOT
    channel, so the onset gate stays silent."""
    p = _OrphanProducer()

    def noop(_): pass

    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    rec = _buff_apply_rec(10, None, g, 'Bleed', buff_type=2, stack_after=3)
    section = p.fire([rec], show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == ""


def test_buff_apply_refresh_and_silent_activate_suppressed():
    p = _OrphanProducer()

    def noop(_): pass

    g = _enemy_snap(uid=200, name='Goblin')
    refresh = _buff_apply_rec(10, None, g, 'Frozen', is_refresh=True)
    g2 = _enemy_snap(uid=201, name='Wolf')
    passive = _buff_apply_rec(11, None, g2, 'Pack Tactics',
                              is_silent_activate=True)
    section = p.fire([refresh, passive], show_coords=True,
                     movement_verbose=False, log_fn=noop, telemetry=None)
    assert section[1] == ""


def test_buff_apply_wizard_skipped():
    """Wizard buffs are crisis/digest territory."""
    p = _OrphanProducer()

    def noop(_): pass

    rec = _buff_apply_rec(10, None, _wizard_snap(), 'Blind')
    section = p.fire([rec], show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == ""


def test_buff_replace_churn_suppresses_both_fade_and_onset():
    """A same-turn fade+reapply of Blind (STACK_REPLACE) reads as no change —
    neither the fade nor the onset is spoken."""
    p = _OrphanProducer()

    def noop(_): pass

    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    fade = {'sequence': 10, 'parent': None, 'event_type': 'EventOnBuffRemove',
            'payload': {'target': g, 'buff': {'name': 'Blind'}}, 'marks': []}
    reapply = _buff_apply_rec(11, None, g, 'Blind', buff_type=2)
    section = p.fire([fade, reapply], show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == ""


def test_buff_apply_collapses_across_targets():
    p = _OrphanProducer()

    def noop(_): pass

    recs = []
    for i, x in enumerate((3, 5, 7)):
        g = _enemy_snap(uid=200 + i, name='Goblin', x=x, y=4)
        recs.append(_buff_apply_rec(10 + i, None, g, 'Frozen', turns=2))
    section = p.fire(recs, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "3 Goblins" in section[1]
    assert "Frozen, 2 turns." in section[1]


# ---- Adversarial-review regression fixes ----


def test_item_spatial_any_visible_member_in_sight():
    """Mixed-visibility collapsed group: a closer HIDDEN member must not bury a
    farther VISIBLE one — the line is in-sight, anchored on the nearest visible
    member."""
    near_hidden = {'x': 9, 'y': 9, 'can_see_wizard': False}   # dist 1, hidden
    far_seen = {'x': 4, 'y': 4, 'can_see_wizard': True}       # dist 6, seen
    item = _make_item(RANK_ENEMY_ACTION, [near_hidden, far_seen], "group.")
    in_los, dist = _item_spatial(item, 10, 10)
    assert in_los is True
    assert dist == 6  # nearest VISIBLE member, not the hidden dist-1 one


def test_b2_keeps_nonwizard_death_when_cast_hits_wizard():
    """A cast that hits the wizard (crisis-claimed) AND kills a non-wizard via a
    non-damage death must NOT swallow the death (review C1)."""
    p = _OrphanProducer()

    def noop(_): pass

    goblin = _enemy_snap(uid=300, name='Goblin', x=5, y=5)
    chain = [
        {'sequence': 10, 'parent': None, 'event_type': 'cast_begin',
         'payload': {'caster': _enemy_snap(name='Lich', x=2, y=2),
                     'spell': {'name': 'Doom', 'melee': False},
                     'is_player': False, 'pay_costs': True}, 'marks': []},
        {'sequence': 11, 'parent': 10, 'event_type': 'EventOnDamaged',
         'payload': {'target': _wizard_snap(), 'damage': 6,
                     'damage_type': 'Dark', 'source_name': 'Doom'},
         'marks': ['crisis_v1']},
        _death_rec(12, 10, goblin),  # non-wizard death, no non-wizard damage
    ]
    section = p.fire(chain, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "Goblin (5,5) died." in section[1]


def test_b2_keeps_nonwizard_spawn_when_cast_hits_wizard():
    """A cast that hits the wizard AND summons must not swallow the spawn."""
    p = _OrphanProducer()

    def noop(_): pass

    chain = [
        {'sequence': 10, 'parent': None, 'event_type': 'cast_begin',
         'payload': {'caster': _enemy_snap(name='Lich', x=2, y=2),
                     'spell': {'name': 'Dark Rite', 'melee': False},
                     'is_player': False, 'pay_costs': True}, 'marks': []},
        {'sequence': 11, 'parent': 10, 'event_type': 'EventOnDamaged',
         'payload': {'target': _wizard_snap(), 'damage': 6,
                     'damage_type': 'Dark', 'source_name': 'Dark Rite'},
         'marks': ['crisis_v1']},
        _spawn_rec(12, 10, _enemy_snap(uid=400, name='Skeleton', x=3, y=3)),
    ]
    section = p.fire(chain, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "Skeleton spawned" in section[1]


def test_wizard_only_debuff_cast_dropped():
    """A pure control-debuff cast on the wizard is crisis's; orphan must NOT
    also render 'Enemy cast Mass Blindness' (review C3)."""
    p = _OrphanProducer()

    def noop(_): pass

    chain = [
        {'sequence': 10, 'parent': None, 'event_type': 'cast_begin',
         'payload': {'caster': _enemy_snap(name='Raven Mage', x=2, y=2),
                     'spell': {'name': 'Mass Blindness', 'melee': False},
                     'is_player': False, 'pay_costs': True}, 'marks': []},
        {'sequence': 11, 'parent': 10, 'event_type': 'EventOnBuffApply',
         'payload': {'target': _wizard_snap(),
                     'buff': {'name': 'Blind', 'turns_left': 3, 'buff_type': 2},
                     'stack_count_after': 1}, 'marks': ['crisis_v1']},
    ]
    section = p.fire(chain, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert section[1] == ""


def test_frozen_break_no_double_with_fade():
    """An ambient Frozen shatter renders 'Frozen broke' only — not also
    'Frozen faded' (review: Agent A double)."""
    p = _OrphanProducer()

    def noop(_): pass

    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    records = [
        {'sequence': 10, 'parent': None, 'event_type': 'EventOnBuffRemove',
         'payload': {'target': g, 'buff': {'name': 'Frozen', 'turns_left': 0}},
         'marks': []},
        {'sequence': 11, 'parent': None, 'event_type': 'EventOnUnfrozen',
         'payload': {'target': g, 'damage_type': 'Fire'}, 'marks': []},
    ]
    section = p.fire(records, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "Frozen broke." in section[1]
    assert "faded" not in section[1]


def test_silent_activate_apply_does_not_eat_real_fade():
    """A spawn-time passive activation (is_silent_activate) of a buff must not
    form a false churn pair that suppresses an unrelated real fade of the same
    buff name (review fix #5)."""
    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    records = [
        {'sequence': 10, 'parent': None, 'event_type': 'EventOnBuffRemove',
         'payload': {'target': g, 'buff': {'name': 'Haste', 'turns_left': 0}},
         'marks': []},
        _buff_apply_rec(11, None, g, 'Haste', buff_type=1,
                        is_silent_activate=True),
    ]
    idx = _build_index(records)
    lines = _tick_lines(records, idx)
    assert "Goblin (3,4) Haste faded." in lines


# ---- Regression: EventOnBuffAttemptApply string-payload must not crash ----


def test_attempt_apply_string_payload_does_not_crash():
    """RW3's EventOnBuffAttemptApply is captured generically with STRING
    payload fields ({'buff': 'Blind', 'unit': 'Treant'}). Orphan scans all
    records (wizard-team discovery, churn detection) and must not call .get on
    those strings (was: 'str' object has no attribute 'get', dropping the whole
    ambient line on debuff-cast turns)."""
    from orphan import _find_wizard_team, _buff_churn_pairs
    p = _OrphanProducer()

    def noop(_): pass

    attempt = {'sequence': 9, 'parent': None,
               'event_type': 'EventOnBuffAttemptApply',
               'payload': {'buff': 'Blind', 'duration': 4, 'unit': 'Treant'},
               'marks': []}
    # An ordinary ambient line should still compose alongside the bad record.
    g = _enemy_snap(uid=200, name='Goblin', x=3, y=4)
    dot = _bleed_stack(10, g, 2)
    records = [attempt] + dot
    # Direct helper calls must not raise.
    assert _find_wizard_team(records) is None or isinstance(
        _find_wizard_team(records), int)
    assert isinstance(_buff_churn_pairs(records), set)
    # Full producer fire must not raise and still renders the DOT line.
    section = p.fire(records, show_coords=True, movement_verbose=False,
                     log_fn=noop, telemetry=None)
    assert "Goblin (3,4) Bleed: 3 Physical" in section[1]


# ---- Shield changes (R3): out-of-chain gains/strips on non-wizard units ----


def _su(name, team=1, tier='minion', pid=None, x=3, y=4):
    return {'id': pid, 'name': name, 'team': team, 'tier': tier,
            'is_player_controlled': False, 'x': x, 'y': y}


def _sgain(target, amount=3, marks=None):
    return {'event_type': 'shield_gained',
            'payload': {'target': target, 'amount': amount, 'shields_after': amount},
            'marks': marks or []}


def _sstrip(target, removed=2, marks=None, shields_after=None):
    payload = {'target': target, 'amount_removed': removed}
    if shields_after is not None:
        payload['shields_after'] = shields_after
    return {'event_type': 'shield_stripped', 'payload': payload,
            'marks': marks or []}


def _sblock(target, amount=12, dtype='Fire', source='Fire Bolt',
            remaining=2, marks=None):
    return {'event_type': 'shield_blocked',
            'payload': {'target': target, 'blocked_amount': amount,
                        'damage_type': dtype, 'source_name': source,
                        'shields_remaining': remaining},
            'marks': marks or []}


def _texts(records, wizard_team=0, show_coords=True, **flags):
    items, _claimed = _render_shield_changes(
        records, wizard_team, show_coords, **flags)
    return [it.get('text') for it in items]


def _block_texts(records, wizard_team=0, show_coords=True, **flags):
    items, _claimed = _render_shield_blocks(
        records, wizard_team, show_coords, **flags)
    return [it.get('text') for it in items]


def test_orphan_enemy_gain_single():
    # enemy_shield_totals defaults True → resulting count rides the line.
    assert _texts([_sgain(_su('Ogre', pid=1))]) == \
        ["Ogre (3,4) gained 3 shields, 3 total."]


def test_orphan_enemy_gain_no_total_when_off():
    assert _texts([_sgain(_su('Ogre', pid=1))], enemy_shield_totals=False) == \
        ["Ogre (3,4) gained 3 shields."]


def test_orphan_enemy_gain_collapses_each():
    recs = [_sgain(_su('Ogre', pid=1, x=3, y=3)),
            _sgain(_su('Ogre', pid=2, x=4, y=3)),
            _sgain(_su('Ogre', pid=3, x=5, y=3))]
    assert _texts(recs) == [
        "3 Ogres at (3,3), (4,3), (5,3) gained 3 shields, 3 total each."]


def test_orphan_gain_ally_prefixed_default_no_total():
    # ally_shield_totals defaults False → lean ally line.
    assert _texts([_sgain(_su('Wolf', team=0, pid=1), amount=2)]) == \
        ["Ally Wolf (3,4) gained 2 shields."]


def test_orphan_gain_ally_total_when_on():
    assert _texts([_sgain(_su('Wolf', team=0, pid=1), amount=2)],
                  ally_shield_totals=True) == \
        ["Ally Wolf (3,4) gained 2 shields, 2 total."]


def test_orphan_gain_total_splits_collapse():
    # Two Ogres gaining the same amount but ending at different totals must not
    # merge once the total is spoken.
    a = _su('Ogre', pid=1, x=3, y=3)
    b = _su('Ogre', pid=2, x=4, y=3)
    recs = [_sgain(a, amount=2), {'event_type': 'shield_gained',
            'payload': {'target': b, 'amount': 2, 'shields_after': 5},
            'marks': []}]
    out = _texts(recs)
    assert "Ogre (3,3) gained 2 shields, 2 total." in out
    assert "Ogre (4,3) gained 2 shields, 5 total." in out


def test_orphan_strip_single_and_collapse():
    assert _texts([_sstrip(_su('Goblin', pid=1))]) == \
        ["Goblin (3,4) shields stripped."]
    recs = [_sstrip(_su('Goblin', pid=1, x=1, y=1)),
            _sstrip(_su('Goblin', pid=2, x=2, y=1))]
    assert _texts(recs) == ["2 Goblins at (1,1), (2,1) shields stripped."]


def test_orphan_strip_superseded_by_block_not_rendered():
    rec = _sstrip(_su('Goblin', pid=1), marks=['superseded_by_block'])
    items, claimed = _render_shield_changes([rec], 0, True)
    assert items == []          # block owns it
    assert len(claimed) == 1    # but still claimed so nothing else renders it


def test_orphan_skips_claimed_by_other():
    rec = _sgain(_su('Ogre', pid=1), marks=['digest_v1'])
    items, claimed = _render_shield_changes([rec], 0, True)
    assert items == [] and claimed == []


def test_orphan_skips_wizard_target():
    wiz = {'id': 100, 'name': 'Wizard', 'team': 0, 'tier': 'wizard',
           'is_player_controlled': True, 'x': 10, 'y': 10}
    items, claimed = _render_shield_changes([_sgain(wiz)], 0, True)
    assert items == [] and claimed == []


def test_orphan_gain_same_name_ally_enemy_split_by_team():
    # A Dominated 'Ogre' (ally) and a hostile 'Ogre' that would otherwise share
    # a signature (both totals hidden) must NOT merge under one prefix — the
    # ally-designation rule is mandatory.
    ally = _su('Ogre', team=0, pid=1, x=3, y=3)   # wizard_team=0 → ally
    enemy = _su('Ogre', team=1, pid=2, x=4, y=3)  # enemy
    out = _texts([_sgain(ally, amount=2), _sgain(enemy, amount=2)],
                 enemy_shield_totals=False, ally_shield_totals=False)
    assert "Ally Ogre (3,3) gained 2 shields." in out
    assert "Ogre (4,3) gained 2 shields." in out
    assert len(out) == 2


def test_orphan_strip_total_when_on():
    # shields_after > 0 → ', N left' when the team's config is on.
    assert _texts([_sstrip(_su('Goblin', pid=1), shields_after=1)]) == \
        ["Goblin (3,4) shields stripped, 1 left."]
    # Fully stripped → no '0 left' noise; the bare verb says it.
    assert _texts([_sstrip(_su('Goblin', pid=1), shields_after=0)]) == \
        ["Goblin (3,4) shields stripped."]


# ---- Shield blocks (R3): non-wizard block voice (was entirely silent) ----


def test_orphan_block_single_with_total():
    # enemy default on → magnitude/type/source + remaining count.
    assert _block_texts([_sblock(_su('Ogre', pid=1))]) == \
        ["Ogre (3,4) blocked 12 Fire from Fire Bolt, 2 shields left."]


def test_orphan_block_ally_default_no_total():
    # ally default off → the block still voices (it was the silent gap), minus
    # the remaining-count tail.
    assert _block_texts([_sblock(_su('Wolf', team=0, pid=1))]) == \
        ["Ally Wolf (3,4) blocked 12 Fire from Fire Bolt."]


def test_orphan_block_ally_total_when_on():
    assert _block_texts([_sblock(_su('Wolf', team=0, pid=1))],
                        ally_shield_totals=True) == \
        ["Ally Wolf (3,4) blocked 12 Fire from Fire Bolt, 2 shields left."]


def test_orphan_block_last_shield():
    assert _block_texts([_sblock(_su('Ogre', pid=1), remaining=0)]) == \
        ["Ogre (3,4) blocked 12 Fire from Fire Bolt, last shield."]


def test_orphan_block_collapses():
    recs = [_sblock(_su('Ogre', pid=1, x=3, y=3), source='Goblin'),
            _sblock(_su('Ogre', pid=2, x=4, y=3), source='Goblin')]
    assert _block_texts(recs) == [
        "2 Ogres at (3,3), (4,3) blocked 12 Fire from Goblin, "
        "2 shields left each."]


def test_orphan_block_same_name_ally_enemy_split_by_team():
    ally = _su('Ogre', team=0, pid=1, x=3, y=3)
    enemy = _su('Ogre', team=1, pid=2, x=4, y=3)
    out = _block_texts([_sblock(ally), _sblock(enemy)],
                       enemy_shield_totals=False, ally_shield_totals=False)
    assert "Ally Ogre (3,3) blocked 12 Fire from Fire Bolt." in out
    assert "Ogre (4,3) blocked 12 Fire from Fire Bolt." in out
    assert len(out) == 2


def test_orphan_block_skips_wizard_and_claimed():
    wiz = {'id': 100, 'name': 'Wizard', 'team': 0, 'tier': 'wizard',
           'is_player_controlled': True, 'x': 10, 'y': 10}
    assert _block_texts([_sblock(wiz)]) == []
    claimed_rec = _sblock(_su('Ogre', pid=1), marks=['digest_v1'])
    items, claimed = _render_shield_blocks([claimed_rec], 0, True)
    assert items == [] and claimed == []


# ---- Team flips (R2): ambient conversions on non-wizard units ----


def _tj_o(target, marks=None):
    # enemy -> player
    return {'event_type': 'team_joined',
            'payload': {'target': target, 'team_before': 1, 'team_after': 0},
            'marks': marks or []}


def _tt_o(target, marks=None):
    # player -> enemy
    return {'event_type': 'team_turned',
            'payload': {'target': target, 'team_before': 0, 'team_after': 1},
            'marks': marks or []}


def _team_texts(records, wizard_team=0, show_coords=True):
    items, _claimed = _render_team_changes(records, wizard_team, show_coords)
    return [it.get('text') for it in items]


def test_orphan_team_turned_single_no_prefix():
    assert _team_texts([_tt_o(_su('Wolf', team=1, pid=1))]) == \
        ["Wolf (3,4) turned hostile."]


def test_orphan_team_joined_single_no_prefix():
    # even though the unit is now player-team, no "Ally" prefix — the
    # disposition carries the allegiance.
    assert _team_texts([_tj_o(_su('Ogre', team=0, pid=1))]) == \
        ["Ogre (3,4) turned friendly."]


def test_orphan_team_collapses_by_name():
    recs = [_tt_o(_su('Wolf', team=1, pid=1, x=3, y=4)),
            _tt_o(_su('Wolf', team=1, pid=2, x=4, y=4))]
    assert _team_texts(recs) == ["2 Wolves at (3,4), (4,4) turned hostile."]


def test_orphan_team_skips_claimed_by_digest():
    rec = _tj_o(_su('Ogre', pid=1), marks=['digest_v1'])
    items, claimed = _render_team_changes([rec], 0, True)
    assert items == [] and claimed == []


def test_orphan_team_skips_wizard():
    wiz = {'id': 100, 'name': 'Wizard', 'team': 0, 'tier': 'wizard',
           'is_player_controlled': True, 'x': 10, 'y': 10}
    items, _ = _render_team_changes([_tj_o(wiz)], 0, True)
    assert items == []


# ---- Channel + blocked-fragment rendering (2026-07-03 session) ----


def _channel_start_chain(seq_start, caster, spell_name, melee=True):
    """Chain for a channel START: the cast applies the Channeling buff to
    the caster and deals nothing this turn (ScuttlerPinch shape)."""
    return [
        {
            'sequence': seq_start, 'parent': None,
            'event_type': 'cast_begin',
            'payload': {
                'caster': caster,
                'spell': {'name': spell_name, 'melee': melee,
                          'cur_charges': 1, 'max_charges': 1},
                'is_player': False, 'pay_costs': True,
            },
            'marks': [],
        },
        {
            'sequence': seq_start + 1, 'parent': seq_start,
            'event_type': 'EventOnBuffApply',
            'payload': {
                'target': caster,
                'buff': {'id': 1, 'name': 'Channeling', 'turns_left': 2,
                         'stack_type': 0, 'buff_type': 1},
                'stack_count_after': 1,
            },
            'marks': [],
        },
    ]


def test_channel_continuation_melee_renders_channeled_verb():
    """Continuation chains carry the real spell name (journal unwraps the
    bound method) and speak the game's verb: never 'cast attack'."""
    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Scuttler', x=6, y=3),
        'Pinch', _ally_snap(), damage=5, dtype='Physical', melee=True,
    )
    chain[0]['payload']['is_channel_continuation'] = True
    line = _render_action_chain(chain, wizard_team=0, show_coords=True,
                                movement_verbose=False)
    assert line == ("Scuttler (6,3) channeled Pinch, hit Ally Goatia (8,8),"
                    " 5 Physical.")


def test_channel_continuation_nonmelee_renders_channeled_at():
    chain = _enemy_cast_chain(
        10, _enemy_snap(name='Gazer', x=6, y=3),
        'Eye Beam', _ally_snap(), damage=4, dtype='Arcane', melee=False,
    )
    chain[0]['payload']['is_channel_continuation'] = True
    line = _render_action_chain(chain, wizard_team=0, show_coords=True,
                                movement_verbose=False)
    assert line == ("Gazer (6,3) channeled Eye Beam at Ally Goatia (8,8),"
                    " 4 Arcane.")


def test_channel_start_renders_began_channeling():
    """The start cast (no damage, Channeling applied to the caster) reads
    'began channeling {spell}', not the bare melee 'attacked.'."""
    caster = _enemy_snap(name='Scuttler', x=6, y=3)
    chain = _channel_start_chain(10, caster, 'Pinch', melee=True)
    line = _render_action_chain(chain, wizard_team=0, show_coords=True,
                                movement_verbose=False)
    assert line == "Scuttler (6,3) began channeling Pinch."


def test_blocked_melee_chain_suppresses_bare_attack_line():
    """A melee whose only outcome was a shield block renders None — the
    blocked section is the canonical voice (mirrors the game log, which
    shows DMG_BLOCKED and no separate attack line)."""
    caster = _enemy_snap(name='Scuttler', x=6, y=3)
    ally = _ally_snap(uid=300, name='Sword of Light', x=5, y=10)
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'cast_begin',
            'payload': {
                'caster': caster,
                'spell': {'name': 'Pinch', 'melee': True,
                          'cur_charges': 1, 'max_charges': 1},
                'is_player': False, 'pay_costs': True,
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'shield_blocked',
            'payload': {
                'target': ally, 'blocked_amount': 5,
                'damage_type': 'Physical', 'source_name': 'Pinch',
                'shields_remaining': 1,
            },
            'marks': [],
        },
    ]
    line = _render_action_chain(chain, wizard_team=0, show_coords=True,
                                movement_verbose=False)
    assert line is None


def test_buff_section_skips_channeling_rendered_on_action_line():
    """An orphan-marked Channeling apply (its action chain rendered
    'began channeling X') must not double as 'gained Channeling'. An
    unmarked one (unrendered chain) keeps its buff line."""
    from orphan import _render_buff_applies
    caster = _enemy_snap(name='Scuttler', x=6, y=3)
    marked = {
        'sequence': 11, 'parent': 10, 'event_type': 'EventOnBuffApply',
        'payload': {
            'target': caster,
            'buff': {'id': 1, 'name': 'Channeling', 'turns_left': 2,
                     'stack_type': 0, 'buff_type': 1},
            'stack_count_after': 1,
        },
        'marks': [ORPHAN_MARK],
    }
    items, _claimed = _render_buff_applies([marked], 0, True)
    assert items == []

    unmarked = dict(marked)
    unmarked['marks'] = []
    items, _claimed = _render_buff_applies([unmarked], 0, True)
    assert [it['text'] for it in items] == [
        "Scuttler (6,3) gained Channeling, 2 turns."]


# ---- Repetition is not multiplicity (2026-07-03 grouping/dedup session) ----


def test_aoe_same_unit_multi_hit_renders_hits_not_units():
    """One blade hit three times must not read as three blades at one
    coordinate ("3 Ally Dancing Blades at (3,8), (3,8), (3,8)" specimen)."""
    caster = _enemy_snap(name='Goblin Mage', x=6, y=3)
    blade = _ally_snap(uid=300, name='Dancing Blade', x=3, y=8)
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'cast_begin',
            'payload': {
                'caster': caster,
                'spell': {'name': 'Volley', 'melee': False,
                          'cur_charges': 1, 'max_charges': 1},
                'is_player': False, 'pay_costs': True,
            },
            'marks': [],
        },
    ]
    for i in range(3):
        chain.append({
            'sequence': 11 + i, 'parent': 10,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': blade, 'damage': 5, 'damage_type': 'Physical',
                'source_name': 'Volley',
            },
            'marks': [],
        })
    line = _render_action_chain(chain, wizard_team=0, show_coords=True,
                                movement_verbose=False)
    assert line == ("Goblin Mage (6,3) cast Volley at Ally Dancing Blade"
                    " (3,8), 3 hits, 5 Physical each.")


def test_blocked_same_unit_twice_renders_hits_not_units():
    """One blade blocking two same-signature hits must not read as two
    blades ("2 Ally Sword of Lights at (5,10), (5,10) blocked" specimen —
    ally lines merge because ally shields-left is config-off)."""
    from orphan import _render_shield_blocks
    blade = _ally_snap(uid=300, name='Sword of Light', x=5, y=10)
    recs = []
    for i in range(2):
        recs.append({
            'sequence': 10 + i, 'parent': None,
            'event_type': 'shield_blocked',
            'payload': {
                'target': blade, 'blocked_amount': 5,
                'damage_type': 'Physical', 'source_name': 'Pinch',
                'shields_remaining': 1 - i,
            },
            'marks': [],
        })
    items, _claimed = _render_shield_blocks(recs, 0, True,
                                            ally_shield_totals=False,
                                            enemy_shield_totals=True)
    assert [it['text'] for it in items] == [
        "Ally Sword of Light (5,10) blocked 2 hits, 5 Physical each"
        " from Pinch."]


def test_fade_same_unit_multi_stack_speaks_once():
    """Multi-stack fades on one unit collapse to one line (the
    'Necrosis faded' x3 sibling — minion side)."""
    ogre = _enemy_snap(uid=400, name='Ogre', x=3, y=3)
    recs = []
    for i in range(3):
        recs.append({
            'sequence': 10 + i, 'parent': None,
            'event_type': 'EventOnBuffRemove',
            'payload': {
                'target': ogre,
                'buff': {'id': 7, 'name': 'Necrosis', 'turns_left': 0,
                         'stack_type': 0, 'buff_type': 2},
            },
            'marks': [],
        })
    idx = _build_index(recs)
    lines = _tick_lines(recs, idx)
    assert lines == ["Ogre (3,3) Necrosis faded."]


def test_shield_gain_same_unit_twice_renders_times():
    """One unit gaining the same shield amount twice reads as repetition,
    not two units."""
    ogre = _enemy_snap(uid=400, name='Ogre', x=3, y=3)
    recs = []
    for i in range(2):
        recs.append({
            'sequence': 10 + i, 'parent': None,
            'event_type': 'shield_gained',
            'payload': {'target': ogre, 'amount': 1, 'shields_after': None},
            'marks': [],
        })
    items, _claimed = _render_shield_changes(recs, 0, True,
                                             ally_shield_totals=False,
                                             enemy_shield_totals=False)
    assert [it['text'] for it in items] == [
        "Ogre (3,3) gained 1 shield, 2 times."]
