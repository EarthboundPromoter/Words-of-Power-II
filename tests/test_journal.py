# Tests for journal.py shield capture (R3 Feature A, step A1).
# Run from the game root with: python -m pytest "<mod>/tests/test_journal.py" -v
# (journal imports Level; the game root must be on sys.path — i.e. run from it.)
#
# Covers the pure, game-free pieces of the shield capture: the post-resist
# "would have been" recompute, the block-detection signature, and the shape of
# the synthesized records. The patched-method wiring + live block detection are
# validated by the adversarial review + field play, not here.

import sys
import os
from types import SimpleNamespace

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from journal import (
    _resist_blocked_amount,
    _claim_block_event,
    _shield_change_record,
    _shield_gained_payload,
    _shield_stripped_payload,
    _shield_blocked_payload,
    _payload_shield_removed,
    _team_change_record,
    _flying_change_record,
    _rename_record,
    _sprite_change_record,
    _debuff_immune_change_record,
    _classify_watched,
)


# ---- Fixtures ----


def _unit(name="Wizard", shields=4, player=True):
    return SimpleNamespace(
        name=name, x=10, y=10, cur_hp=50, max_hp=50, shields=shields,
        team=0 if player else 1, is_player_controlled=player,
        is_boss=False, is_lair=False, parent=None,
    )


class _Tag:
    # Hashable (identity) stand-in for a damage-type Tag, which the game uses
    # as a dict key in unit.resists. SimpleNamespace can't be a key (it defines
    # __eq__, so it's unhashable).
    def __init__(self, name):
        self.name = name


def _dtype(name="Fire"):
    return _Tag(name)


def _source(name="Fire Bolt", owner_name="Aelf"):
    return SimpleNamespace(name=name, owner=SimpleNamespace(name=owner_name))


# ---- _resist_blocked_amount: mirror Level.py:4034-4039 ----


def test_blocked_amount_zero_resist_is_full():
    assert _resist_blocked_amount({_dtype(): 0}, _dtype(), 12) == 12


def test_blocked_amount_uses_damage_type_key():
    ft = _dtype("Fire")
    # 50% Fire resist -> ceil(12 * 0.5) = 6
    assert _resist_blocked_amount({ft: 50}, ft, 12) == 6


def test_blocked_amount_ceils_the_resisted_fraction():
    ft = _dtype("Fire")
    # 50% of 11 = 5.5 -> ceil -> 6 (matches the game's math.ceil)
    assert _resist_blocked_amount({ft: 50}, ft, 11) == 6


def test_blocked_amount_full_resist_is_zero():
    ft = _dtype()
    assert _resist_blocked_amount({ft: 100}, ft, 30) == 0


def test_blocked_amount_resist_capped_at_100():
    ft = _dtype()
    # 150 resist clamps to 100 -> 0, no over-resist heal
    assert _resist_blocked_amount({ft: 150}, ft, 30) == 0


def test_blocked_amount_negative_resist_amplifies():
    ft = _dtype()
    # -50 resist passes through uncapped: ceil(10 * 1.5) = 15
    assert _resist_blocked_amount({ft: -50}, ft, 10) == 15


def test_blocked_amount_missing_type_defaults_zero_resist():
    assert _resist_blocked_amount({_dtype("Ice"): 75}, _dtype("Fire"), 8) == 8


def test_blocked_amount_empty_or_none_resists():
    assert _resist_blocked_amount({}, _dtype(), 9) == 9
    assert _resist_blocked_amount(None, _dtype(), 9) == 9


def test_blocked_amount_none_amount():
    assert _resist_blocked_amount({_dtype(): 0}, _dtype(), None) is None


# ---- _claim_block_event: precise block detection + claim-once via marks ----


def _rec(seq, event_type, target_id=None):
    payload = {}
    if target_id is not None:
        payload['target'] = {'id': target_id}
    return {'sequence': seq, 'event_type': event_type, 'payload': payload, 'marks': []}


def test_block_event_claimed_when_shield_removed_for_target():
    records = [
        _rec(10, 'EventOnDamaged', target_id=200),
        _rec(11, 'EventOnShieldRemoved', target_id=200),
    ]
    assert _claim_block_event(records, seq_before=9, target_id=200) is True


def test_block_event_not_claimed_when_event_predates_call():
    # the shield-removed record is at/below seq_before -> not from this call
    records = [_rec(9, 'EventOnShieldRemoved', target_id=200)]
    assert _claim_block_event(records, seq_before=9, target_id=200) is False


def test_block_event_not_claimed_for_other_target():
    records = [_rec(11, 'EventOnShieldRemoved', target_id=999)]
    assert _claim_block_event(records, seq_before=9, target_id=200) is False


