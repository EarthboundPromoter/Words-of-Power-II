# Tests for equipment.py producer.
# Run with: cd ~ && python -m pytest "<path_to_mod>/tests/test_equipment.py" -v

import sys
import os

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from equipment import (
    EQUIPMENT_MARK,
    PRIORITY_PLAYER_PASSIVES,
    _render_equipment_chain,
    _compose_resisted_section,
    _EquipmentProducer,
)


# ---- Fixtures (mirrored from test_orphan.py for these tests) ----


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


# ---- _render_equipment_chain ----


def test_equipment_chain_debuff_apply():
    """Stone Mask applies Petrified to an enemy. Renders as
    'Stone Mask petrified Aelf (3,4), 3 turns.'"""
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'Stone Mask', 'turns_left': 0,
                         'stack_type': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'EventOnBuffApply',
            # 'Petrify' (not past-tense) — the verb-form rendering used to
            # produce "Stone Mask petrify Aelf"; the "applied {Name} to" form
            # reads grammatically for any buff name.
            'payload': {
                'target': _enemy_snap(name='Aelf', x=3, y=4),
                'buff': {'name': 'Petrify', 'turns_left': 3,
                         'stack_type': 0, 'buff_type': 2},
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == ["Stone Mask applied Petrify to Aelf (3,4), 3 turns."]


def test_equipment_chain_damage_to_enemies():
    """A damage-aura equipment hits 3 enemies of same type for same dmg."""
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'DamageAura', 'turns_left': 0,
                         'stack_type': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
    ]
    for i in range(3):
        chain.append({
            'sequence': 11 + i, 'parent': 10,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': _enemy_snap(uid=300 + i, name='Goblin',
                                      x=3 + i, y=4),
                'damage': 2, 'damage_type': 'Fire',
                'source_name': 'DamageAura',
            },
            'marks': [],
        })
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == [
        "DamageAura hit 3 Goblins at (3,4), (4,4), (5,4), 2 Fire each."
    ]


def test_equipment_chain_skips_wizard_target_damage():
    """Damage on wizard from equipment is crisis-claimed; orphan skips."""
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'CursedAura', 'turns_left': 0,
                         'stack_type': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': _wizard_snap(),
                'damage': 2, 'damage_type': 'Dark',
                'source_name': 'CursedAura',
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == []


