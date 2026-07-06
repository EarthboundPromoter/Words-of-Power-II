# Tests for Shift+Tab reverse target cycling (owner greenlight 2026-07-06,
# requested by Neurrone). The game has no reverse cycle; `_cycle_tab_reverse`
# mirrors cycle_tab_targets (RiftWizard3.py:2361-2379) with the index stepped
# -1, spell contexts only (targeting / walk / look — deploy keeps forward
# Tab). Owner rulings pinned here:
# - cold start (cursor not on a tab target) lands at the END of the list;
# - both directions speak through the same `_announce_tab_cycle` helper, so
#   the reverse read carries the identical "N of M" counter voice;
# - empty target list speaks "No targets", same as forward (2026-07-03
#   cycler-not-selector ruling).
#
# The functions live nested inside the installer closure in screen_reader.py
# — not importable — so, like test_shop_prop.py, this file extracts their
# source by signature markers and execs it. A renamed/moved function breaks
# extraction LOUDLY at collection; it can never pass silently.

import sys
import textwrap
import types
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
GAME = MOD.parents[1]
for p in (str(GAME), str(MOD)):
    if p not in sys.path:
        sys.path.insert(0, p)

sys.modules.setdefault('steamworks', types.ModuleType('steamworks'))
import Game  # noqa: F401  (resolves the Level<->Game import cycle)
import Level

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


class _FakeTTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


_tts = _FakeTTS()

_ns = {
    'Level': Level,
    'async_tts': _tts,
    'log': lambda *a, **k: None,
    '_describe_target': lambda v: "TARGET",
    '_check_aoe_warning': lambda v: ("", "", ""),
    '_aoe_announced_state': [False],
    '_last_examine_xy': [None],
}
exec(_extract("    def _announce_tab_cycle(self):",
              "    _PyGameView.cycle_tab_targets"), _ns)

_reverse = _ns['_cycle_tab_reverse']


class _View:
    def __init__(self, targets, cursor):
        self.tab_targets = targets
        self.cur_spell_target = cursor
        self.deploy_target = None
        self.examined = []

    def try_examine_tile(self, point):
        self.examined.append(point)


_P = Level.Point
_TARGETS = [_P(1, 1), _P(2, 2), _P(3, 3)]


def test_reverse_steps_back_one():
    view = _View(list(_TARGETS), _P(2, 2))
    _tts.spoken.clear()
    _reverse(view)
    assert view.cur_spell_target == _P(1, 1)
    assert view.examined == [_P(1, 1)]
    assert _tts.spoken == ["1 of 3. TARGET"]


def test_reverse_wraps_from_first_to_last():
    view = _View(list(_TARGETS), _P(1, 1))
    _tts.spoken.clear()
    _reverse(view)
    assert view.cur_spell_target == _P(3, 3)
    assert _tts.spoken == ["3 of 3. TARGET"]


def test_cold_start_lands_at_end_of_list():
    # Cursor not on any tab target (e.g. targeting just opened, cursor on
    # self) — owner ruling: land at the END. In walk context the list is
    # pickups then portals, so this is the jump-to-the-rifts behavior.
    view = _View(list(_TARGETS), _P(9, 9))
    _tts.spoken.clear()
    _reverse(view)
    assert view.cur_spell_target == _P(3, 3)
    assert _tts.spoken == ["3 of 3. TARGET"]


def test_empty_list_speaks_no_targets_and_moves_nothing():
    view = _View([], _P(9, 9))
    _tts.spoken.clear()
    _reverse(view)
    assert view.cur_spell_target == _P(9, 9)
    assert view.examined == []
    assert _tts.spoken == ["No targets"]


def test_two_reverses_walk_the_list_backward():
    view = _View(list(_TARGETS), _P(3, 3))
    _tts.spoken.clear()
    _reverse(view)
    _reverse(view)
    assert view.cur_spell_target == _P(1, 1)
    assert _tts.spoken == ["2 of 3. TARGET", "1 of 3. TARGET"]
