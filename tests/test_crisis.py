# Tests for crisis.py producer.
# Run with: cd ~ && python -m pytest "<path_to_mod>/tests/test_crisis.py" -v

import sys
import os

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from crisis import (
    CRISIS_MARK,
    PRIORITY_CRITICAL,
    _claim,
    _current_threshold_label,
    _has_crisis_mark,
    _is_wizard_snap,
    _render_buff_applied,
    _render_buff_faded,
    _render_cloud_on_wizard,
    _render_damage_taken,
    _render_displaced,
    _render_wizard_death,
    _attacker_phrase,
    _render_wizard_shield_blocked,
    _render_wizard_shield_stripped,
    _render_wizard_shield_gained,
    _render_wizard_healed,
    _render_wizard_lethal_save,
    _render_wizard_buff_gained,
    _CrisisProducer,
)


# ---- Fixtures ----


def _wizard_snap(x=10, y=10, cur_hp=50, max_hp=50):
    return {
        'id': 100, 'name': 'Wizard', 'x': x, 'y': y,
        'cur_hp': cur_hp, 'max_hp': max_hp,
        'team': 0, 'tier': 'wizard',
        'is_player_controlled': True,
        'is_boss': False, 'is_lair': False, 'parent_id': None,
    }


def _enemy_snap(name="Aelf", x=5, y=5, cur_hp=18, max_hp=18, team=1):
    return {
        'id': 200, 'name': name, 'x': x, 'y': y,
        'cur_hp': cur_hp, 'max_hp': max_hp,
        'team': team, 'tier': 'minion',
        'is_player_controlled': False,
        'is_boss': False, 'is_lair': False, 'parent_id': None,
    }


def _damage_record(target, damage=30, dtype='Lightning', source='Aelf'):
    return {
        'sequence': 10, 'parent': None,
        'event_type': 'EventOnDamaged',
        'payload': {
            'target': target, 'damage': damage,
            'damage_type': dtype, 'source_name': source,
        },
        'marks': [],
    }


def _buff_record(event_type, target, name='Petrified', buff_type=2,
                 turns_left=3, sequence=20):
    return {
        'sequence': sequence, 'parent': None,
        'event_type': event_type,
        'payload': {
            'target': target,
            'buff': {
                'name': name, 'buff_type': buff_type,
                'turns_left': turns_left,
            },
        },
        'marks': [],
    }


# ---- Helper predicate tests ----


def test_is_wizard_snap_true_for_player():
    assert _is_wizard_snap(_wizard_snap()) is True


def test_is_wizard_snap_false_for_enemy():
    assert _is_wizard_snap(_enemy_snap()) is False


def test_is_wizard_snap_false_for_none():
    assert _is_wizard_snap(None) is False


def test_claim_idempotent():
    rec = {'marks': []}
    _claim(rec)
    _claim(rec)
    assert rec['marks'] == [CRISIS_MARK]


def test_has_crisis_mark():
    rec = {'marks': [CRISIS_MARK]}
    assert _has_crisis_mark(rec) is True
    rec2 = {'marks': []}
    assert _has_crisis_mark(rec2) is False


# ---- _render_damage_taken ----


def test_damage_taken_wizard_with_source():
    rec = _damage_record(_wizard_snap(), damage=30,
                          dtype='Lightning', source='Aelf')
    assert _render_damage_taken(rec) == "Wizard took 30 Lightning from Aelf."


def test_damage_taken_wizard_no_dtype():
    rec = _damage_record(_wizard_snap(), damage=30,
                          dtype=None, source='Aelf')
    assert _render_damage_taken(rec) == "Wizard took 30 from Aelf."


def test_damage_taken_wizard_no_source():
    rec = _damage_record(_wizard_snap(), damage=30,
                          dtype='Fire', source=None)
    assert _render_damage_taken(rec) == "Wizard took 30 Fire."


def test_damage_taken_skips_enemy_target():
    rec = _damage_record(_enemy_snap(), damage=30)
    assert _render_damage_taken(rec) is None


def test_damage_taken_skips_zero_damage():
    rec = _damage_record(_wizard_snap(), damage=0)
    assert _render_damage_taken(rec) is None


def test_damage_taken_skips_non_damage_event():
    rec = {'event_type': 'EventOnHealed', 'payload': {}}
    assert _render_damage_taken(rec) is None


# ---- attacker naming on damage taken (_attacker_phrase) ----


def _dmg_payload(source='Storm Breath', owner='Storm Drake',
                 is_buff=False, buff_type=None, damage=8, dtype='Lightning'):
    return {
        'target': _wizard_snap(), 'damage': damage, 'damage_type': dtype,
        'source_name': source, 'source_owner_name': owner,
        'source_is_buff': is_buff, 'source_buff_type': buff_type,
    }


def _dmg_rec(payload, sequence=1):
    return {'sequence': sequence, 'parent': None,
            'event_type': 'EventOnDamaged', 'payload': payload, 'marks': []}


def test_attacker_named_attack_uses_possessive():
    assert _render_damage_taken(_dmg_rec(_dmg_payload())) == \
        "Wizard took 8 Lightning from Storm Drake's Storm Breath."


def test_attacker_generic_melee_names_attacker_only():
    payload = _dmg_payload(source='Melee Attack', owner='Goblin',
                           damage=6, dtype='Physical')
    assert _render_damage_taken(_dmg_rec(payload)) == \
        "Wizard took 6 Physical from Goblin."


def test_attacker_dot_temp_buff_names_source_only():
    # Bleed (curse buff) ticking on the wizard: owner is the victim, so name
    # the buff, never "Wizard's Bleed".
    payload = _dmg_payload(source='Bleed', owner='Wizard', is_buff=True,
                           buff_type=2, damage=3, dtype='Physical')
    assert _render_damage_taken(_dmg_rec(payload)) == \
        "Wizard took 3 Physical from Bleed."


def test_attacker_no_owner_unchanged():
    # Legacy/ownerless record (no source_owner_name) -> original phrasing.
    rec = _damage_record(_wizard_snap(), damage=4, dtype='Physical',
                         source='Spikes')
    assert _render_damage_taken(rec) == "Wizard took 4 Physical from Spikes."


