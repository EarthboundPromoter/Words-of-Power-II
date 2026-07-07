# Tests for slice 2 of the cursor-tool pass: movement hygiene.
#
# Three behaviors pinned (CURSOR_TOOL_UX_PASS.md, owner-ruled 2026-07-06):
# - Shift+move speaks the LANDING through the normal announcer plus one
#   compressed "Crossed:" summary (was: all four tiles in sequence). Floor
#   COUNTS (owner: dropping it makes the player do arithmetic); duplicates
#   group with digit counts; a move clamped short by the map edge appends
#   "Edge", and a move pinned at the edge (no step possible) speaks "Edge"
#   alone — silence is a bad state (framework).
# - LCtrl speech cancel is handled ABOVE the modifier-skip guard (the guard
#   `continue`s on K_LCTRL, which made the old dispatch-chain branch dead
#   code) and above the scanner resets, so a mid-cycle cancel never breaks
#   cycling.
# - Shift+RCtrl diagonals step 4 tiles (parity with the game's numpad
#   diagonals), sharing the same landing+summary voice.

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

from helpers import _compress_crossed

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


# ---- _compress_crossed (pure helper) ----

def test_empty_labels_yield_empty_summary():
    assert _compress_crossed([]) == ""


def test_singles_join_in_order():
    assert _compress_crossed(["web", "Imp"]) == "web, Imp"


def test_duplicates_group_with_digit_counts():
    assert _compress_crossed(["web", "web", "Imp"]) == "2 webs, Imp"


def test_first_appearance_order_survives_grouping():
    assert _compress_crossed(["Imp", "web", "Imp"]) == "2 Imps, web"


# ---- _crossed_tile_label (extracted from the installer closure) ----

def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


_ns = {
    '_name': lambda obj, fallback="something": getattr(obj, 'name', fallback) or fallback,
}
exec(_extract("    def _crossed_tile_label(level, point):",
              "    def _x4_finalize(view):"), _ns)
_label = _ns['_crossed_tile_label']


class _Tile:
    def __init__(self, unit=None, prop=None, cloud=None, wall=False, chasm=False):
        self.unit = unit
        self.prop = prop
        self.cloud = cloud
        self._wall = wall
        self.is_chasm = chasm

    def is_wall(self):
        return self._wall


class _Grid:
    def __init__(self, tile):
        self.tiles = [[tile]]


_P = types.SimpleNamespace


def _named(name):
    return types.SimpleNamespace(name=name)


def test_unit_outranks_prop_and_cloud():
    tile = _Tile(unit=_named("Imp"), prop=_named("Rift"), cloud=_named("web"))
    assert _label(_Grid(tile), _P(x=0, y=0)) == "Imp"


def test_prop_then_cloud_then_terrain():
    assert _label(_Grid(_Tile(prop=_named("Rift"))), _P(x=0, y=0)) == "Rift"
    assert _label(_Grid(_Tile(cloud=_named("web"))), _P(x=0, y=0)) == "web"
    assert _label(_Grid(_Tile(wall=True)), _P(x=0, y=0)) == "wall"
    assert _label(_Grid(_Tile(chasm=True)), _P(x=0, y=0)) == "chasm"


def test_plain_floor_counts_as_floor():
    assert _label(_Grid(_Tile()), _P(x=0, y=0)) == "floor"


def test_out_of_bounds_is_dropped():
    assert _label(_Grid(_Tile()), _P(x=5, y=5)) is None


# ---- _x4_finalize (extracted): landing + summary + edge rulings ----

class _FakeTTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


_tts = _FakeTTS()
_x4 = [None]
_look_announced = []

_fns = {
    '_name': _ns['_name'],
    '_crossed_tile_label': _label,
    '_compress_crossed': _compress_crossed,
    '_x4_move': _x4,
    'async_tts': _tts,
    'log': lambda *a, **k: None,
    '_announce_look_tile': lambda v, p: _look_announced.append(p),
    '_announce_target_tile': lambda v, p: _look_announced.append(p),
    '_announce_deploy_tile': lambda v, p: _look_announced.append(p),
}
exec(_extract("    def _x4_finalize(view):",
              "    def patched_try_examine_tile(self, point):"), _fns)
_finalize = _fns['_x4_finalize']


LookSpell = type('LookSpell', (), {})  # the finalize branches on the type NAME


