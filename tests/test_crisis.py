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