# ---- shield block / strip / gain (R3) ----


def _blocked_record(target, amount=12, dtype='Fire', source='Fire Bolt',
                    remaining=2, marks=None, sequence=30):
    return {'sequence': sequence, 'parent': None,
            'event_type': 'shield_blocked',
            'payload': {'target': target, 'blocked_amount': amount,
                        'damage_type': dtype, 'source_name': source,
                        'shields_remaining': remaining},
            'marks': marks or []}


def _stripped_record(target, after=1, marks=None, sequence=30):
    return {'sequence': sequence, 'parent': None,
            'event_type': 'shield_stripped',
            'payload': {'target': target, 'amount_removed': 1,
                        'shields_after': after},
            'marks': marks or []}


def _gained_record(target, amount=2, after=3, sequence=30):
    return {'sequence': sequence, 'parent': None,
            'event_type': 'shield_gained',
            'payload': {'target': target, 'amount': amount, 'shields_after': after},
            'marks': []}


def test_shield_blocked_full():
    assert _render_wizard_shield_blocked(_blocked_record(_wizard_snap())) == \
        "Wizard blocked 12 Fire from Fire Bolt, 2 shields left."


def test_shield_blocked_singular_remaining():
    assert _render_wizard_shield_blocked(
        _blocked_record(_wizard_snap(), remaining=1)) == \
        "Wizard blocked 12 Fire from Fire Bolt, 1 shield left."


def test_shield_blocked_last_shield():
    assert _render_wizard_shield_blocked(
        _blocked_record(_wizard_snap(), remaining=0)) == \
        "Wizard blocked 12 Fire from Fire Bolt, last shield."


def test_shield_blocked_non_wizard_ignored():
    assert _render_wizard_shield_blocked(_blocked_record(_enemy_snap())) is None


def test_shield_stripped_with_remaining():
    assert _render_wizard_shield_stripped(
        _stripped_record(_wizard_snap(), after=2)) == \
        "Wizard shields stripped, 2 shields left."


def test_shield_stripped_all():
    assert _render_wizard_shield_stripped(
        _stripped_record(_wizard_snap(), after=0)) == "Wizard shields stripped."


def test_shield_stripped_superseded_by_block_ignored():
    rec = _stripped_record(_wizard_snap(), after=1, marks=['superseded_by_block'])
    assert _render_wizard_shield_stripped(rec) is None


def test_shield_gained_with_total():
    assert _render_wizard_shield_gained(_gained_record(_wizard_snap())) == \
        "Wizard gained 2 shields, 3 total."


def test_shield_gained_singular():
    assert _render_wizard_shield_gained(
        _gained_record(_wizard_snap(), amount=1, after=1)) == \
        "Wizard gained 1 shield, 1 total."


def test_shield_gained_non_wizard_ignored():
    assert _render_wizard_shield_gained(_gained_record(_enemy_snap())) is None


# ---- _render_wizard_healed ----


def _heal_record(target, amount=5, source='Regeneration', sequence=40,
                 parent=None):
    return {'sequence': sequence, 'parent': parent,
            'event_type': 'EventOnHealed',
            'payload': {'target': target, 'heal_amount': amount,
                        'source_name': source}, 'marks': []}


def test_wizard_healed_with_source():
    assert _render_wizard_healed(_heal_record(_wizard_snap())) == \
        "Wizard healed 5 from Regeneration."


def test_wizard_healed_no_source():
    assert _render_wizard_healed(_heal_record(_wizard_snap(), source=None)) == \
        "Wizard healed 5."


def test_wizard_healed_zero_ignored():
    assert _render_wizard_healed(_heal_record(_wizard_snap(), amount=0)) is None


def test_wizard_healed_non_wizard_ignored():
    assert _render_wizard_healed(_heal_record(_enemy_snap())) is None


# ---- _render_wizard_lethal_save (R5, interim; data-driven) ----


def _silent_heal_record(target, cur_before=0, cur_after=50, max_hp_after=50,
                        sequence=40):
    heal = cur_after - cur_before
    return {'sequence': sequence, 'parent': None,
            'event_type': 'silent_heal',
            'payload': {'target': target, 'heal_amount': heal,
                        'cur_hp_before': cur_before, 'cur_hp_after': cur_after,
                        'max_hp_after': max_hp_after,
                        'source_name': None}, 'marks': []}


def test_lethal_save_restored_to_full():
    # cur_hp 0 -> 50 (== max): "restored to full", reporting the max.
    rec = _silent_heal_record(_wizard_snap(), cur_before=0, cur_after=50, max_hp_after=50)
    assert _render_wizard_lethal_save(rec) == \
        "You would have died — restored to full, 50 health."


def test_lethal_save_survived_at_one():
    # Soulbound clamp: cur_hp -3 -> 1 (< max): "survived at 1 health".
    rec = _silent_heal_record(_wizard_snap(), cur_before=-3, cur_after=1, max_hp_after=50)
    assert _render_wizard_lethal_save(rec) == \
        "You would have died — survived at 1 health."


def test_ordinary_silent_heal_not_a_lethal_save():
    # Ruby Heart / components: cur_hp_before > 0 -> NOT a lethal save -> inert.
    rec = _silent_heal_record(_wizard_snap(), cur_before=20, cur_after=75, max_hp_after=75)
    assert _render_wizard_lethal_save(rec) is None


def test_lethal_save_non_wizard_ignored():
    rec = _silent_heal_record(_enemy_snap(), cur_before=0, cur_after=1, max_hp_after=18)
    assert _render_wizard_lethal_save(rec) is None


def test_lethal_save_voiced_through_fire():
    p = _CrisisProducer()
    def noop(_): pass
    rec = _silent_heal_record(_wizard_snap(), cur_before=0, cur_after=50, max_hp_after=50)
    section = p.fire([rec], _StubWizard(50, 50), noop, telemetry=None)
    assert "You would have died — restored to full, 50 health." in section[1]
    assert _has_crisis_mark(rec)


