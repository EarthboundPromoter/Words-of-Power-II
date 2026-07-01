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
