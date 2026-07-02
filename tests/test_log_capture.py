# Pure-logic tests for the oracle's reconciliation rows + parity checker
# (log_capture.py). Synthetic dict records and a fake telemetry double — no
# game import beyond text.py (constants), following the test_digest split:
# real-Level integration for the wrap lives in test_journal_capture_gate.py.
#
# Run from the game root: python -m pytest "<mod>/tests/test_log_capture.py"

import sys
import os

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

import log_capture
from log_capture import (ParityChecker, rows,
                         EXPECT, VIEW_LAYER, PENDING, JUSTIFIED_DROP)


# ---- Doubles ----

class _FakeTelemetry:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []

    def is_enabled(self):
        return self.enabled

    def emit(self, ev, **fields):
        self.calls.append((ev, fields))


_seq = [0]


def _rec(kind, template=None, turn=2):
    _seq[0] += 1
    payload = {}
    if kind == 'game_log':
        payload = {'template': template, 'values': {},
                   'resolved': template, 'turn': turn}
    return {'sequence': _seq[0], 'event_type': kind, 'payload': payload,
            'marks': []}


# Real rows used as fixtures (import text lazily via rows()).
def _tpl(status_wanted):
    for tpl, row in rows().items():
        if row['status'] == status_wanted:
            return tpl, row
    raise AssertionError("no row with status %r" % status_wanted)


DMG_DEALS = None  # resolved in setup below


def _dmg_deals():
    import text
    return text.DMG_DEALS


# ---- Row table sanity ----

def test_rows_statuses_valid_and_expect_rows_have_kinds():
    valid = {EXPECT, VIEW_LAYER, PENDING, JUSTIFIED_DROP}
    for tpl, row in rows().items():
        assert row['status'] in valid, tpl
        if row['status'] == EXPECT:
            assert row['kinds'], "EXPECT row without kinds: %r" % tpl
        else:
            assert row['kinds'] == (), tpl


def test_rows_cover_the_census_shape():
    # 39 unique templates enumerated in the S27 gate (45 sites; shared
    # templates merge). A game update that adds a line shows up as an
    # unknown-template alarm at runtime, not here; this pins OUR side.
    assert len(rows()) == 39


# ---- Happy path / failure / multiplicity ----

def test_covered_line_with_record_is_silent():
    tel = _FakeTelemetry()
    c = ParityChecker()
    recs = [_rec('game_log', _dmg_deals()), _rec('EventOnDamaged')]
    c.sweep(recs, tel)
    c.sweep(recs, tel)   # next window empty -> no carried failure either
    assert tel.calls == []


def test_missing_record_fails_after_one_window_grace():
    tel = _FakeTelemetry()
    c = ParityChecker()
    recs = [_rec('game_log', _dmg_deals())]      # no EventOnDamaged anywhere
    c.sweep(recs, tel)
    assert tel.calls == []                        # deferred, not yet failed
    c.sweep(recs, tel)                            # window 2: still nothing new
    assert [ev for ev, _ in tel.calls] == ['oracle_parity_fail']
    ev, fields = tel.calls[0]
    assert fields['template'] == _dmg_deals()
    assert fields['missing'] == 1
    c.sweep(recs, tel)                            # failed once, dropped
    assert len(tel.calls) == 1


def test_straddle_counterpart_in_next_window_no_failure():
    tel = _FakeTelemetry()
    c = ParityChecker()
    c.sweep([_rec('game_log', _dmg_deals())], tel)
    c.sweep([_rec('EventOnDamaged')], tel)        # arrives one window late
    c.sweep([], tel)
    assert tel.calls == []


def test_multiplicity_two_lines_need_two_records():
    tel = _FakeTelemetry()
    c = ParityChecker()
    recs = [_rec('game_log', _dmg_deals()), _rec('game_log', _dmg_deals()),
            _rec('EventOnDamaged')]
    c.sweep(recs, tel)
    c.sweep([], tel)
    fails = [(ev, f) for ev, f in tel.calls if ev == 'oracle_parity_fail']
    assert len(fails) == 1 and fails[0][1]['missing'] == 1

    tel2 = _FakeTelemetry()
    c2 = ParityChecker()
    recs2 = [_rec('game_log', _dmg_deals()), _rec('game_log', _dmg_deals()),
             _rec('EventOnDamaged'), _rec('EventOnDamaged')]
    c2.sweep(recs2, tel2)
    c2.sweep([], tel2)
    assert tel2.calls == []