def test_ordinary_silent_heal_inert_through_fire():
    # An ordinary wizard silent heal (cur_before > 0) stays inert in the interim.
    p = _CrisisProducer()
    def noop(_): pass
    rec = _silent_heal_record(_wizard_snap(), cur_before=20, cur_after=50, max_hp_after=50)
    section = p.fire([rec], _StubWizard(50, 50), noop, telemetry=None)
    assert "would have died" not in section[1].lower()
    assert not _has_crisis_mark(rec)


# ---- _render_wizard_buff_gained ----


def test_wizard_buff_gained_bless():
    rec = _buff_record('EventOnBuffApply', _wizard_snap(), name='Blessed',
                       buff_type=1, turns_left=10)
    assert _render_wizard_buff_gained(rec) == "Wizard gained Blessed, 10 turns."


def test_wizard_buff_gained_no_turns():
    rec = _buff_record('EventOnBuffApply', _wizard_snap(), name='Blessed',
                       buff_type=1, turns_left=0)
    assert _render_wizard_buff_gained(rec) == "Wizard gained Blessed."


def test_wizard_buff_gained_curse_ignored():
    # Curses (type 2) are owned by _handle_wizard_debuff_apply, not here.
    rec = _buff_record('EventOnBuffApply', _wizard_snap(), name='Petrified',
                       buff_type=2, turns_left=3)
    assert _render_wizard_buff_gained(rec) is None


def test_wizard_buff_gained_non_wizard_ignored():
    rec = _buff_record('EventOnBuffApply', _enemy_snap(), name='Blessed',
                       buff_type=1)
    assert _render_wizard_buff_gained(rec) is None


# ---- chain-aware guard on positives (heal / buff-gain) via fire() ----


def _cast_begin_root(spell='Fireball', sequence=1):
    return {'sequence': sequence, 'parent': None, 'event_type': 'cast_begin',
            'payload': {'is_player': True, 'spell': {'name': spell}},
            'marks': []}


def test_out_of_chain_heal_claimed_and_voiced():
    p = _CrisisProducer()
    def noop(_): pass
    heal = _heal_record(_wizard_snap(), amount=5, source='Regeneration')
    section = p.fire([heal], _StubWizard(50, 50), noop, telemetry=None)
    assert "Wizard healed 5 from Regeneration." in section[1]
    assert _has_crisis_mark(heal)


def test_in_chain_heal_not_claimed_by_crisis():
    # Heal parented to a player keypress cast -> digest's; crisis abstains.
    p = _CrisisProducer()
    def noop(_): pass
    root = _cast_begin_root()
    heal = _heal_record(_wizard_snap(), amount=5, source='Fireball',
                        sequence=2, parent=1)
    section = p.fire([root, heal], _StubWizard(50, 50), noop, telemetry=None)
    assert "healed" not in section[1].lower()
    assert not _has_crisis_mark(heal)


def test_equipment_tick_heal_not_claimed_by_crisis():
    # Heal parented to an equipment_tick -> equipment producer's; crisis abstains.
    p = _CrisisProducer()
    def noop(_): pass
    eq = {'sequence': 1, 'parent': None, 'event_type': 'equipment_tick',
          'payload': {}, 'marks': []}
    heal = _heal_record(_wizard_snap(), amount=5, source='Stone Mask',
                        sequence=2, parent=1)
    section = p.fire([eq, heal], _StubWizard(50, 50), noop, telemetry=None)
    assert "healed" not in section[1].lower()
    assert not _has_crisis_mark(heal)


# ---- _render_buff_applied (debuff on wizard) ----


def test_buff_applied_petrify_on_wizard():
    rec = _buff_record('EventOnBuffApply', _wizard_snap(),
                        name='Petrified', buff_type=2, turns_left=3)
    assert _render_buff_applied(rec) == "Wizard petrified, 3 turns."


def test_buff_applied_no_turns():
    rec = _buff_record('EventOnBuffApply', _wizard_snap(),
                        name='Petrified', buff_type=2, turns_left=None)
    assert _render_buff_applied(rec) == "Wizard petrified."


def test_buff_applied_skips_self_buff_on_wizard():
    """Self-buffs (buff_type=0 passive, 1 bless, 3 item) are NOT crisis —
    they fall through to orphan or digest."""
    rec = _buff_record('EventOnBuffApply', _wizard_snap(),
                        name='Cascade', buff_type=1, turns_left=10)
    assert _render_buff_applied(rec) is None


def test_buff_applied_skips_debuff_on_enemy():
    rec = _buff_record('EventOnBuffApply', _enemy_snap(),
                        name='Petrified', buff_type=2, turns_left=3)
    assert _render_buff_applied(rec) is None


# ---- _render_buff_faded ----


def test_buff_faded_wizard():
    rec = _buff_record('EventOnBuffRemove', _wizard_snap(),
                        name='Cascade')
    assert _render_buff_faded(rec) == "Wizard's Cascade faded."


def test_buff_faded_skips_unit_removed():
    """is_unit_removed is buff cleanup on death — not a fade we narrate."""
    rec = _buff_record('EventOnBuffRemove', _wizard_snap(), name='Cascade')
    rec['payload']['is_unit_removed'] = True
    assert _render_buff_faded(rec) is None


def test_buff_faded_skips_enemy():
    rec = _buff_record('EventOnBuffRemove', _enemy_snap(), name='Stun')
    assert _render_buff_faded(rec) is None


# ---- _render_wizard_death ----


def test_wizard_death():
    rec = {
        'sequence': 30, 'parent': None,
        'event_type': 'EventOnDeath',
        'payload': {'target': _wizard_snap()},
        'marks': [],
    }
    assert _render_wizard_death(rec) == "Wizard died."


def test_wizard_death_skips_enemy():
    rec = {
        'sequence': 30, 'parent': None,
        'event_type': 'EventOnDeath',
        'payload': {'target': _enemy_snap()},
        'marks': [],
    }
    assert _render_wizard_death(rec) is None


# ---- _render_displaced ----


def test_displaced_wizard_teleport():
    rec = {
        'sequence': 40, 'parent': None,
        'event_type': 'EventOnMoved',
        'payload': {
            'unit': _wizard_snap(x=8, y=12),
            'teleport': True,
        },
        'marks': [],
    }
    assert _render_displaced(rec) == "Wizard displaced to (8,12)."


