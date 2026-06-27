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
    """A DOT-tick EventOnDamaged on the wizard (source is a buff, so the
    journal captured source_turns_left)."""
    return {
        'sequence': seq, 'parent': None, 'event_type': 'EventOnDamaged',
        'payload': {
            'target': _wizard_snap(), 'damage': damage,
            'damage_type': dtype, 'source_name': source,
            'source_turns_left': turns,
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


def test_wizard_nondot_damage_not_summed():
    """Non-DOT hits (no buff source) render per event, never summed."""
    p = _CrisisProducer()
    w = _StubWizardBuffs()
    r1 = _damage_record(_wizard_snap(), damage=5, dtype='Fire', source='Imp')
    r1['sequence'] = 10
    r2 = _damage_record(_wizard_snap(), damage=5, dtype='Fire', source='Imp')
    r2['sequence'] = 11
    s = p.fire([r1, r2], w, _noop)
    assert s[1].count("Wizard took 5 Fire from Imp.") == 2
