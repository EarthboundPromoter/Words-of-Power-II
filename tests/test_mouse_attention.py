# Tests for mouse-attention arbitration (click reclaim, 2026-07-07).
#
# Behaviors pinned (owner ruling: click-only reclaim, field session
# 2026-07-07 - a parked mouse drew a giant off-wizard LoS overlay and held
# examine hostage on one unit, locking the threat overlay to it):
# - The keyboard owns the battlefield attention point at load.
# - Any KEYDOWN hands attention to the keyboard; a mouse click or wheel
#   hands it back. Mouse MOTION never reclaims (desk bumps and NVDA/JAWS
#   pointer routing move the mouse without expressing intent).
# - While the keyboard owns attention, get_mouse_level_point answers None
#   without consulting the game, so every consumer falls through to its
#   keyboard-native source (held L: deploy target / cursor / wizard).
# - The kill switch (mouse_attention_arbitration=false) restores native
#   behavior unconditionally.
# - The game-side consumer set is pinned: a game update that adds a
#   get_mouse_level_point call site must re-audit the seam.

import sys
import textwrap
import types
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
GAME = MOD.parents[1]
for p in (str(GAME), str(MOD)):
    if p not in sys.path:
        sys.path.insert(0, p)

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    # Snap both ends to line starts so textwrap.dedent sees the span's
    # real (uniform) indentation.
    start = _src.index(marker)
    start = _src.rfind("\n", 0, start) + 1
    end = _src.index(terminator, start)
    end = _src.rfind("\n", 0, end) + 1
    return textwrap.dedent(_src[start:end])


_FAKE_PYGAME = types.SimpleNamespace(
    KEYDOWN=768, KEYUP=769,
    MOUSEMOTION=1024, MOUSEBUTTONDOWN=1025, MOUSEWHEEL=1027,
)


def _evt(evt_type):
    return types.SimpleNamespace(type=evt_type)


class FakeView:
    def get_mouse_level_point(self):
        return "NATIVE_POINT"


def _make_ns(arbitration=True):
    """Exec the arbitration span with spies."""
    logs = []

    class _View(FakeView):
        pass

    ns = {
        'pygame': _FAKE_PYGAME,
        'cfg': types.SimpleNamespace(mouse_attention_arbitration=arbitration),
        'log': logs.append,
        '_PyGameView': _View,
    }
    code = _extract("# ---- Mouse-attention arbitration: click reclaim ----",
                    "# ---- end mouse-attention arbitration ----")
    exec(code, ns)
    ns['_logs'] = logs
    ns['_View'] = _View
    return ns


# ---- Arbiter transitions ----

def test_keyboard_owns_attention_at_load():
    ns = _make_ns()
    assert ns['_mouse_attention'][0] == 'keyboard'


def test_keydown_keeps_keyboard():
    ns = _make_ns()
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.KEYDOWN)])
    assert ns['_mouse_attention'][0] == 'keyboard'


def test_mouse_motion_never_reclaims():
    ns = _make_ns()
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.MOUSEMOTION)] * 5)
    assert ns['_mouse_attention'][0] == 'keyboard'


def test_click_reclaims_for_the_mouse():
    ns = _make_ns()
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.MOUSEBUTTONDOWN)])
    assert ns['_mouse_attention'][0] == 'mouse'


def test_wheel_reclaims_for_the_mouse():
    ns = _make_ns()
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.MOUSEWHEEL)])
    assert ns['_mouse_attention'][0] == 'mouse'


def test_keypress_reclaims_from_the_mouse():
    ns = _make_ns()
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.MOUSEBUTTONDOWN)])
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.KEYDOWN)])
    assert ns['_mouse_attention'][0] == 'keyboard'