def test_displaced_skips_normal_movement():
    rec = {
        'sequence': 40, 'parent': None,
        'event_type': 'EventOnMoved',
        'payload': {
            'unit': _wizard_snap(),
            'teleport': False,
        },
        'marks': [],
    }
    assert _render_displaced(rec) is None


def test_displaced_skips_enemy_teleport():
    rec = {
        'sequence': 40, 'parent': None,
        'event_type': 'EventOnMoved',
        'payload': {
            'unit': _enemy_snap(),
            'teleport': True,
        },
        'marks': [],
    }
    assert _render_displaced(rec) is None


# ---- _render_cloud_on_wizard ----


def test_cloud_on_wizard_renders_with_duration():
    rec = {
        'sequence': 50, 'parent': None,
        'event_type': 'cloud_tick',
        'payload': {
            'cloud_name': 'Storm Cloud',
            'x': 10, 'y': 10,
            'duration_after_tick': 2,
        },
        'marks': [],
    }
    line = _render_cloud_on_wizard(rec, (10, 10))
    assert line == "In Storm Cloud, 2 turns left."


def test_cloud_on_wizard_ending_phrasing_on_last_turn():
    rec = {
        'sequence': 50, 'parent': None,
        'event_type': 'cloud_tick',
        'payload': {
            'cloud_name': 'Storm Cloud',
            'x': 10, 'y': 10,
            'duration_after_tick': 0,
        },
        'marks': [],
    }
    line = _render_cloud_on_wizard(rec, (10, 10))
    assert line == "Storm Cloud ending."


def test_cloud_off_wizard_tile_returns_none():
    rec = {
        'sequence': 50, 'parent': None,
        'event_type': 'cloud_tick',
        'payload': {
            'cloud_name': 'Storm Cloud',
            'x': 5, 'y': 5,
            'duration_after_tick': 2,
        },
        'marks': [],
    }
    line = _render_cloud_on_wizard(rec, (10, 10))
    assert line is None


def test_cloud_with_unknown_wizard_pos_returns_none():
    rec = {
        'sequence': 50, 'parent': None,
        'event_type': 'cloud_tick',
        'payload': {
            'cloud_name': 'Storm Cloud',
            'x': 10, 'y': 10,
            'duration_after_tick': 2,
        },
        'marks': [],
    }
    line = _render_cloud_on_wizard(rec, (None, None))
    assert line is None


# ---- _current_threshold_label ----


def test_threshold_above_half_returns_none():
    assert _current_threshold_label(40, 50) is None  # 80%


def test_threshold_at_half_boundary_returns_none():
    # ratio == 0.5 is NOT below; threshold is strict less-than.
    assert _current_threshold_label(25, 50) is None


def test_threshold_just_below_half():
    cutoff, label = _current_threshold_label(24, 50)  # 48%
    assert cutoff == 0.5
    assert label == "half"


def test_threshold_below_quarter():
    cutoff, label = _current_threshold_label(10, 50)  # 20%
    assert cutoff == 0.25
    assert label == "quarter"


def test_threshold_below_tenth():
    cutoff, label = _current_threshold_label(3, 50)  # 6%
    assert cutoff == 0.1
    assert label == "tenth"


def test_threshold_zero_max_returns_none():
    assert _current_threshold_label(0, 0) is None


# ---- Producer state — threshold tracking across calls ----


class _StubWizard:
    def __init__(self, cur_hp, max_hp, x=10, y=10):
        self.cur_hp = cur_hp
        self.max_hp = max_hp
        self.x = x
        self.y = y


def test_producer_emits_threshold_once_per_descent():
    """Wizard drops to half — emits once. Same threshold next turn — no
    repeat. Heal back above half — reset; descend again — emits again."""
    p = _CrisisProducer()

    def noop(_): pass

    # Turn 1: drop to 24/50 (just under half).
    section = p.fire([], _StubWizard(24, 50), noop, telemetry=None)
    assert "half" in section[1]

    # Turn 2: still at 24 — no repeat.
    section = p.fire([], _StubWizard(24, 50), noop, telemetry=None)
    assert "half" not in section[1]
    assert section[1] == ""

    # Turn 3: heal back above half.
    section = p.fire([], _StubWizard(40, 50), noop, telemetry=None)
    assert section[1] == ""

    # Turn 4: drop below half again — emits.
    section = p.fire([], _StubWizard(20, 50), noop, telemetry=None)
    assert "half" in section[1]


def test_producer_descends_through_thresholds_emits_each_new():
    """Wizard descends past half, then past quarter — should emit once
    at each new threshold descent."""
    p = _CrisisProducer()

    def noop(_): pass

    section = p.fire([], _StubWizard(20, 50), noop, telemetry=None)  # 40%
    assert "half" in section[1]

    section = p.fire([], _StubWizard(10, 50), noop, telemetry=None)  # 20%
    assert "quarter" in section[1]
    assert "half" not in section[1]


def test_producer_returns_priority_critical():
    p = _CrisisProducer()

    def noop(_): pass

    section = p.fire([], _StubWizard(50, 50), noop, telemetry=None)
    assert section[0] == PRIORITY_CRITICAL


# ---- Producer integration: damage records produce wizard-prefix lines ----


def test_producer_renders_damage_taken_line():
    """Damage event on wizard becomes a Wizard-prefix line in the section."""
    p = _CrisisProducer()

    def noop(_): pass

    rec = _damage_record(_wizard_snap(), damage=30,
                          dtype='Lightning', source='Aelf')
    section = p.fire([rec], _StubWizard(20, 50), noop, telemetry=None)
    assert "Wizard took 30 Lightning from Aelf." in section[1]


def test_producer_claims_rendered_records():
    """Records that produced lines have CRISIS_MARK stamped."""
    p = _CrisisProducer()

    def noop(_): pass

    rec = _damage_record(_wizard_snap(), damage=30)
    p.fire([rec], _StubWizard(20, 50), noop, telemetry=None)
    assert _has_crisis_mark(rec)