def test_equipment_chain_subcast_attribution():
    """Equipment that fires a sub-cast renders in digest-style multi-
    section form. Both layers of attribution preserved (equipment name
    AND spell it cast); damage outcomes go through the digest's
    Surviving section."""
    target = _enemy_snap(name='Goblin', x=3, y=4)
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'Explosive Spore Manual', 'turns_left': 0,
                         'stack_type': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'cast_begin',
            'payload': {
                'caster': _wizard_snap(),
                'spell': {'name': 'Combust Poison', 'melee': False,
                          'cur_charges': 0, 'max_charges': 0},
                'is_player': True, 'pay_costs': False, 'is_echo': True,
            },
            'marks': [],
        },
        {
            'sequence': 12, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': target,
                'damage_pre_resist': 5, 'damage_post_resist': 5,
                'resisted': False, 'damage_type': 'Poison',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
        {
            'sequence': 13, 'parent': 11,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': target,
                'damage': 5, 'damage_type': 'Poison',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == [
        "Explosive Spore Manual cast Combust Poison."
        " 1 surviving: Goblin (3,4): Combust Poison 5 Poison."
    ]


def test_equipment_chain_subcast_killed_verb():
    """When a sub-cast damage event has a corresponding EventOnDeath in
    the same chain, the verb flips from 'hit' to 'killed'."""
    target = _enemy_snap(uid=200, name='Orc', x=13, y=5)
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'Explosive Spore Manual', 'turns_left': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'cast_begin',
            'payload': {
                'caster': _wizard_snap(),
                'spell': {'name': 'Combust Poison', 'melee': False},
                'is_player': True, 'pay_costs': False, 'is_echo': False,
            },
            'marks': [],
        },
        {
            'sequence': 12, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': target,
                'damage_pre_resist': 30, 'damage_post_resist': 30,
                'resisted': False,
                'damage_type': 'Fire',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
        {
            'sequence': 13, 'parent': 11,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': target,
                'damage': 2, 'damage_type': 'Fire',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
        {
            'sequence': 14, 'parent': 11,
            'event_type': 'EventOnDeath',
            'payload': {'target': target},
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    # Killed section count-led header. The kill-line damage is the CLAMPED
    # actually-dealt amount (EventOnDamaged.damage = 2), matching the game's
    # combat log, not the pre-clamp post-resist 30.
    assert lines == [
        "Explosive Spore Manual cast Combust Poison."
        " 1 killed: Orc (13,5): Combust Poison 2 Fire."
    ]


def test_equipment_chain_subcast_full_resist_renders():
    """An EventOnPreDamaged with resisted=True and damage_post_resist=0
    has no following EventOnDamaged. The renderer surfaces this as a
    'X resisted' line so the listener still hears the equipment fired
    against that target."""
    resister = _enemy_snap(uid=200, name='Hell Hound', x=15, y=5)
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'Explosive Spore Manual', 'turns_left': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'cast_begin',
            'payload': {
                'caster': _wizard_snap(),
                'spell': {'name': 'Combust Poison', 'melee': False},
                'is_player': True, 'pay_costs': False, 'is_echo': False,
            },
            'marks': [],
        },
        {
            'sequence': 12, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': resister,
                'damage_pre_resist': 30, 'damage_post_resist': 0,
                'resisted': True,
                'damage_type': 'Fire',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    # Resisted single-target drops count header (matches debuffs/buffs
    # convention). Bare line "Hell Hound (15,5) resisted." reads cleanly
    # — listener identifies the section by the trailing verb.
    assert lines == [
        "Explosive Spore Manual cast Combust Poison."
        " Hell Hound (15,5) resisted."
    ]


def test_equipment_chain_subcast_immune_renders_with_immune_verb():
    """When target_resist_pct >= 100, the renderer says 'immune'
    instead of 'resisted'. Distinguishes structural immunity (Skeleton's
    100% Lightning resist) from rounding-to-zero high-but-not-immune
    resists."""
    immune_target = _enemy_snap(uid=200, name='Skeleton', x=15, y=5)
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'Explosive Spore Manual', 'turns_left': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'cast_begin',
            'payload': {
                'caster': _wizard_snap(),
                'spell': {'name': 'Combust Poison', 'melee': False},
                'is_player': True, 'pay_costs': False, 'is_echo': False,
            },
            'marks': [],
        },
        {
            'sequence': 12, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': immune_target,
                'damage_pre_resist': 30, 'damage_post_resist': 0,
                'resisted': True, 'damage_type': 'Lightning',
                'source_name': 'Combust Poison',
                'target_resist_pct': 100,  # immune to Lightning
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == [
        "Explosive Spore Manual cast Combust Poison."
        " Skeleton (15,5) immune."
    ]


def test_equipment_chain_subcast_mixed_kills_resists():
    """The user's reported case: Combust Poison cascade with mixed
    outcomes — kills, hits-without-kill, and full-resists. All should
    render with appropriate verbs in the correct lines."""
    killed_orc_a = _enemy_snap(uid=200, name='Orc', x=13, y=5)
    killed_orc_b = _enemy_snap(uid=201, name='Orc', x=14, y=5)
    resister = _enemy_snap(uid=202, name='Hell Hound', x=15, y=5)
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'Explosive Spore Manual', 'turns_left': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'cast_begin',
            'payload': {
                'caster': _wizard_snap(),
                'spell': {'name': 'Combust Poison', 'melee': False},
                'is_player': True, 'pay_costs': False, 'is_echo': False,
            },
            'marks': [],
        },
        # Orc A: damage 2 Fire, dies.
        {
            'sequence': 12, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': killed_orc_a,
                'damage_pre_resist': 30, 'damage_post_resist': 30,
                'resisted': False, 'damage_type': 'Fire',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
        {
            'sequence': 13, 'parent': 11,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': killed_orc_a, 'damage': 2,
                'damage_type': 'Fire', 'source_name': 'Combust Poison',
            },
            'marks': [],
        },
        {
            'sequence': 14, 'parent': 11,
            'event_type': 'EventOnDeath',
            'payload': {'target': killed_orc_a},
            'marks': [],
        },
        # Orc B: damage 20 Fire, dies.
        {
            'sequence': 15, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': killed_orc_b,
                'damage_pre_resist': 30, 'damage_post_resist': 30,
                'resisted': False, 'damage_type': 'Fire',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
        {
            'sequence': 16, 'parent': 11,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': killed_orc_b, 'damage': 20,
                'damage_type': 'Fire', 'source_name': 'Combust Poison',
            },
            'marks': [],
        },
        {
            'sequence': 17, 'parent': 11,
            'event_type': 'EventOnDeath',
            'payload': {'target': killed_orc_b},
            'marks': [],
        },
        # Hell Hound: full resist, no Damaged event.
        {
            'sequence': 18, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': resister,
                'damage_pre_resist': 30, 'damage_post_resist': 0,
                'resisted': True, 'damage_type': 'Fire',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    # The Orcs share the same NOMINAL hit (post-resist 30) but took DIFFERENT
    # actual damage — Orc A had 2 HP left (dealt 2), Orc B had 20 (dealt 20).
    # Since the kill line speaks the clamped actually-dealt amount, they no
    # longer merge: each renders with its own damage. Hell Hound full-resisted.
    assert lines == [
        "Explosive Spore Manual cast Combust Poison."
        " 2 killed: Orc (13,5): Combust Poison 2 Fire."
        " Orc (14,5): Combust Poison 20 Fire."
        " Hell Hound (15,5) resisted."
    ]


def test_equipment_chain_subcast_multi_target_collapse():
    """Sub-cast hits 3 same-name same-damage targets that all survive.
    Digest-style multi-section form collapses them in the Surviving
    section."""
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'Explosive Spore Manual', 'turns_left': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'cast_begin',
            'payload': {
                'caster': _wizard_snap(),
                'spell': {'name': 'Combust Poison', 'melee': False},
                'is_player': True, 'pay_costs': False, 'is_echo': True,
            },
            'marks': [],
        },
    ]
    seq = 12
    for i in range(3):
        target = _enemy_snap(uid=300 + i, name='Goblin', x=3 + i, y=4)
        chain.append({
            'sequence': seq, 'parent': 11,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': target,
                'damage_pre_resist': 5, 'damage_post_resist': 5,
                'resisted': False, 'damage_type': 'Poison',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        })
        chain.append({
            'sequence': seq + 1, 'parent': 11,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': target,
                'damage': 5, 'damage_type': 'Poison',
                'source_name': 'Combust Poison',
            },
            'marks': [],
        })
        seq += 2
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == [
        "Explosive Spore Manual cast Combust Poison."
        " 3 surviving: 3 Goblins at (3,4), (4,4), (5,4): Combust Poison 5 Poison."
    ]


def test_equipment_chain_no_subcast_keeps_direct_attribution():
    """DamageAura type — no sub-cast, equipment hits directly. Render
    keeps the existing form: 'DamageAura hit Goblin (3,4), 2 Fire.' (no
    'cast' clause)."""
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'DamageAura', 'turns_left': 0,
                         'stack_type': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': _enemy_snap(name='Goblin', x=3, y=4),
                'damage': 2, 'damage_type': 'Fire',
                'source_name': 'DamageAura',
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == ["DamageAura hit Goblin (3,4), 2 Fire."]


def test_equipment_chain_heal_on_ally():
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'HealAura', 'turns_left': 0,
                         'stack_type': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
        {
            'sequence': 11, 'parent': 10,
            'event_type': 'EventOnHealed',
            'payload': {
                'target': _ally_snap(name='Goatia', x=8, y=8),
                'heal_amount': 2,
                'source_name': 'HealAura',
            },
            'marks': [],
        },
    ]
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == ["HealAura healed Ally Goatia (8,8), 2 HP."]




def test_equipment_damage_same_unit_multi_hit_renders_hits():
    """One unit hit twice by an equipment tick reads as repetition, not
    two units (2026-07-03 grouping/dedup session)."""
    chain = [
        {
            'sequence': 10, 'parent': None,
            'event_type': 'equipment_tick',
            'payload': {
                'buff': {'name': 'DamageAura', 'turns_left': 0,
                         'stack_type': 0},
                'owner': _wizard_snap(),
            },
            'marks': [],
        },
    ]
    for i in range(2):
        chain.append({
            'sequence': 11 + i, 'parent': 10,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': _enemy_snap(uid=300, name='Goblin', x=3, y=4),
                'damage': 2, 'damage_type': 'Fire',
                'source_name': 'DamageAura',
            },
            'marks': [],
        })
    lines = _render_equipment_chain(chain, wizard_team=0, show_coords=True)
    assert lines == ["DamageAura hit Goblin (3,4), 2 hits, 2 Fire each."]
