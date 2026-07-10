# Tests for the spring-look pass (owner-ruled 2026-07-10).
#
# Behaviors pinned:
# - Gesture grammar re-chord: the axis jump is Ctrl+Shift+direction; bare
#   Ctrl+direction steps, conjuring the spring Look from normal play.
# - Spring lifecycle: conjure owns the cursor; the poll holds while Ctrl is
#   physically down (poll, not KEYUP — self-heals across focus loss);
#   release aborts THE SPRING ONLY; V / a spell hotkey replaces cur_spell
#   and the identity check disowns the hold silently (the latch, free).
# - Conjure failure (API drift: no LookSpell) fails safe — the press falls
#   through to the game untouched.
# - Jump receipt: count modes (full = distance-to-landing default,
#   open-space = the span itself), compass on by default, landing-first
#   ordering config, unit/prop true-count carve-out in BOTH modes.
# - Edge: pinned jumps re-announce on every press.

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

from helpers import _bound_keys, _pluralize  # noqa: E402

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


# pygame isn't installed in the test venv; the extracted code only compares
# keycode identities here, so a stub namespace is a faithful double.
pygame = types.SimpleNamespace(
    K_KP1=1, K_KP2=2, K_KP3=3, K_KP4=4, K_KP6=6, K_KP7=7, K_KP8=8, K_KP9=9,
    K_LSHIFT=100, K_RSHIFT=101, K_LCTRL=102, K_RCTRL=103,
    K_UP=50, K_DOWN=51, K_LEFT=52, K_RIGHT=53,
    KEYUP=200, KEYDOWN=201)

_KB_DIRS_TEST = (
    (0, pygame.K_UP, (0, -1)),
    (1, pygame.K_DOWN, (0, 1)),
    (2, pygame.K_LEFT, (-1, 0)),
    (3, pygame.K_RIGHT, (1, 0)),
    (4, pygame.K_KP7, (-1, -1)),
    (5, pygame.K_KP9, (1, -1)),
)


class _FakeTTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


_tts = _FakeTTS()
_cfg = types.SimpleNamespace(jump_coalesce_units=False,
                             jump_count_open_space=False,
                             jump_compass=True,
                             jump_landing_first=False)


class _FakeMain:
    class LookSpell:
        def __init__(self):
            self.caster = None


def _orig_choose(view, spell):
    view.cur_spell = spell


_ns = {
    '_pg_keybind': pygame,
    '_KB_DIRS': _KB_DIRS_TEST,
    '_bound_keys': _bound_keys,
    '_pluralize': _pluralize,
    'cfg': _cfg,
    'Level': types.SimpleNamespace(
        Point=lambda x, y: types.SimpleNamespace(x=x, y=y)),
    'async_tts': _tts,
    'log': lambda *a, **k: None,
    '_name': lambda obj, fallback="something": getattr(obj, 'name', fallback) or fallback,
    '_main': _FakeMain,
    '_original_choose_spell': _orig_choose,
}
exec(_extract("    _NUMPAD_DIR_KEYS = frozenset((",
              "    def _crossed_tile_label(level, point):"), _ns)

_conjure = _ns['_spring_conjure']
_poll = _ns['_spring_poll']
_spring = _ns['_spring']
_dir_vec = _ns['_dir_vec']
_jump = _ns['_jump_step']
_P = _ns['Level'].Point


# ---- spring lifecycle ----

def _view(cur_spell=None):
    v = types.SimpleNamespace(
        game=types.SimpleNamespace(p1=types.SimpleNamespace(x=3, y=3)),
        cur_spell=cur_spell,
        aborted=[])

    def _abort():
        v.aborted.append(True)
        v.cur_spell = None
    v.abort_cur_spell = _abort
    return v


def test_conjure_owns_the_cursor():
    _spring[0] = None
    v = _view()
    assert _conjure(v) is True
    assert v.cur_spell is _spring[0]
    assert isinstance(v.cur_spell, _FakeMain.LookSpell)
    assert v.cur_spell.caster is v.game.p1