def test_producer_skips_already_claimed_records():
    """Records with CRISIS_MARK from a prior fire are not re-rendered."""
    p = _CrisisProducer()

    def noop(_): pass

    rec = _damage_record(_wizard_snap(), damage=30)
    rec['marks'] = [CRISIS_MARK]
    section = p.fire([rec], _StubWizard(50, 50), noop, telemetry=None)
    assert section[1] == ""


# ---- Refresh/stack cadence (Model A) ----


class _StubBuff:
    def __init__(self, name, turns_left):
        self.name = name
        self.turns_left = turns_left


class _StubWizardBuffs:
    """Wizard stub that exposes a live `.buffs` list for the agency poll.
    Full HP by default so the HP-threshold branch stays quiet."""
    def __init__(self, cur_hp=50, max_hp=50, x=10, y=10, buffs=None):
        self.cur_hp = cur_hp
        self.max_hp = max_hp
        self.x = x
        self.y = y
        self.buffs = buffs if buffs is not None else []


def _apply_record(name, turns, agency=None, buff_type=2, seq=20,
                  resist_penalty=None):
    return {
        'sequence': seq, 'parent': None,
        'event_type': 'EventOnBuffApply',
        'payload': {
            'target': _wizard_snap(),
            'buff': {
                'name': name, 'buff_type': buff_type,
                'turns_left': turns, 'agency': agency,
                'resist_penalty': resist_penalty or {},
            },
        },
        'marks': [],
    }


def _noop(_):
    pass


def test_control_onset_then_per_turn_countdown():
    """Control debuff: apply line on onset, then per-turn countdown — and
    no double-up on the onset turn."""
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Stunned', 3)])

    s = p.fire([_apply_record('Stunned', 3, 'control', seq=10)], w, _noop)
    assert "Wizard stunned, 3 turns." in s[1]
    assert "Still stunned" not in s[1]

    w.buffs = [_StubBuff('Stunned', 2)]
    s = p.fire([], w, _noop)
    assert s[1] == "Still stunned, 2 turns left."

    w.buffs = [_StubBuff('Stunned', 1)]
    s = p.fire([], w, _noop)
    assert s[1] == "Still stunned, 1 turn left."


def test_noncontrol_debuff_announced_once_then_suppressed():
    """Non-control debuff re-applied to the same duration is silent after
    onset (no high-water escalation), and gets no countdown."""
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Poisoned', 5)])

    s = p.fire([_apply_record('Poisoned', 5, None, seq=10)], w, _noop)
    assert s[1] == "Wizard poisoned, 5 turns."

    s = p.fire([_apply_record('Poisoned', 5, None, seq=11)], w, _noop)
    assert s[1] == ""


def test_control_escalation_reannounced_then_counts_from_new_high():
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Stunned', 2)])

    s = p.fire([_apply_record('Stunned', 2, 'control', seq=10)], w, _noop)
    assert "Wizard stunned, 2 turns." in s[1]

    w.buffs = [_StubBuff('Stunned', 4)]
    s = p.fire([_apply_record('Stunned', 4, 'control', seq=11)], w, _noop)
    assert "Wizard stunned, 4 turns." in s[1]
    assert "Still stunned" not in s[1]

    w.buffs = [_StubBuff('Stunned', 3)]
    s = p.fire([], w, _noop)
    assert s[1] == "Still stunned, 3 turns left."


def test_fade_clears_highwater_reannounces():
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Poisoned', 5)])

    s = p.fire([_apply_record('Poisoned', 5, None, seq=10)], w, _noop)
    assert s[1] == "Wizard poisoned, 5 turns."

    w.buffs = []
    fade = _buff_record('EventOnBuffRemove', _wizard_snap(), name='Poisoned')
    fade['sequence'] = 11
    s = p.fire([fade], w, _noop)
    assert s[1] == "Wizard's Poisoned faded."

    w.buffs = [_StubBuff('Poisoned', 5)]
    s = p.fire([_apply_record('Poisoned', 5, None, seq=12)], w, _noop)
    assert s[1] == "Wizard poisoned, 5 turns."


def test_silence_counts_down():
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Silenced', 2)])

    s = p.fire([_apply_record('Silenced', 2, 'silence', seq=10)], w, _noop)
    assert "Wizard silenced, 2 turns." in s[1]

    w.buffs = [_StubBuff('Silenced', 1)]
    s = p.fire([], w, _noop)
    assert s[1] == "Still silenced, 1 turn left."


def test_suppressed_reapply_still_claimed():
    """A suppressed flat re-application is still claimed so the orphan
    producer doesn't re-narrate it."""
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Poisoned', 5)])

    p.fire([_apply_record('Poisoned', 5, None, seq=10)], w, _noop)
    rec2 = _apply_record('Poisoned', 5, None, seq=11)
    p.fire([rec2], w, _noop)
    assert _has_crisis_mark(rec2)


# ---- Class-4: scaling resist penalty read ----


def test_resist_penalty_deepens_on_each_stack():
    """A stacking resist penalty (same duration each stack) is silent on the
    severity gate but speaks the deepening effective resist."""
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Melted Armor', 3)])

    s = p.fire([_apply_record('Melted Armor', 3, seq=10,
                              resist_penalty={'Physical': -10})], w, _noop)
    assert "Physical resistance now -10%." in s[1]

    s = p.fire([_apply_record('Melted Armor', 3, seq=11,
                              resist_penalty={'Physical': -20})], w, _noop)
    assert s[1] == "Physical resistance now -20%."


def test_resist_penalty_not_deepening_is_silent():
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Melted Armor', 3)])

    p.fire([_apply_record('Melted Armor', 3, seq=10,
                          resist_penalty={'Physical': -10})], w, _noop)
    s = p.fire([_apply_record('Melted Armor', 3, seq=11,
                              resist_penalty={'Physical': -10})], w, _noop)
    assert s[1] == ""