def _view(tile):
    game = types.SimpleNamespace(deploying=False, cur_level=_Grid(tile),
                                 next_level=None)
    return types.SimpleNamespace(game=game, cur_spell=LookSpell(),
                                 deploy_target=None)


def _pt(x=0, y=0):
    return _P(x=x, y=y)


def _run(points, expected):
    _tts.spoken.clear()
    _look_announced.clear()
    _x4[0] = {'points': points, 'expected': expected}
    _finalize(_view(_Tile()))  # every collected tile reads as plain floor


def test_full_move_speaks_landing_then_crossed_floors():
    _run([_pt(), _pt(), _pt(), _pt()], expected=4)
    assert len(_look_announced) == 1  # landing through the normal announcer
    assert _tts.spoken == ["Crossed: 3 floors"]


def test_clamped_move_appends_edge():
    _run([_pt(), _pt()], expected=4)
    assert _tts.spoken == ["Crossed: floor. Edge"]


def test_single_step_clamp_speaks_bare_edge_after_landing():
    _run([_pt()], expected=4)
    assert len(_look_announced) == 1
    assert _tts.spoken == ["Edge"]


def test_pinned_at_edge_speaks_edge_and_no_landing():
    _run([], expected=4)
    assert _look_announced == []
    assert _tts.spoken == ["Edge"]


def test_finalize_always_disarms_the_collector():
    _run([_pt()], expected=4)
    assert _x4[0] is None


# ---- source pins: cancel placement + dispatch shape ----

def _hotkey_loop_src():
    start = _src.index("        try:\n            for evt in self.events:")
    end = _src.index('            log(f"[Hotkey] Error: {e}")', start)
    return _src[start:end]


def test_cancel_sits_above_the_modifier_guard_and_the_scanner_resets():
    # The old branch died because the modifier guard continues on Ctrl keys
    # before dispatch; the fix must run before the guard AND before the
    # scanner resets (cancel mid-cycle must not break cycling). Cancel is
    # side-agnostic — either Ctrl (the Ctrl-idiom ruling).
    loop = _hotkey_loop_src()
    cancel = loop.index("if evt.key in (pygame.K_LCTRL, pygame.K_RCTRL):")
    guard = loop.index("if evt.key in (pygame.K_LSHIFT")
    resets = loop.index("# Reset scan cycling")
    assert cancel < guard < resets


def test_dead_dispatch_cancel_branch_is_gone():
    assert "elif evt.key == pygame.K_LCTRL:" not in _src


def test_cancel_clears_speech_and_continues():
    loop = _hotkey_loop_src()
    cancel = loop.index("if evt.key in (pygame.K_LCTRL, pygame.K_RCTRL):")
    block = loop[cancel:loop.index("# Skip modifier-only keys", cancel)]
    for required in ("async_tts.cancel()", "_cancel_hp_announcement()",
                     "batcher.clear()", "continue"):
        assert required in block, required


def test_rctrl_diagonal_block_is_retired():
    # Ctrl-idiom conversion (owner-ruled 2026-07-06): diagonals are
    # two-arrow chords; RCtrl gestures and the AltGr synonym are gone.
    assert "_RCTRL_DIAG_MAP" not in _src


def test_chording_is_wired():
    assert "_chord_process(self, deploying)" in _src
    assert "[Chord] diagonal" in _src


def test_examine_hook_collects_during_shift_move():
    assert "_x4_move[0]['points'].append" in _src


def test_finalize_runs_in_the_remaining_x4_paths():
    # game-path post-original and the fake-shift repair use (self);
    # the chord path finalizes through _chord_step's (view). The (view)
    # count includes the def line itself, hence 2.
    assert _src.count("_x4_finalize(self)") == 2
    assert _src.count("_x4_finalize(view)") == 2


# ---- fake-shift detection (Shift+numpad repair) ----
# The driver strips Shift from NumLock-on numpad presses (fake shift): a
# Shift KEYUP arrives in the same batch, BEFORE the numpad make, while Shift
# was down entering the frame. Detection is order-sensitive by design — a
# genuine press-then-release has the opposite ordering and must not match.

from helpers import _bound_keys  # noqa: E402