def test_poll_holds_while_ctrl_is_down():
    _spring[0] = None
    v = _view()
    _conjure(v)
    assert _poll(v, True) is False
    assert _spring[0] is v.cur_spell
    assert not v.aborted


def test_release_aborts_the_spring():
    _spring[0] = None
    v = _view()
    _conjure(v)
    assert _poll(v, False) is True   # the buffered glance press dies with it
    assert _spring[0] is None
    assert v.aborted


def test_latch_disowns_without_abort():
    # V or a spell hotkey replaces cur_spell through choose_spell (which
    # keeps the cursor position) — the identity check stops matching and
    # releasing Ctrl no longer aborts anything.
    _spring[0] = None
    v = _view()
    _conjure(v)
    v.cur_spell = object()
    assert _poll(v, False) is False
    assert _spring[0] is None
    assert not v.aborted


def test_poll_without_a_spring_is_a_no_op():
    _spring[0] = None
    v = _view(cur_spell=object())
    assert _poll(v, False) is False
    assert not v.aborted


def test_conjure_fails_safe_without_lookspell():
    # API drift: no LookSpell in the main module. The press must fall
    # through to the game untouched (native behavior survives breakage).
    _spring[0] = None
    old = _ns['_main']
    _ns['_main'] = types.SimpleNamespace()
    try:
        v = _view()
        assert _conjure(v) is False
        assert _spring[0] is None
        assert v.cur_spell is None
    finally:
        _ns['_main'] = old


def test_dir_vec_covers_the_whole_rose():
    view = types.SimpleNamespace(key_binds={
        0: [pygame.K_UP, pygame.K_KP8], 1: [pygame.K_DOWN, None],
        2: [pygame.K_LEFT, None], 3: [pygame.K_RIGHT, None],
        4: [pygame.K_KP7, None], 5: [pygame.K_KP9, None]})
    assert _dir_vec(view, pygame.K_KP8) == (0, -1)   # numpad orthogonal
    assert _dir_vec(view, pygame.K_KP7) == (-1, -1)  # numpad diagonal
    assert _dir_vec(view, pygame.K_RIGHT) == (1, 0)
    assert _dir_vec(view, 999) is None


# ---- jump receipt: count modes, compass, order, carve-outs ----

class _Tile:
    def __init__(self, unit=None, prop=None, cloud=None, wall=False,
                 chasm=False):
        self.unit = unit
        self.prop = prop
        self.cloud = cloud
        self._wall = wall
        self.is_chasm = chasm

    def is_wall(self):
        return self._wall


def _named(name):
    return types.SimpleNamespace(name=name)


class _JumpLevel:
    def __init__(self, row):
        self.tiles = [[t] for t in row]  # tiles[x][y], single row at y=0
        self._w = len(row)

    def is_point_in_bounds(self, p):
        return 0 <= p.x < self._w and p.y == 0


def _jump_view(level, x):
    game = types.SimpleNamespace(deploying=False, cur_level=level,
                                 next_level=None)
    v = types.SimpleNamespace(game=game, cur_spell=object(),
                              deploy_target=None,
                              cur_spell_target=_P(x, 0), examined=[])
    v.try_examine_tile = v.examined.append
    return v


def _set_cfg(**flags):
    _cfg.jump_coalesce_units = flags.get('coalesce', False)
    _cfg.jump_count_open_space = flags.get('open_space', False)
    _cfg.jump_compass = flags.get('compass', True)
    _cfg.jump_landing_first = flags.get('landing_first', False)


def _jrun(row, start, **flags):
    _tts.spoken.clear()
    _set_cfg(**flags)
    view = _jump_view(_JumpLevel(row), start)
    _jump(view, False, (1, 0))
    return view


def _imp_row():
    # floor floor floor floor Imp — the imp is FOUR tiles out, three floor
    # tiles between.
    return [_Tile(), _Tile(), _Tile(), _Tile(), _Tile(unit=_named("Imp"))]


def test_full_count_is_distance_to_landing():
    _jrun(_imp_row(), 0)
    assert _tts.spoken == ["4 floor east"]