def test_resist_recovers_on_fade_then_reannounces():
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Melted Armor', 3)])

    p.fire([_apply_record('Melted Armor', 3, seq=10,
                          resist_penalty={'Physical': -10})], w, _noop)

    w.buffs = []
    fade = _buff_record('EventOnBuffRemove', _wizard_snap(), name='Melted Armor')
    fade['sequence'] = 11
    fade['payload']['buff']['resist_penalty'] = {'Physical': 0}
    p.fire([fade], w, _noop)

    s = p.fire([_apply_record('Melted Armor', 3, seq=12,
                              resist_penalty={'Physical': -10})], w, _noop)
    assert "Physical resistance now -10%." in s[1]


def test_resist_still_positive_no_line():
    """A resist-lowering debuff whose effective total stays non-negative is
    not a vulnerability — no resist line."""
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Chilled', 3)])

    s = p.fire([_apply_record('Chilled', 3, seq=10,
                              resist_penalty={'Fire': 20})], w, _noop)
    assert "resistance now" not in s[1]


# ---- Slice 4: wizard DOT damage summing ----


def _dot_damage(source, damage=3, dtype='Physical', turns=4, seq=10):
    """A DOT-tick EventOnDamaged on the wizard. A real DOT buff sits on the
    wizard, so its source_owner_name is the wizard — that owner==target check
    is how the producer tells a true DOT from an attacker-owned damage aura."""
    return {
        'sequence': seq, 'parent': None, 'event_type': 'EventOnDamaged',
        'payload': {
            'target': _wizard_snap(), 'damage': damage,
            'damage_type': dtype, 'source_name': source,
            'source_turns_left': turns, 'source_owner_name': 'Wizard',
        },
        'marks': [],
    }


def test_wizard_dot_stacks_sum_per_source():
    """Three Bleed ticks on the wizard sum to one line."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    recs = [_dot_damage('Bleed', 3, 'Physical', 4, seq=s) for s in (10, 11, 12)]
    s = p.fire(recs, w, _noop)
    assert "Wizard took 9 Physical from Bleed." in s[1]
    assert "took 3 Physical" not in s[1]


def test_wizard_distinct_dot_sources_not_merged():
    """Different DOT sources stay distinct."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    recs = [
        _dot_damage('Bleed', 3, 'Physical', 4, seq=10),
        _dot_damage('Poisoned', 1, 'Poison', 5, seq=11),
    ]
    s = p.fire(recs, w, _noop)
    assert "Wizard took 3 Physical from Bleed." in s[1]
    assert "Wizard took 1 Poison from Poisoned." in s[1]


def test_wizard_nondot_identical_hits_collapse_with_multiplier():
    """B1: repeated identical non-DOT hits collapse to ONE line with a
    count multiplier (default), keeping the per-hit value — not summed,
    not N copies."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    r1 = _damage_record(_wizard_snap(), damage=5, dtype='Fire', source='Imp')
    r1['sequence'] = 10
    r2 = _damage_record(_wizard_snap(), damage=5, dtype='Fire', source='Imp')
    r2['sequence'] = 11
    s = p.fire([r1, r2], w, _noop)
    assert "Wizard took 5 Fire from Imp, 2 times." in s[1]
    # Default is per-hit value with multiplier, NOT a sum.
    assert "10" not in s[1]
    assert s[1].count("Wizard took") == 1


def test_wizard_nondot_identical_hits_summed_flag():
    """B1: with crisis_damage_summed, identical hits report the total."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    r1 = _damage_record(_wizard_snap(), damage=5, dtype='Fire', source='Imp')
    r1['sequence'] = 10
    r2 = _damage_record(_wizard_snap(), damage=5, dtype='Fire', source='Imp')
    r2['sequence'] = 11
    s = p.fire([r1, r2], w, _noop, damage_summed=True)
    assert "Wizard took 10 Fire from Imp." in s[1]
    assert "times" not in s[1]


def _dot_record(source='Bleed', damage=3, dtype='Physical', owner='Wizard',
                turns_left=2, sequence=10):
    """A buff-sourced damage record: source_turns_left set, owner names the
    bearer. owner='Wizard' => DOT on the victim; owner=<enemy> => an
    attacker-owned damage aura."""
    return {
        'sequence': sequence, 'parent': None, 'event_type': 'EventOnDamaged',
        'payload': {
            'target': _wizard_snap(), 'damage': damage, 'damage_type': dtype,
            'source_name': source, 'source_turns_left': turns_left,
            'source_owner_name': owner, 'source_is_buff': True,
            'source_buff_type': 2,
        },
        'marks': [],
    }


def test_dot_on_wizard_sums():
    """A true DOT (buff sits on the wizard) sums its stacks per turn."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    r1 = _dot_record(source='Bleed', damage=3, owner='Wizard', sequence=10)
    r2 = _dot_record(source='Bleed', damage=3, owner='Wizard', sequence=11)
    r3 = _dot_record(source='Bleed', damage=3, owner='Wizard', sequence=12)
    s = p.fire([r1, r2, r3], w, _noop)
    assert "Wizard took 9 Physical from Bleed." in s[1]


def test_damage_aura_not_summed_as_dot():
    """REGRESSION: an attacker-owned DamageAuraBuff also has turns_left, but
    its owner is the ENEMY, not the wizard. It must NOT be summed as a wizard
    DOT — it collapses as a counted direct hit instead."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    # Two separate enemies each carry 'Toxic Aura' hitting the wizard for 10.
    a1 = _dot_record(source='Toxic Aura', damage=10, dtype='Poison',
                     owner='Spider', sequence=10)
    a2 = _dot_record(source='Toxic Aura', damage=10, dtype='Poison',
                     owner='Spider', sequence=11)
    s = p.fire([a1, a2], w, _noop)
    # Not summed to 20; collapsed with a count instead.
    assert "20" not in s[1]
    assert "Wizard took 10 Poison from Toxic Aura, 2 times." in s[1]


