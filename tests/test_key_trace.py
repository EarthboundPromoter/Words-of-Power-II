# Tests for the key trace (input diagnostics, owner-ruled 2026-07-09):
# key_trace_enabled (default OFF) logs raw KEYDOWN/KEYUP edges with
# millisecond spacing, aggregates the game's hand-rolled autowalk repeats
# into the release line, and self-heals against missed KEYUPs. Log-only —
# the scan must never touch view.events.
#
# The functions live nested inside the installer closure in screen_reader.py
# — not importable — so this file extracts their source by signature markers
# and execs them (the test_cursor_routing.py pattern). A renamed/moved
# function breaks extraction LOUDLY at collection.

import re
import textwrap
import types
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


KEYDOWN, KEYUP = 768, 769
KMOD_SHIFT, KMOD_CTRL, KMOD_ALT = 1, 2, 4

_KEY_NAMES = {100: "down", 101: "right", 102: "up", 103: "j"}


class _FakePygame:
    KEYDOWN = KEYDOWN
    KEYUP = KEYUP
    KMOD_SHIFT = KMOD_SHIFT
    KMOD_CTRL = KMOD_CTRL
    KMOD_ALT = KMOD_ALT

    def __init__(self):
        self.pressed = {}
        self.key = types.SimpleNamespace(
            name=lambda k: _KEY_NAMES.get(k, f"key{k}"),
            get_pressed=lambda: self.pressed)


def _evt(etype, key, mod=0):
    return types.SimpleNamespace(type=etype, key=key, mod=mod)


def _ns(enabled=True):
    lines = []
    pg = _FakePygame()
    ns = {
        'cfg': types.SimpleNamespace(key_trace_enabled=enabled),
        'log': lines.append,
        'pygame': pg,
    }
    exec(_extract("    _kt_down = {}",
                  "    # ---- Two-arrow diagonal chording"), ns)
    return ns, lines, pg


def _view(events, pg, held=()):
    # get_pressed must report this frame's END state: held keys down,
    # everything else up (the self-heal reads it after the event loop).
    pg.pressed = {k: (k in held) for k in _KEY_NAMES}
    pg.pressed.update({k: True for k in held})
    return types.SimpleNamespace(events=list(events))


def test_disabled_logs_nothing():
    ns, lines, pg = _ns(enabled=False)
    ns['_key_trace_scan'](_view([_evt(KEYDOWN, 100)], pg, held=(100,)))
    assert lines == []
    ns['_kt']("chord: buffered down")
    assert lines == []


def test_fresh_press_logs_down_and_second_press_carries_the_gap():
    ns, lines, pg = _ns()
    ns['_key_trace_scan'](_view([_evt(KEYDOWN, 100)], pg, held=(100,)))
    assert lines == ["[KeyTrace] down down"]
    ns['_key_trace_scan'](_view([_evt(KEYDOWN, 101)], pg, held=(100, 101)))
    assert len(lines) == 2
    assert re.fullmatch(r"\[KeyTrace\] down right \(\+\d+ms\)", lines[1])


def test_release_carries_hold_duration_and_repeat_count():
    ns, lines, pg = _ns()
    ns['_key_trace_scan'](_view([_evt(KEYDOWN, 100)], pg, held=(100,)))
    # Two synthesized autowalk repeats while held: no extra down lines.
    ns['_key_trace_scan'](_view(
        [_evt(KEYDOWN, 100), _evt(KEYDOWN, 100)], pg, held=(100,)))
    assert len(lines) == 1
    ns['_key_trace_scan'](_view([_evt(KEYUP, 100)], pg))
    assert len(lines) == 2
    assert re.fullmatch(r"\[KeyTrace\] up down after \d+ms, 2 repeats",
                        lines[1])


def test_untracked_release_logs_plain_up():
    ns, lines, pg = _ns()
    ns['_key_trace_scan'](_view([_evt(KEYUP, 103)], pg))
    assert lines == ["[KeyTrace] up j"]


def test_modifier_state_rides_the_down_line():
    ns, lines, pg = _ns()
    ns['_key_trace_scan'](_view(
        [_evt(KEYDOWN, 100, mod=KMOD_SHIFT | KMOD_CTRL)], pg, held=(100,)))
    assert lines == ["[KeyTrace] down down +shift+ctrl"]


def test_missed_keyup_self_heals_so_the_next_press_logs():
    ns, lines, pg = _ns()
    ns['_key_trace_scan'](_view([_evt(KEYDOWN, 100)], pg, held=(100,)))
    # Focus loss ate the KEYUP: next frame the key is physically up with no
    # event. The stale entry must clear, so a fresh press logs a down line
    # instead of counting as a repeat.
    ns['_key_trace_scan'](_view([], pg))
    ns['_key_trace_scan'](_view([_evt(KEYDOWN, 100)], pg, held=(100,)))
    downs = [l for l in lines if l.startswith("[KeyTrace] down down")]
    assert len(downs) == 2


def test_scan_never_touches_events():
    ns, lines, pg = _ns()
    events = [_evt(KEYDOWN, 100), _evt(KEYUP, 103)]
    view = _view(events, pg, held=(100,))
    before = list(view.events)
    ns['_key_trace_scan'](view)
    assert view.events == before


def test_verdict_helper_prefixes_when_enabled():
    ns, lines, _pg = _ns()
    ns['_kt']("chord: buffered down, awaiting an orthogonal partner")
    assert lines == [
        "[KeyTrace] chord: buffered down, awaiting an orthogonal partner"]