def test_last_event_in_batch_wins():
    ns = _make_ns()
    ns['_attention_scan_events']([
        _evt(_FAKE_PYGAME.KEYDOWN), _evt(_FAKE_PYGAME.MOUSEBUTTONDOWN)])
    assert ns['_mouse_attention'][0] == 'mouse'
    ns['_attention_scan_events']([
        _evt(_FAKE_PYGAME.MOUSEBUTTONDOWN), _evt(_FAKE_PYGAME.KEYDOWN)])
    assert ns['_mouse_attention'][0] == 'keyboard'


def test_keyup_is_not_intent():
    # Releasing a key expresses nothing; only presses flip ownership.
    ns = _make_ns()
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.MOUSEBUTTONDOWN)])
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.KEYUP)])
    assert ns['_mouse_attention'][0] == 'mouse'


def test_scan_logs_only_on_change_and_is_idempotent():
    ns = _make_ns()
    base = len(ns['_logs'])
    batch = [_evt(_FAKE_PYGAME.MOUSEBUTTONDOWN)]
    ns['_attention_scan_events'](batch)
    assert len(ns['_logs']) == base + 1
    # Same batch scanned again (draw_screen + level-input both scan): no-op.
    ns['_attention_scan_events'](batch)
    assert len(ns['_logs']) == base + 1
    # Keyboard frames with no events never log.
    ns['_attention_scan_events']([])
    assert len(ns['_logs']) == base + 1


# ---- The patched mouse point ----

def test_keyboard_mode_mutes_the_mouse_point():
    ns = _make_ns()
    view = ns['_View']()
    assert view.get_mouse_level_point() is None


def test_mouse_mode_delegates_to_the_game():
    ns = _make_ns()
    ns['_attention_scan_events']([_evt(_FAKE_PYGAME.MOUSEBUTTONDOWN)])
    view = ns['_View']()
    assert view.get_mouse_level_point() == "NATIVE_POINT"


def test_kill_switch_restores_native_behavior():
    ns = _make_ns(arbitration=False)
    view = ns['_View']()
    assert view.get_mouse_level_point() == "NATIVE_POINT"


def test_reclaim_types_cover_click_and_wheel():
    ns = _make_ns()
    assert _FAKE_PYGAME.MOUSEBUTTONDOWN in ns['_MOUSE_RECLAIM_TYPES']
    assert _FAKE_PYGAME.MOUSEWHEEL in ns['_MOUSE_RECLAIM_TYPES']
    assert _FAKE_PYGAME.MOUSEMOTION not in ns['_MOUSE_RECLAIM_TYPES']


# ---- Consumer-set pin (API drift guard) ----

def test_game_consumer_set_is_pinned():
    """The None-return silently changes behavior for every consumer of
    get_mouse_level_point. Pin the game-side call sites: today they are
    process_level_input (:2709), draw_threat's dead local (:5636),
    draw_los's origin chain (:5702), and highlight_examine_override
    (:5736). A game update that adds or removes one fails here and must
    re-audit the arbitration seam."""
    game_src = (GAME / "RiftWizard3.py").read_text(encoding="utf-8")
    calls = game_src.count("self.get_mouse_level_point()")
    assert calls == 4, (
        f"get_mouse_level_point call sites changed (expected 4, found "
        f"{calls}) - re-audit mouse-attention arbitration")


def test_pygame_bound_before_load_time_evaluation():
    """_MOUSE_RECLAIM_TYPES evaluates at load time, but the scope's shared
    `import pygame` sits below the arbitration section. Pin the dedicated
    pre-section binding (2026-07-07 field load failure: NameError - every
    neighboring section only touches pygame inside deferred functions, so
    nothing else catches this)."""
    marker = _src.index("# ---- Mouse-attention arbitration")
    assert "\n    import pygame\n" in _src[:marker], (
        "the arbitration section needs `import pygame` bound above it")


def test_mod_installs_the_patch():
    assert "_PyGameView.get_mouse_level_point = patched_get_mouse_level_point" in _src
    # Both per-frame flip points feed the arbiter.
    assert _src.count("_attention_scan_events(getattr(self, 'events', None) or [])") == 2