def test_wizard_nondot_varying_magnitude_not_merged():
    """B1: hits of differing magnitude stay separate (resist/vuln info)."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    r1 = _damage_record(_wizard_snap(), damage=5, dtype='Fire', source='Imp')
    r1['sequence'] = 10
    r2 = _damage_record(_wizard_snap(), damage=8, dtype='Fire', source='Imp')
    r2['sequence'] = 11
    s = p.fire([r1, r2], w, _noop)
    assert "Wizard took 5 Fire from Imp." in s[1]
    assert "Wizard took 8 Fire from Imp." in s[1]


# ---- B4: non-damage caster attribution ----


def _wizard_debuff_record(name='Blind', turns_left=3, sequence=10, parent=None,
                          source_caster=None):
    return {
        'sequence': sequence, 'parent': parent,
        'event_type': 'EventOnBuffApply',
        'payload': {
            'target': _wizard_snap(),
            'buff': {'name': name, 'buff_type': 2, 'turns_left': turns_left,
                     'agency': None, 'resist_penalty': {},
                     'source_caster': source_caster},
        },
        'marks': [],
    }


def _enemy_cast_root(caster_name='Raven Mage', sequence=1):
    return {
        'sequence': sequence, 'parent': None, 'event_type': 'cast_begin',
        'payload': {
            'is_player': False,
            'caster': _enemy_snap(name=caster_name),
            'spell': {'name': 'Mass Blindness'},
        },
        'marks': [],
    }


def test_b4_debuff_caster_from_buff_source():
    """B4: applier named directly from buff.source_caster (deferred-proof)."""
    p = _CrisisProducer()
    rec = _wizard_debuff_record(source_caster='Raven Mage')
    s = p.fire([rec], _StubWizardBuffs(), _noop)
    assert "Wizard blind, 3 turns, by Raven Mage." in s[1]


def test_b4_debuff_caster_from_chain_walk():
    """B4: when buff.source is unset, name the chain root's caster."""
    p = _CrisisProducer()
    root = _enemy_cast_root('Raven Mage', sequence=1)
    rec = _wizard_debuff_record(sequence=2, parent=1, source_caster=None)
    s = p.fire([root, rec], _StubWizardBuffs(), _noop)
    assert "Wizard blind, 3 turns, by Raven Mage." in s[1]


def test_b4_debuff_anonymous_when_no_source():
    """B4: no source, no walkable cast root -> anonymous (design floor)."""
    p = _CrisisProducer()
    rec = _wizard_debuff_record(sequence=5, parent=None, source_caster=None)
    s = p.fire([rec], _StubWizardBuffs(), _noop)
    assert "Wizard blind, 3 turns." in s[1]
    assert "by" not in s[1]


# ---- B3: external-only displacement ----


def _wizard_teleport_record(sequence=10, parent=None, x=8, y=12):
    return {
        'sequence': sequence, 'parent': parent,
        'event_type': 'EventOnMoved',
        'payload': {'unit': _wizard_snap(x=x, y=y), 'teleport': True},
        'marks': [],
    }


def test_b3_external_displace_announced_and_claimed():
    """B3: an enemy push (out-of-chain teleport) surfaces and is claimed."""
    p = _CrisisProducer()
    rec = _wizard_teleport_record(sequence=10, parent=None)
    s = p.fire([rec], _StubWizard(50, 50), _noop)
    assert "Wizard displaced to (8,12)." in s[1]
    assert _has_crisis_mark(rec)


def test_b3_own_blink_suppressed():
    """B3: the player's own Blink (in player-keypress chain) is NOT a crisis
    displace — the digest owns it via compose_moved_section. Crisis neither
    lines nor claims it."""
    p = _CrisisProducer()
    root = _cast_begin_root(spell='Blink', sequence=1)  # is_player=True root
    rec = _wizard_teleport_record(sequence=2, parent=1)
    s = p.fire([root, rec], _StubWizard(50, 50), _noop)
    assert "displaced" not in s[1]
    assert not _has_crisis_mark(rec)


def test_b3_forced_swap_teleport_false_with_caster():
    """#2: a force-swap relocates the wizard via EventOnMoved with
    teleport=False (the swapped-into unit; Level.py:3043). With an external
    caster in the chain it must still surface — a teleport-only gate would
    drop it. Named with its cause."""
    p = _CrisisProducer()
    root = _enemy_cast_root('Cyclops', sequence=1)
    rec = _wizard_teleport_record(sequence=2, parent=1)
    rec['payload']['teleport'] = False
    s = p.fire([root, rec], _StubWizard(50, 50), _noop)
    assert "Wizard displaced to (8,12) by Cyclops." in s[1]
    assert _has_crisis_mark(rec)


def test_b3_manual_step_teleport_false_no_caster_silent():
    """#2 guard: a manual step (teleport=False, parent=None, no caster) is
    out-of-chain but must stay silent — it is NOT a forced relocation."""
    p = _CrisisProducer()
    rec = _wizard_teleport_record(sequence=10, parent=None)
    rec['payload']['teleport'] = False
    s = p.fire([rec], _StubWizard(50, 50), _noop)
    assert "displaced" not in s[1]
    assert not _has_crisis_mark(rec)


def test_b3_multistep_pull_collapses_to_final():
    """#1: a multi-step pull fires one EventOnMoved per tile. Only the final
    destination speaks; intermediate steps are claimed silently."""
    p = _CrisisProducer()
    root = _enemy_cast_root('Ice Lizard', sequence=1)
    step1 = _wizard_teleport_record(sequence=2, parent=1, x=10, y=7)
    step2 = _wizard_teleport_record(sequence=3, parent=1, x=9, y=8)
    step3 = _wizard_teleport_record(sequence=4, parent=1, x=8, y=9)
    s = p.fire([root, step1, step2, step3], _StubWizard(50, 50), _noop)
    joined = s[1]
    assert joined.count("displaced") == 1
    assert "Wizard displaced to (8,9) by Ice Lizard." in joined
    assert _has_crisis_mark(step1) and _has_crisis_mark(step2)
    assert _has_crisis_mark(step3)


def test_b3_external_displace_with_caster():
    """B3+B4: an enemy teleport names its cause from the chain root."""
    p = _CrisisProducer()
    root = _enemy_cast_root('Raven Mage', sequence=1)
    rec = _wizard_teleport_record(sequence=2, parent=1)
    s = p.fire([root, rec], _StubWizard(50, 50), _noop)
    assert "Wizard displaced to (8,12) by Raven Mage." in s[1]


# ---- B5: fade / re-apply churn ----