def test_block_event_not_claimed_without_shield_event():
    # deal_damage returned 0 for some non-block reason; no shield event raised
    records = [_rec(10, 'EventOnDamaged', target_id=200),
               _rec(11, 'EventOnDeath', target_id=200)]
    assert _claim_block_event(records, seq_before=9, target_id=200) is False


def test_block_event_claimed_only_once():
    # An outer deal_damage wrapper whose window overlaps a nested same-unit
    # block must NOT re-claim the event the inner wrapper already took.
    records = [_rec(11, 'EventOnShieldRemoved', target_id=200)]
    assert _claim_block_event(records, seq_before=9, target_id=200) is True   # inner
    assert _claim_block_event(records, seq_before=9, target_id=200) is False  # outer


def test_two_real_blocks_each_claimable_once():
    # Two genuine blocks on the same unit -> two distinct events, each claimed
    # exactly once (correct count), but neither twice.
    records = [
        _rec(11, 'EventOnShieldRemoved', target_id=200),
        _rec(12, 'EventOnShieldRemoved', target_id=200),
    ]
    assert _claim_block_event(records, seq_before=9, target_id=200) is True
    assert _claim_block_event(records, seq_before=9, target_id=200) is True
    assert _claim_block_event(records, seq_before=9, target_id=200) is False


# ---- _shield_change_record: __setattr__ gain/strip/no-op classification ----


def test_change_record_gain():
    et, payload = _shield_change_record(_unit(shields=4), before=1, after=4)
    assert et == 'shield_gained'
    assert payload['amount'] == 3
    assert payload['shields_before'] == 1
    assert payload['shields_after'] == 4


def test_change_record_strip():
    et, payload = _shield_change_record(_unit(name="Ogre", player=False),
                                        before=4, after=1)
    assert et == 'shield_stripped'
    assert payload['amount_removed'] == 3


def test_change_record_noop_returns_none():
    assert _shield_change_record(_unit(), before=3, after=3) is None


def test_change_record_treats_none_as_zero():
    # first stored value (None -> 0) becoming a real gain
    et, payload = _shield_change_record(_unit(), before=None, after=2)
    assert et == 'shield_gained'
    assert payload['amount'] == 2


def test_change_record_strip_to_zero():
    et, payload = _shield_change_record(_unit(), before=2, after=0)
    assert et == 'shield_stripped'
    assert payload['amount_removed'] == 2


# ---- Record shapes ----


def test_shield_gained_payload_shape():
    p = _shield_gained_payload(_unit(shields=4), amount=3,
                               shields_before=1, shields_after=4)
    assert p['amount'] == 3
    assert p['shields_before'] == 1
    assert p['shields_after'] == 4
    assert p['target']['name'] == "Wizard"
    assert p['target']['is_player_controlled'] is True


def test_shield_stripped_payload_computes_removed():
    p = _shield_stripped_payload(_unit(name="Ogre", player=False),
                                 shields_before=4, shields_after=1)
    assert p['amount_removed'] == 3
    assert p['shields_before'] == 4
    assert p['shields_after'] == 1
    assert p['target']['name'] == "Ogre"


def test_shield_blocked_payload_shape():
    p = _shield_blocked_payload(_unit(shields=2), blocked_amount=12,
                                damage_type=_dtype("Fire"),
                                source=_source("Fire Bolt", "Aelf"),
                                shields_remaining=2)
    assert p['blocked_amount'] == 12
    assert p['damage_type'] == "Fire"
    assert p['source_name'] == "Fire Bolt"
    assert p['source_owner_name'] == "Aelf"
    assert p['shields_remaining'] == 2
    assert p['target']['name'] == "Wizard"


def test_shield_blocked_payload_tolerates_sourceless():
    p = _shield_blocked_payload(_unit(), blocked_amount=5,
                                damage_type=_dtype("Ice"), source=None,
                                shields_remaining=0)
    assert p['source_name'] is None
    assert p['source_owner_name'] is None
    assert p['damage_type'] == "Ice"


def test_payload_shield_removed_enriched_with_source():
    evt = SimpleNamespace(unit=_unit(), source=_source("Dark Bolt", "Bone Shambler"))
    p = _payload_shield_removed(evt)
    assert p['target']['name'] == "Wizard"
    assert p['source_name'] == "Dark Bolt"
    assert p['source_owner_name'] == "Bone Shambler"


def test_payload_shield_removed_handles_missing_source():
    evt = SimpleNamespace(unit=_unit(), source=None)
    p = _payload_shield_removed(evt)
    assert p['source_name'] is None
    assert p['source_owner_name'] is None