def test_open_space_counts_the_span_itself():
    _jrun(_imp_row(), 0, open_space=True)
    assert _tts.spoken == ["3 floor east"]


def test_compass_off_drops_the_direction():
    _jrun(_imp_row(), 0, compass=False)
    assert _tts.spoken == ["4 floor"]


def test_landing_first_examine_precedes_the_moved_receipt():
    trace = []
    _set_cfg(landing_first=True)
    view = _jump_view(_JumpLevel(_imp_row()), 0)
    view.try_examine_tile = lambda p: trace.append('landing')
    _tts.speak = trace.append
    try:
        _jump(view, False, (1, 0))
    finally:
        del _tts.speak
        _set_cfg()
    assert trace == ['landing', 'Moved 4 floor east']


def test_span_first_receipt_precedes_the_examine():
    trace = []
    _set_cfg()
    view = _jump_view(_JumpLevel(_imp_row()), 0)
    view.try_examine_tile = lambda p: trace.append('landing')
    _tts.speak = trace.append
    try:
        _jump(view, False, (1, 0))
    finally:
        del _tts.speak
    assert trace == ['4 floor east', 'landing']


def test_chasm_span_names_the_crossing():
    row = [_Tile(chasm=True), _Tile(chasm=True), _Tile(chasm=True), _Tile()]
    _jrun(row, 0)   # origin inside the chasm: stride it to the far floor
    assert _tts.spoken == ["3 chasm east"]


def test_cloud_span_names_the_cloud():
    def gas():
        return _Tile(cloud=_named("Poison Gas"))
    row = [gas(), gas(), gas(), _Tile()]
    _jrun(row, 0)
    assert _tts.spoken == ["3 Poison Gas east"]


def test_unit_span_speaks_true_count_in_both_modes():
    # The carve-out: beings are a census, never inflated by the count mode.
    def row():
        return [_Tile(unit=_named("Imp")), _Tile(unit=_named("Imp")),
                _Tile(unit=_named("Imp")), _Tile()]
    _jrun(row(), 0, coalesce=True)
    assert _tts.spoken == ["past 2 Imps east"]
    _jrun(row(), 0, coalesce=True, open_space=True)
    assert _tts.spoken == ["past 2 Imps east"]


def test_edge_run_keeps_full_count_agreement():
    # Landing IS span content at the map edge: both count modes agree.
    row = [_Tile(), _Tile(), _Tile(), _Tile()]
    _jrun(row, 0)
    assert _tts.spoken == ["3 floor east, edge"]
    _jrun(row, 0, open_space=True)
    assert _tts.spoken == ["3 floor east, edge"]


def test_pinned_edge_reannounces_every_press():
    _tts.spoken.clear()
    _set_cfg()
    view = _jump_view(_JumpLevel([_Tile(), _Tile()]), 1)
    _jump(view, False, (1, 0))
    _jump(view, False, (1, 0))
    assert _tts.spoken == ["Edge", "Edge"]
    assert view.examined == []


# ---- source pins: grammar + wiring ----

def test_jump_is_ctrl_shift_and_spring_is_wired():
    assert ("jumping = bool(ctrl_held and shift_held and "
            "(cursor_ctx or springable))") in _src
    assert "if _spring_poll(view, ctrl_held):" in _src
    assert "if _spring_conjure(view):" in _src


def test_release_drops_the_buffered_glance_press():
    seg_start = _src.index("if _spring_poll(view, ctrl_held):")
    assert "_arrow_buffer[0] = None" in _src[seg_start:seg_start + 400]


def test_wrapper_edge_scan_marks_first_and_self_heals():
    assert "_edge_down[_eevt.key] = True   # mark first: one Edge per press" in _src
    assert "_edge_down = {}" in _src


def test_chord_step_speaks_edge_on_blocked_single_steps():
    seg = _src[_src.index("def _chord_step(view, deploying, vec, x4):"):
               _src.index("# ---- Axis jump")]
    assert seg.count('async_tts.speak("Edge")') == 2  # cursor + deploy branches