def test_b5_fade_suppressed_when_reapplied_same_turn():
    """B5: a debuff that fades AND re-applies in one turn suppresses the fade
    line (the pair would otherwise read 'X faded. Wizard X.')."""
    p = _CrisisProducer()
    fade = _buff_record('EventOnBuffRemove', _wizard_snap(), name='Blind',
                        buff_type=2, sequence=10)
    apply = _wizard_debuff_record(name='Blind', turns_left=3, sequence=11)
    s = p.fire([fade, apply], _StubWizardBuffs(), _noop)
    assert "faded" not in s[1]


def test_b5_genuine_fade_still_speaks():
    """B5: a fade with no same-turn re-apply still narrates normally."""
    p = _CrisisProducer()
    fade = _buff_record('EventOnBuffRemove', _wizard_snap(), name='Blind',
                        buff_type=2, sequence=10)
    s = p.fire([fade], _StubWizardBuffs(), _noop)
    assert "Wizard's Blind faded." in s[1]


def test_b5_repeated_reblind_silent_after_first():
    """B5 (the log-1279 case): already blind, then fade+re-apply churn at the
    same duration on later turns stays SILENT — no fade line, and the re-apply
    is gated by the severity high-water set on first onset."""
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Blind', 3)])
    # Turn 1: fresh blind — announces.
    s1 = p.fire([_wizard_debuff_record(name='Blind', turns_left=3, sequence=10)],
                w, _noop)
    assert "Wizard blind, 3 turns." in s1[1]
    # Turn 2: fade + re-apply at the same duration — fully silent.
    fade = _buff_record('EventOnBuffRemove', _wizard_snap(), name='Blind',
                        buff_type=2, sequence=20)
    apply = _wizard_debuff_record(name='Blind', turns_left=3, sequence=21)
    s2 = p.fire([fade, apply], w, _noop)
    assert "faded" not in s2[1]
    assert "blind" not in s2[1].lower()


def test_b5_reblind_longer_duration_escalates():
    """B5: a re-apply at a LONGER duration still escalates (magnitude rides
    the shown channel), even through churn."""
    p = _CrisisProducer()
    w = _StubWizardBuffs(buffs=[_StubBuff('Blind', 5)])
    p.fire([_wizard_debuff_record(name='Blind', turns_left=3, sequence=10)],
           w, _noop)
    fade = _buff_record('EventOnBuffRemove', _wizard_snap(), name='Blind',
                        buff_type=2, sequence=20)
    apply = _wizard_debuff_record(name='Blind', turns_left=6, sequence=21)
    s2 = p.fire([fade, apply], w, _noop)
    assert "Wizard blind, 6 turns." in s2[1]
    assert "faded" not in s2[1]


# ---- Unit 4 (D6): staged capture-only kinds excluded from unmodeled telemetry ----


class _FakeTelemetry:
    def __init__(self):
        self.calls = []

    def emit(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_unit4_staged_kinds_do_not_trip_crisis_unmodeled():
    # Wizard-subject hp_loss / xp_change / EventOnAwakened are capture-only,
    # staged for the composer phase — a quiet turn carrying them must not fire
    # wizard_records_no_output (else every HP-cost cast and SP pickup becomes
    # routine telemetry noise).
    prod = _CrisisProducer()
    tel = _FakeTelemetry()
    records = [
        {"event_type": "hp_loss", "payload": {"target": _wizard_snap()}},
        {"event_type": "xp_change", "payload": {"target": _wizard_snap()}},
        {"event_type": "EventOnAwakened", "payload": {"target": _wizard_snap()}},
    ]
    prod._maybe_emit_unmodeled(tel, records, [])
    assert tel.calls == []


def test_game_log_records_never_trip_crisis_unmodeled():
    # Oracle records carry template/values/resolved/turn — no unit snapshot —
    # so the wizard-subject scan structurally never sees them. Pinned so a
    # future payload change that adds a unit snap re-raises the question.
    prod = _CrisisProducer()
    tel = _FakeTelemetry()
    prod._maybe_emit_unmodeled(
        tel,
        [{"event_type": "game_log",
          "payload": {"template": "{unit} pays {cost} HP to cast {spell}",
                      "values": {"unit": "Wizard"}, "resolved": "…", "turn": 3}}],
        [],
    )
    assert tel.calls == []


def test_cause_marker_records_never_trip_crisis_unmodeled():
    # Unit 2: marker payloads carry a 'recipient' snapshot, not
    # 'unit'/'target', so the wizard-subject scan structurally never sees
    # them (game_log precedent — no _STAGED_CAPTURE_ONLY_KINDS twin needed).
    # Pinned so a payload-key change re-raises the question.
    prod = _CrisisProducer()
    tel = _FakeTelemetry()
    prod._maybe_emit_unmodeled(
        tel,
        [{"event_type": "item_pickup",
          "payload": {"item": "Frostpetal", "item_kind": "component",
                      "component": "Frostpetal",
                      "recipient": _wizard_snap()}}],
        [],
    )
    assert tel.calls == []


def test_non_staged_wizard_record_still_trips_crisis_unmodeled():
    # The diagnostic itself is preserved for kinds crisis is expected to render.
    prod = _CrisisProducer()
    tel = _FakeTelemetry()
    prod._maybe_emit_unmodeled(
        tel,
        [{"event_type": "silent_heal", "payload": {"target": _wizard_snap()}}],
        [],
    )
    assert len(tel.calls) == 1


# ---- Unit 1: container-diff kinds excluded from crisis unmodeled telemetry ----


def test_unit1_container_kinds_do_not_trip_crisis_unmodeled():
    # Container-diff payloads DO carry unit snapshots (unlike game_log), so
    # wizard-subject instances land on routine turns constantly — every
    # wizard buff apply folds resists, every cast decrements charges. They
    # are composer-staged, not missing crisis branches.
    import container_diff as cd
    prod = _CrisisProducer()
    tel = _FakeTelemetry()
    records = [{"event_type": k, "payload": {"unit": _wizard_snap()}}
               for k in cd.ALL_KINDS]
    prod._maybe_emit_unmodeled(tel, records, [])
    assert tel.calls == []