# ---- _team_change_record: categorical allegiance flips (TEAM_PLAYER=0/ENEMY=1) ----


def test_team_change_noop_returns_none():
    assert _team_change_record(_unit(), 0, 0) is None
    assert _team_change_record(_unit(), 1, 1) is None


def test_team_change_enemy_to_player_is_joined():
    et, payload = _team_change_record(_unit(name="Ogre", player=False), 1, 0)
    assert et == 'team_joined'                 # "turned friendly"
    assert payload['team_before'] == 1
    assert payload['team_after'] == 0
    assert payload['target']['name'] == "Ogre"


def test_team_change_player_to_enemy_is_turned():
    et, payload = _team_change_record(_unit(name="Wolf"), 0, 1)
    assert et == 'team_turned'                 # "turned hostile"
    assert payload['team_before'] == 0
    assert payload['team_after'] == 1


# ---- G-H/G-I attrs fix (2026-07-03 ruling): flying / name / asset_name /
# ---- debuff_immune builders. Kinds are direction-named (shield/team
# ---- precedent); no-op filtering lives IN the builders.


def test_flying_gained_on_zero_crossing():
    et, payload = _flying_change_record(_unit(name="Mind Maggot"), 0, 1)
    assert et == 'flight_gained'
    assert payload['flying_before'] == 0
    assert payload['flying_after'] == 1
    assert payload['target']['name'] == "Mind Maggot"


def test_flying_stack_increment_is_noop():
    # 1->2 renders nothing (the flag is already up) — G-H capture-per-render.
    assert _flying_change_record(_unit(), 1, 2) is None
    assert _flying_change_record(_unit(), 0, 0) is None
    assert _flying_change_record(_unit(), None, 0) is None


def test_flying_lost_from_any_positive():
    et, payload = _flying_change_record(_unit(), 2, 0)
    assert et == 'flight_lost'
    assert payload['flying_before'] == 2
    assert payload['flying_after'] == 0


def test_rename_records_transition():
    et, payload = _rename_record(
        _unit(name="Mind Maggot Drone"), "Mind Maggot", "Mind Maggot Drone")
    assert et == 'unit_renamed'
    assert payload['name_before'] == "Mind Maggot"
    assert payload['name_after'] == "Mind Maggot Drone"
    # Snapshot is post-write: carries the NEW identity (team precedent).
    assert payload['target']['name'] == "Mind Maggot Drone"


def test_rename_noop_returns_none():
    assert _rename_record(_unit(), "Orc", "Orc") is None


def test_sprite_change_records_transition_and_noop():
    et, payload = _sprite_change_record(
        _unit(name="Mind Maggot"), "mind_maggot", "mind_maggot_winged")
    assert et == 'sprite_change'
    assert payload['asset_before'] == "mind_maggot"
    assert payload['asset_after'] == "mind_maggot_winged"
    assert _sprite_change_record(_unit(), "orc", "orc") is None


def test_debuff_immune_flip_and_standing_rewrite():
    et, payload = _debuff_immune_change_record(_unit(name="Snow Queen"), False, True)
    assert et == 'debuff_immunity_gained'
    assert payload['immune_before'] is False
    assert payload['immune_after'] is True
    et, _ = _debuff_immune_change_record(_unit(), True, False)
    assert et == 'debuff_immunity_lost'
    # The Diamond Aegis rewrites `shields > 0` every turn/attempt
    # (FinalBosses.py:1414-1415) — same-value rewrites are the no-op case.
    assert _debuff_immune_change_record(_unit(), True, True) is None
    assert _debuff_immune_change_record(_unit(), None, False) is None


def test_classify_watched_routes_the_new_attrs():
    unit = _unit(name="Mind Maggot")
    assert _classify_watched(unit, 'flying', 0, 1)[0] == 'flight_gained'
    assert _classify_watched(unit, 'name', "A", "B")[0] == 'unit_renamed'
    assert _classify_watched(unit, 'asset_name', "a", "b")[0] == 'sprite_change'
    assert _classify_watched(unit, 'debuff_immune', False, True)[0] == 'debuff_immunity_gained'


def test_new_attrs_are_watched():
    from journal import _WATCHED_ATTRS
    for attr in ('flying', 'name', 'asset_name', 'debuff_immune'):
        assert attr in _WATCHED_ATTRS, attr


