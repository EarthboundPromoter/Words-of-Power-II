# Slice 0 (the G1 Shape-C consumption contract): the journal's persistent
# record index. Pins the build law from SLICE0_BOUNDARY_SPINE_BUILD_PLAN §0:
# extend-on-read idempotence; clear-first rebuild on wholesale list
# replacement (replay rebinds journal.records — the rebuild is that path's
# NORMAL operation); sequences read off record dicts only; parent-None
# records never pollute the children map; extend_index never raises (a
# raise at the fire sites would mute the turn's speech); tail_after
# compares sequences, never list positions.

import sys
import types
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
GAME = MOD.parents[1]
for p in (str(GAME), str(MOD)):
    if p not in sys.path:
        sys.path.insert(0, p)

sys.modules.setdefault('steamworks', types.ModuleType('steamworks'))

import journal as journal_mod
from journal import tail_after


def _rec(seq, parent=None, payload=None, event_type='EventOnDamaged'):
    return {
        'sequence': seq, 'action_chain_id': 0, 'level_id': 1,
        'event_type': event_type, 'parent': parent,
        'timestamp': 0.0, 'payload': payload or {}, 'marks': [],
    }


def _fresh():
    return journal_mod._Journal()


def _legacy_index(records):
    # orphan._build_index / digest.build_record_index, verbatim: the
    # equivalence reference (skeptic finding 8 — the mid-turn consumer's
    # gate is this pin, not replay).
    return {r['sequence']: r for r in records if 'sequence' in r}


# ---- extension basics ----

def test_extension_matches_the_legacy_index_builders():
    j = _fresh()
    j.records = [_rec(1), _rec(2, parent=1), _rec(3, parent=1), _rec(4, parent=3)]
    j.extend_index()
    assert j.record_index.by_seq == _legacy_index(j.records)


def test_children_map_holds_append_order_and_skips_parent_none():
    j = _fresh()
    j.records = [_rec(1), _rec(2, parent=1), _rec(3), _rec(4, parent=1)]
    j.extend_index()
    kids = j.record_index.children
    assert [r['sequence'] for r in kids[1]] == [2, 4]
    assert None not in kids
    assert 3 not in kids  # roots with no children get no bucket


def test_extension_is_idempotent_and_incremental():
    j = _fresh()
    j.records = [_rec(1), _rec(2, parent=1)]
    j.extend_index()
    j.extend_index()
    assert j._indexed_count == 2
    j.records.append(_rec(3, parent=2))
    j.extend_index()
    assert j._indexed_count == 3
    assert j.record_index.by_seq[3]['parent'] == 2
    assert [r['sequence'] for r in j.record_index.children[2]] == [3]


def test_wizard_team_memo_replicates_the_five_key_scan():
    wiz = {'is_player_controlled': True, 'team': 7, 'name': 'Wizard'}
    for key in ('caster', 'target', 'unit', 'user', 'owner'):
        j = _fresh()
        j.records = [
            # String payload field (EventOnBuffAttemptApply shape) must
            # not blow up or match — the isinstance guard.
            _rec(1, payload={'unit': 'Treant', 'buff': 'Blind'}),
            _rec(2, payload={key: wiz}),
        ]
        j.extend_index()
        assert j.record_index.wizard_team == 7, key


def test_wizard_team_requires_non_none_team_and_first_match_wins():
    j = _fresh()
    j.records = [
        _rec(1, payload={'unit': {'is_player_controlled': True, 'team': None}}),
        _rec(2, payload={'unit': {'is_player_controlled': True, 'team': 3}}),
        _rec(3, payload={'unit': {'is_player_controlled': True, 'team': 9}}),
    ]
    j.extend_index()
    assert j.record_index.wizard_team == 3


# ---- lifecycle ----

def test_reset_clears_the_index_without_rebinding_the_view():
    j = _fresh()
    j.records = [_rec(1), _rec(2, parent=1)]
    j.extend_index()
    view = j.record_index
    by_seq_ref = view.by_seq
    j.reset(level_id=2)
    assert j.record_index is view
    assert view.by_seq is by_seq_ref
    assert view.by_seq == {} and view.children == {}
    assert view.wizard_team is None
    assert j._indexed_count == 0 and j._last_indexed_seq is None
    # Post-reset extension restarts cleanly (sequences keep rising).
    j.records = [_rec(10)]
    j.extend_index()
    assert set(view.by_seq) == {10}


def test_wholesale_rebind_to_a_shorter_list_rebuilds_clean():
    # replay_profile.py rebinds J.records per batch and never calls
    # reset — the mismatch detector makes rebuild the normal path.
    j = _fresh()
    j.records = [_rec(1), _rec(2, parent=1), _rec(3, parent=2)]
    j.extend_index()
    j.records = [_rec(100), _rec(101, parent=100)]
    j.extend_index()
    assert set(j.record_index.by_seq) == {100, 101}
    assert set(j.record_index.children) == {100}


def test_rebind_same_length_different_sequences_is_detected():
    j = _fresh()
    j.records = [_rec(1), _rec(2)]
    j.extend_index()
    j.records = [_rec(50), _rec(60)]
    j.extend_index()
    assert set(j.record_index.by_seq) == {50, 60}


def test_extend_index_never_raises_on_malformed_records():
    j = _fresh()
    j.records = [_rec(1), "not a record at all", _rec(3)]
    j.extend_index()  # must not raise (finding 10); maps end safe
    assert isinstance(j.record_index.by_seq, dict)


# ---- the watchdog feed ----

def test_marked_records_drain_and_reset():
    j = _fresh()
    a, b = _rec(1), _rec(2)
    j.note_producer_mark(a)
    j.note_producer_mark(b)
    j.note_producer_mark(a)  # double-claim shape: noted twice is fine
    drained = j.drain_marked_records()
    assert drained == [a, b, a]
    assert j.drain_marked_records() == []
    j.note_producer_mark(b)
    j.reset(level_id=5)
    assert j.drain_marked_records() == []


# ---- tail_after ----

def test_tail_after_slices_by_sequence_not_position():
    records = [_rec(5), _rec(7), _rec(9)]
    assert tail_after(records, -1) == records
    assert [r['sequence'] for r in tail_after(records, 5)] == [7, 9]
    assert [r['sequence'] for r in tail_after(records, 7)] == [9]
    assert tail_after(records, 9) == []
    assert tail_after([], 3) == []


def test_tail_after_survives_a_reset_shaped_list():
    # After journal.reset the list restarts but sequences keep rising:
    # a producer cursor from the old level still slices correctly.
    post_reset = [_rec(40), _rec(41)]
    assert tail_after(post_reset, 37) == post_reset