# pygame isn't installed in the test venv; the detector only compares keycode
# identities, so a stub namespace with distinct ints is a faithful double.
pygame = types.SimpleNamespace(
    K_KP1=1, K_KP2=2, K_KP3=3, K_KP4=4, K_KP6=6, K_KP7=7, K_KP8=8, K_KP9=9,
    K_LSHIFT=100, K_RSHIFT=101,
    K_UP=50, K_DOWN=51, K_LEFT=52, K_RIGHT=53,
    KEYUP=200, KEYDOWN=201)

# Mirrors the real table's shape: orthogonals first (chording reads
# _KB_DIRS[:4]), diagonals after; numpad keys ride the orthogonal bind
# lists' second slots, as in the game's defaults. KP4 is deliberately
# left unbound to keep an unbound-numpad-key case.
_KB_DIRS_TEST = (
    (0, pygame.K_UP, (0, -1)),
    (1, pygame.K_DOWN, (0, 1)),
    (2, pygame.K_LEFT, (-1, 0)),
    (3, pygame.K_RIGHT, (1, 0)),
    (4, pygame.K_KP7, (-1, -1)),
    (5, pygame.K_KP9, (1, -1)),
)

_fk = {
    '_pg_keybind': pygame,
    '_KB_DIRS': _KB_DIRS_TEST,
    '_bound_keys': _bound_keys,
}
exec(_extract("    _NUMPAD_DIR_KEYS = frozenset((",
              "    def _crossed_tile_label(level, point):"), _fk)
_detect = _fk['_fake_shift_numpad_dirs']
_vec_for = _fk['_chord_vec_for']

_VIEW = types.SimpleNamespace(key_binds={
    0: [pygame.K_UP, pygame.K_KP8],
    1: [pygame.K_DOWN, pygame.K_KP2],
    2: [pygame.K_LEFT, None],
    3: [pygame.K_RIGHT, None],
    4: [pygame.K_KP7, None],
    5: [pygame.K_KP9, None],
})


def _up(key):
    return types.SimpleNamespace(type=pygame.KEYUP, key=key)


def _down(key):
    return types.SimpleNamespace(type=pygame.KEYDOWN, key=key)


def test_fake_shift_signature_detected():
    events = [_up(pygame.K_LSHIFT), _down(pygame.K_KP8)]
    assert _detect(_VIEW, events, shift_entry=True) == [(events[1], (0, -1))]


def test_no_entry_shift_means_no_match():
    events = [_up(pygame.K_LSHIFT), _down(pygame.K_KP8)]
    assert _detect(_VIEW, events, shift_entry=False) == []


def test_genuine_press_then_release_ordering_does_not_match():
    events = [_down(pygame.K_KP8), _up(pygame.K_LSHIFT)]
    assert _detect(_VIEW, events, shift_entry=True) == []


def test_rolled_second_key_without_keyup_marker_does_not_match():
    events = [_down(pygame.K_KP2)]
    assert _detect(_VIEW, events, shift_entry=True) == []


def test_arrow_keys_never_match():
    events = [_up(pygame.K_LSHIFT), _down(pygame.K_UP)]
    assert _detect(_VIEW, events, shift_entry=True) == []


def test_numpad_key_not_bound_to_a_direction_is_skipped():
    events = [_up(pygame.K_LSHIFT), _down(pygame.K_KP4)]  # KP4 unbound above
    assert _detect(_VIEW, events, shift_entry=True) == []


def test_repair_block_consumes_and_tracker_folds():
    assert "_fake_shift_numpad_dirs(self, self.events, _shift_entry)" in _src
    assert "_shift_entry = _phys_shift[0]" in _src


# ---- chord eligibility (bind-following, numpad excluded) ----

def test_chord_eligibility_follows_binds_and_excludes_numpad():
    assert _vec_for(_VIEW, pygame.K_UP) == (0, -1)
    assert _vec_for(_VIEW, pygame.K_RIGHT) == (1, 0)
    # Numpad keys never chord, even when bound to a direction — they have
    # native diagonals, and buffering them would be pure latency.
    assert _vec_for(_VIEW, pygame.K_KP8) is None
    assert _vec_for(_VIEW, pygame.K_KP7) is None
    assert _vec_for(_VIEW, 999) is None


def test_chord_eligibility_survives_a_rebind():
    rebound = types.SimpleNamespace(key_binds={
        0: [60, None], 1: [61, None], 2: [62, None], 3: [63, None],
    })
    assert _vec_for(rebound, 60) == (0, -1)
    assert _vec_for(rebound, pygame.K_UP) is None