def test_any_of_kind_group_satisfies():
    import text
    tel = _FakeTelemetry()
    c = ParityChecker()
    # DMG_BLOCKED expects shield_blocked OR EventOnShieldRemoved.
    c.sweep([_rec('game_log', text.DMG_BLOCKED),
             _rec('EventOnShieldRemoved')], tel)
    c.sweep([], tel)
    assert tel.calls == []


# ---- Unknown-template alarm ----

def test_unknown_template_alarms_once_per_template():
    tel = _FakeTelemetry()
    c = ParityChecker()
    c.sweep([_rec('game_log', "a brand new EA line")], tel)
    c.sweep([_rec('game_log', "a brand new EA line")], tel)
    alarms = [(ev, f) for ev, f in tel.calls
              if ev == 'oracle_unknown_template']
    assert len(alarms) == 1
    assert alarms[0][1]['template'] == "a brand new EA line"


# ---- Exempt statuses ----

def test_view_pending_drop_rows_never_fail():
    tel = _FakeTelemetry()
    c = ParityChecker()
    view_tpl, _ = _tpl(VIEW_LAYER)
    pending_tpl, _ = _tpl(PENDING)
    drop_tpl, _ = _tpl(JUSTIFIED_DROP)
    c.sweep([_rec('game_log', view_tpl),
             _rec('game_log', pending_tpl),
             _rec('game_log', drop_tpl)], tel)
    c.sweep([], tel)
    assert tel.calls == []


# ---- Gating and cursor ----

def test_disabled_seam_is_a_noop():
    tel = _FakeTelemetry(enabled=False)
    c = ParityChecker()
    c.sweep([_rec('game_log', "anything")], tel)
    assert tel.calls == []
    assert c._cursor == 0        # untouched: nothing was processed


def test_cursor_never_resweeps_old_records():
    tel = _FakeTelemetry()
    c = ParityChecker()
    recs = [_rec('game_log', "a brand new EA line 2")]
    c.sweep(recs, tel)
    c._unknown_seen.clear()       # if the line were re-swept it would re-alarm
    c.sweep(recs, tel)
    alarms = [ev for ev, _ in tel.calls if ev == 'oracle_unknown_template']
    assert len(alarms) == 1


def test_reset_drops_carry_and_unknown_dedupe_but_not_position():
    tel = _FakeTelemetry()
    c = ParityChecker()
    # An unmet expectation is pending carry-over...
    c.sweep([_rec('game_log', _dmg_deals()),
             _rec('game_log', "weird line")], tel)
    assert len(c._carry) == 1
    c.reset()                     # level transition / post-load
    assert c._carry == []         # ...dropped, never fails across the boundary
    c.sweep([], tel)
    assert [ev for ev, _ in tel.calls] == ['oracle_unknown_template']
    # Post-reset (journal.reset empties records; sequence keeps climbing):
    # new records sit above the cursor and are seen exactly once.
    c.sweep([_rec('game_log', "weird line")], tel)   # dedupe cleared by reset
    alarms = [ev for ev, _ in tel.calls if ev == 'oracle_unknown_template']
    assert len(alarms) == 2


def test_reset_then_new_records_not_skipped():
    tel = _FakeTelemetry()
    c = ParityChecker()
    c.sweep([_rec('game_log', _dmg_deals()), _rec('EventOnDamaged')], tel)
    c.reset()
    # Simulates the post-journal.reset world: old records gone, sequence
    # monotonic. The new window must be fully processed.
    c.sweep([_rec('game_log', _dmg_deals())], tel)
    c.sweep([], tel)
    assert [ev for ev, _ in tel.calls] == ['oracle_parity_fail']