def test_new_kinds_staged_in_both_producer_known_sets():
    # Records-only discipline: the six new kinds must be composer-staged in
    # BOTH producers (digest known-set + crisis staged twin — these payloads
    # carry 'target' unit snapshots). Lazy imports keep this file light.
    import digest
    import crisis
    kinds = ('flight_gained', 'flight_lost', 'unit_renamed', 'sprite_change',
             'debuff_immunity_gained', 'debuff_immunity_lost')
    for kind in kinds:
        assert kind in digest._COMPOSER_KNOWN_EVENT_TYPES, kind
        assert kind in crisis._STAGED_CAPTURE_ONLY_KINDS, kind


# ---- _payload_death attribution fields (death-attribution feature) ----
# The payload mirrors the game's own death line inputs (Level.py:4125 reads
# source.owner) plus the DOT recovery field killing_source_caster.


def test_payload_death_direct_hit_carries_owner():
    from journal import _payload_death
    aelf = _unit(name="Aelf", player=False)
    spell = SimpleNamespace(name="Poison Sting", owner=aelf)
    dmg = SimpleNamespace(damage=9, damage_type=_dtype("Poison"), source=spell)
    evt = SimpleNamespace(unit=_unit(), damage_event=dmg)
    p = _payload_death(evt)
    assert p['killing_source'] == 'Poison Sting'
    assert p['source_owner_name'] == 'Aelf'
    assert p['source_is_buff'] is False
    assert p['killing_source_caster'] is None


def test_payload_death_dot_curse_recovers_caster():
    # Buff.owner is the BEARER (Level.py:1137-1139) — the vanilla death line
    # names the victim for DOT kills. killing_source_caster recovers the
    # applier when the effect set buff.source.
    from journal import _payload_death
    import Level as _Level
    wizard = _unit()
    shaman = _unit(name="Goblin Shaman", player=False)
    poison = _Level.Buff()
    poison.name = "Poison"
    poison.buff_type = _Level.BUFF_TYPE_CURSE
    poison.owner = wizard
    poison.source = SimpleNamespace(caster=shaman)
    dmg = SimpleNamespace(damage=4, damage_type=_dtype("Poison"),
                          source=poison)
    evt = SimpleNamespace(unit=wizard, damage_event=dmg)
    p = _payload_death(evt)
    assert p['killing_source'] == 'Poison'
    assert p['source_owner_name'] == 'Wizard'
    assert p['source_is_buff'] is True
    assert p['source_buff_type'] == _Level.BUFF_TYPE_CURSE
    assert p['killing_source_caster'] == 'Goblin Shaman'


def test_payload_death_no_damage_event_all_none():
    # kill() without a damage event: every attribution field is None but
    # present, so the renderer never KeyErrors.
    from journal import _payload_death
    evt = SimpleNamespace(unit=_unit(), damage_event=None)
    p = _payload_death(evt)
    assert p['killing_source'] is None
    assert p['source_owner_name'] is None
    assert p['source_is_buff'] is False
    assert p['source_buff_type'] is None
    assert p['killing_source_caster'] is None


# ---- _payload_death is_expired (duration expiry vs dismissal vs kill) ----
# The game's expiry kill is exactly: no damage event AND turns_to_death <= 0
# at kill time (Level.py:2167-2170). Renderers speak "expired" on the flag.


def test_payload_death_expiry_flagged():
    from journal import _payload_death
    u = _unit(name="Sword of Light", player=False)
    u.turns_to_death = 0
    evt = SimpleNamespace(unit=u, damage_event=None)
    assert _payload_death(evt)['is_expired'] is True


def test_payload_death_midlife_dismissal_not_expiry():
    # A dismissal/transformation of a temporary unit mid-life is causeless
    # but NOT an expiry — turns_to_death is still positive.
    from journal import _payload_death
    u = _unit(name="Wolf", player=False)
    u.turns_to_death = 4
    evt = SimpleNamespace(unit=u, damage_event=None)
    assert _payload_death(evt)['is_expired'] is False


def test_payload_death_damage_kill_never_expiry():
    # A damage kill on the unit's final turn is still a kill.
    from journal import _payload_death
    u = _unit(name="Wolf", player=False)
    u.turns_to_death = 0
    spell = SimpleNamespace(name="Fireball", owner=None)
    dmg = SimpleNamespace(damage=9, damage_type=_dtype("Fire"), source=spell)
    evt = SimpleNamespace(unit=u, damage_event=dmg)
    assert _payload_death(evt)['is_expired'] is False


def test_payload_death_permanent_unit_not_expiry():
    # Permanent units (turns_to_death None, the default stub) never flag.
    from journal import _payload_death
    evt = SimpleNamespace(unit=_unit(), damage_event=None)
    assert _payload_death(evt)['is_expired'] is False
