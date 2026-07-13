# Tests for slice 4 of the cursor-tool pass: the unified pin system.
#
# Behaviors pinned (CURSOR_TOOL_UX_PASS.md, owner-ruled 2026-07-06):
# - ONE pin list, cycled on K in category blocks (enemies, allies,
#   landmarks, bookmarks) with NO interleaving; proximity-ordered within
#   each block; count header on a fresh cycle; Ctrl+K block skip.
# - Alt+K universal toggle: unit pins match by identity, tile pins by
#   coordinates; fresh pins auto-focus.
# - Focus hands off to the most recently pinned survivor when the focused
#   pin dies or is unpinned.
# - Per-level storage: pins never cross realms; deploy pins attach to
#   next_level (the store is weak-keyed by level object).
# - Expiry: unit pins die with the unit, landmark pins expire with the
#   prop, bookmarks never expire.
# - Cull-while-cycling: dropping the just-spoken pin keeps the cycle's
#   position sane and recomputes block starts.

import sys
import textwrap
import types
import weakref
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
GAME = MOD.parents[1]
for p in (str(GAME), str(MOD)):
    if p not in sys.path:
        sys.path.insert(0, p)

sys.modules.setdefault('steamworks', types.ModuleType('steamworks'))

from helpers import (order_pins_in_blocks, pin_count_header, pin_block_label,
                     focus_handoff, pin_matches,
                     _direction_offset, _cardinal_direction)

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


# ---- Pure helpers: block ordering ----

def test_blocks_follow_category_order_not_input_order():
    tagged = [('bookmark', 1.0, 'b1'), ('enemy', 5.0, 'e1'), ('ally', 2.0, 'a1')]
    ordered, starts = order_pins_in_blocks(tagged)
    assert [pin for pin, _cat in ordered] == ['e1', 'a1', 'b1']
    assert [cat for _pin, cat in ordered] == ['enemy', 'ally', 'bookmark']


def test_proximity_orders_within_block_only():
    # A far enemy still precedes a near ally: no interleaving.
    tagged = [('ally', 1.0, 'a-near'), ('enemy', 9.0, 'e-far'), ('enemy', 2.0, 'e-near')]
    ordered, _starts = order_pins_in_blocks(tagged)
    assert [pin for pin, _cat in ordered] == ['e-near', 'e-far', 'a-near']


def test_block_starts_mark_each_category_boundary():
    tagged = [('enemy', 1.0, 'e1'), ('enemy', 2.0, 'e2'),
              ('landmark', 1.0, 'l1'), ('bookmark', 1.0, 'b1')]
    _ordered, starts = order_pins_in_blocks(tagged)
    assert starts == {0, 2, 3}


def test_distance_ties_break_by_input_order():
    tagged = [('enemy', 3.0, 'first'), ('enemy', 3.0, 'second')]
    ordered, _starts = order_pins_in_blocks(tagged)
    assert [pin for pin, _cat in ordered] == ['first', 'second']


def test_empty_input_yields_empty_cycle():
    ordered, starts = order_pins_in_blocks([])
    assert ordered == [] and starts == set()


def test_count_header_totals_and_pluralizes():
    tagged = [('enemy', 1.0, 'e1'), ('enemy', 2.0, 'e2'),
              ('ally', 1.0, 'a1'), ('bookmark', 1.0, 'b1'), ('bookmark', 2.0, 'b2')]
    ordered, _starts = order_pins_in_blocks(tagged)
    assert pin_count_header(ordered) == "5 pinned. 2 enemies, 1 ally, 2 bookmarks"


def test_block_label_singular_plural():
    assert pin_block_label('enemy', 1) == 'enemy'
    assert pin_block_label('enemy', 3) == 'enemies'
    assert pin_block_label('ally', 2) == 'allies'


# ---- Pure helpers: focus + toggle identity ----

def test_focus_hands_off_to_most_recent_survivor():
    pins = [{'seq': 1}, {'seq': 7}, {'seq': 3}]
    assert focus_handoff(pins) is pins[1]
    assert focus_handoff([]) is None


def test_unit_pins_match_by_identity_not_equality():
    class U:
        def __eq__(self, other):
            return True  # pathological equality must not fool the toggle

        def __hash__(self):
            return 0

    u1, u2 = U(), U()
    pin = {'kind': 'unit', 'unit': u1, 'x': 0, 'y': 0}
    assert pin_matches(pin, 'unit', unit=u1)
    assert not pin_matches(pin, 'unit', unit=u2)


def test_tile_pins_match_by_coords_and_kind_separates():
    tile_pin = {'kind': 'tile', 'unit': None, 'x': 4, 'y': 5}
    assert pin_matches(tile_pin, 'tile', x=4, y=5)
    assert not pin_matches(tile_pin, 'tile', x=4, y=6)
    assert not pin_matches(tile_pin, 'unit', unit=None)


# ---- Extracted pin core (storage, validity, category, focus, naming) ----

def _name_stub(obj, fallback="something"):
    return getattr(obj, 'name', fallback) or fallback


_core_ns = {
    'weakref': weakref,
    '_name': _name_stub,
    'pin_matches': pin_matches,
    'focus_handoff': focus_handoff,
    'Level': types.SimpleNamespace(
        are_hostile=lambda player, unit: getattr(unit, 'team', 0) != getattr(player, 'team', 0)),
}
exec(_extract("    _last_scanned_target = [None]",
              "    def _speak_pin(text):"), _core_ns)


class FakeUnit:
    def __init__(self, name, x=0, y=0, team=1):
        self.name = name
        self.x = x
        self.y = y
        self.team = team


class FakeTile:
    def __init__(self, prop=None, cloud=None, wall=False, chasm=False):
        self.prop = prop
        self.cloud = cloud
        self._wall = wall
        self.is_chasm = chasm

    def is_wall(self):
        return self._wall


class FakeLevel:
    def __init__(self, w=8, h=8):
        self.units = []
        self.tiles = [[FakeTile() for _y in range(h)] for _x in range(w)]


def test_pin_lists_are_per_level():
    lv1, lv2 = FakeLevel(), FakeLevel()
    pins1 = _core_ns['_pins_for'](lv1, create=True)
    pins1.append(_core_ns['_make_pin']('tile', name='Bookmark 1', x=1, y=1,
                                       cat_hint='bookmark'))
    assert _core_ns['_pins_for'](lv2) == []
    assert len(_core_ns['_pins_for'](lv1)) == 1
    assert _core_ns['_pins_for'](None) == []


def test_unit_pin_expires_with_unit_bookmark_never():
    lv = FakeLevel()
    u = FakeUnit("Imp", 2, 2)
    lv.units.append(u)
    unit_pin = _core_ns['_make_pin']('unit', unit=u)
    mark_pin = _core_ns['_make_pin']('tile', name='Bookmark 1', x=3, y=3,
                                     cat_hint='bookmark')
    assert _core_ns['_pin_alive'](lv, unit_pin)
    lv.units.remove(u)
    assert not _core_ns['_pin_alive'](lv, unit_pin)
    assert _core_ns['_pin_alive'](lv, mark_pin)


def test_landmark_pin_expires_when_prop_leaves_tile():
    lv = FakeLevel()
    lv.tiles[4][4].prop = types.SimpleNamespace(name="Rift")
    pin = _core_ns['_make_pin']('tile', name='Rift', x=4, y=4, cat_hint='landmark')
    assert _core_ns['_pin_alive'](lv, pin)
    lv.tiles[4][4].prop = None
    assert not _core_ns['_pin_alive'](lv, pin)


def test_unit_category_is_live_charm_migrates_blocks():
    player = FakeUnit("Wizard", team=0)
    u = FakeUnit("Imp", team=1)
    pin = _core_ns['_make_pin']('unit', unit=u)
    assert _core_ns['_pin_category'](pin, player) == 'enemy'
    u.team = 0  # charmed
    assert _core_ns['_pin_category'](pin, player) == 'ally'
    tile_pin = _core_ns['_make_pin']('tile', name='Rift', x=1, y=1,
                                     cat_hint='landmark')
    assert _core_ns['_pin_category'](tile_pin, player) == 'landmark'


def test_is_pinned_reads_scan_target_forms():
    lv = FakeLevel()
    u = FakeUnit("Imp", 2, 2)
    lv.units.append(u)
    pins = _core_ns['_pins_for'](lv, create=True)
    pins.append(_core_ns['_make_pin']('unit', unit=u))
    pins.append(_core_ns['_make_pin']('tile', name='Rift', x=5, y=5,
                                      cat_hint='landmark'))
    assert _core_ns['_is_pinned'](lv, u)
    assert _core_ns['_is_pinned'](lv, ('Rift', 5, 5))
    assert not _core_ns['_is_pinned'](lv, ('Rift', 5, 6))
    assert not _core_ns['_is_pinned'](lv, FakeUnit("Imp", 2, 2))


def test_remove_focused_pin_hands_focus_to_most_recent():
    lv = FakeLevel()
    u1, u2, u3 = FakeUnit("A"), FakeUnit("B"), FakeUnit("C")
    lv.units.extend([u1, u2, u3])
    pins = _core_ns['_pins_for'](lv, create=True)
    p1 = _core_ns['_make_pin']('unit', unit=u1)
    p2 = _core_ns['_make_pin']('unit', unit=u2)
    p3 = _core_ns['_make_pin']('unit', unit=u3)
    pins.extend([p1, p2, p3])
    _core_ns['_set_focus'](lv, p3)
    clause = _core_ns['_remove_pin'](lv, p3)
    assert _core_ns['_focused_pin'](lv) is p2
    assert clause == " Focus: B."
    # Removing a non-focused pin moves nothing and says nothing.
    clause = _core_ns['_remove_pin'](lv, p1)
    assert clause == ""
    assert _core_ns['_focused_pin'](lv) is p2
    # Last one out: focus clears, no dangling heir.
    clause = _core_ns['_remove_pin'](lv, p2)
    assert clause == ""
    assert _core_ns['_focused_pin'](lv) is None


def test_bookmark_names_from_tile_content_with_numbered_fallback():
    lv = FakeLevel()
    lv.tiles[1][1].prop = types.SimpleNamespace(name="Mega Chest")
    lv.tiles[2][2]._wall = True
    assert _core_ns['_bookmark_name'](lv, 1, 1) == "Bookmark, Mega Chest"
    assert _core_ns['_bookmark_name'](lv, 2, 2) == "Bookmark, wall"
    n1 = _core_ns['_bookmark_name'](lv, 3, 3)
    n2 = _core_ns['_bookmark_name'](lv, 4, 4)
    assert n1.startswith("Bookmark ") and n2.startswith("Bookmark ")
    assert n1 != n2


# ---- Extracted cycle machinery: cull + block skip ----

_cycle_ns = dict(_core_ns)
exec(_extract("    class CycleScanner:",
              "    _enemy_scanner = CycleScanner"), _cycle_ns)
exec(_extract("    _pin_scanner = CycleScanner(\"pins\")",
              "\n    def _pins_for"), _cycle_ns)
exec(_extract("    def _pin_cycle_drop(pin):",
              "    def _query_pins(view"), _cycle_ns)


def _load_cycle(pins_with_cats):
    scanner = _cycle_ns['_pin_scanner']
    scanner.turn_reset()
    ordered, starts = order_pins_in_blocks(
        [(cat, float(i), pin) for i, (pin, cat) in enumerate(pins_with_cats)])
    scanner.set_list(ordered, types.SimpleNamespace(x=0, y=0))
    _cycle_ns['_pin_block_starts'][0] = starts
    return scanner


def test_cull_while_cycling_keeps_position_and_recomputes_starts():
    a, b, c = {'seq': 1}, {'seq': 2}, {'seq': 3}
    scanner = _load_cycle([(a, 'enemy'), (b, 'enemy'), (c, 'bookmark')])
    scanner.advance()          # speaks a (idx 0), _idx -> 1
    scanner.advance()          # speaks b (idx 1), _idx -> 2
    _cycle_ns['_pin_cycle_drop'](b)
    assert [p for p, _c in scanner.items] == [a, c]
    assert scanner._idx == 1   # continuing lands on c, not a repeat
    assert _cycle_ns['_pin_block_starts'][0] == {0, 1}
    idx, total, _sc = scanner.advance()
    assert scanner.items[idx][0] is c and total == 2


def test_drop_of_unlisted_pin_is_noop():
    a = {'seq': 1}
    scanner = _load_cycle([(a, 'enemy')])
    _cycle_ns['_pin_cycle_drop']({'seq': 99})
    assert len(scanner.items) == 1


def test_block_skip_walks_block_starts_and_wraps():
    pins = [({'seq': i}, cat) for i, cat in
            enumerate(['enemy', 'enemy', 'ally', 'bookmark', 'bookmark'])]
    _load_cycle(pins)
    adv = _cycle_ns['_pin_block_advance']
    idx, total, show = adv(False, True)      # fresh: first block
    assert (idx, total, show) == (0, 5, False)
    idx, _t, _s = adv(False, False)          # -> ally block
    assert idx == 2
    idx, _t, _s = adv(False, False)          # -> bookmark block
    assert idx == 3
    idx, _t, _s = adv(False, False)          # wraps to enemies
    assert idx == 0


def test_block_skip_reverse_lands_on_current_block_start_first():
    pins = [({'seq': i}, cat) for i, cat in
            enumerate(['enemy', 'enemy', 'ally', 'bookmark'])]
    scanner = _load_cycle(pins)
    scanner.advance()   # idx 0
    scanner.advance()   # idx 1 (mid-enemy-block)
    idx, _t, _s = _cycle_ns['_pin_block_advance'](True, False)
    assert idx == 0     # start of the current block (word-nav semantics)
    idx, _t, _s = _cycle_ns['_pin_block_advance'](True, False)
    assert idx == 3     # wraps backward to the last block


# ---- Extracted per-turn update line ----

def _update_ns(pathfind, path=None):
    ns = dict(_core_ns)
    ns['cfg'] = types.SimpleNamespace(pathfind_marked=pathfind)
    ns['_direction_offset'] = _direction_offset
    ns['_cardinal_direction'] = _cardinal_direction
    ns['_compute_mark_path'] = lambda level, player, target: (path, 'terrain')
    ns['_mark_hp_clause'] = lambda target: (
        f", {target.cur_hp} HP" if getattr(target, 'cur_hp', None) is not None else "")
    exec(_extract("    def _pin_update_line(level, player, pin):",
                  "    def _speak_pin_turn_updates(view):"), ns)
    return ns


class _SeeLevel:
    def __init__(self, visible=True):
        self.visible = visible

    def can_see(self, *_args):
        return self.visible


def test_update_line_plain_form_and_los_transitions():
    ns = _update_ns(pathfind=False)
    lv = _SeeLevel(visible=True)
    player = FakeUnit("Wizard", 0, 0, team=0)
    u = FakeUnit("Imp", 0, 5)
    pin = ns['_make_pin']('unit', unit=u)
    # First report while visible: no LoS tag.
    assert ns['_pin_update_line'](lv, player, pin) == "Pinned: Imp, 5 south"
    # Transition to blocked speaks it; steady state stays quiet.
    lv.visible = False
    assert ns['_pin_update_line'](lv, player, pin).endswith(", blocked")
    assert ns['_pin_update_line'](lv, player, pin) == "Pinned: Imp, 5 south"
    # Transition back speaks "in sight".
    lv.visible = True
    assert ns['_pin_update_line'](lv, player, pin).endswith(", in sight")


def test_update_line_first_report_blocked_speaks():
    ns = _update_ns(pathfind=False)
    player = FakeUnit("Wizard", 0, 0, team=0)
    pin = ns['_make_pin']('unit', unit=FakeUnit("Imp", 0, 5))
    line = ns['_pin_update_line'](_SeeLevel(visible=False), player, pin)
    assert line == "Pinned: Imp, 5 south, blocked"


def test_update_line_pathfind_speaks_next_step_and_hp():
    P = types.SimpleNamespace
    path = [P(x=0, y=0), P(x=0, y=1), P(x=0, y=2)]
    ns = _update_ns(pathfind=True, path=path)
    player = FakeUnit("Wizard", 0, 0, team=0)
    u = FakeUnit("Imp", 0, 5)
    u.cur_hp = 12
    pin = ns['_make_pin']('unit', unit=u)
    assert ns['_pin_update_line'](_SeeLevel(), player, pin) == "South to Imp, 12 HP."


def test_update_line_pathfind_adjacent_silent_no_path_named():
    ns = _update_ns(pathfind=True, path=None)
    player = FakeUnit("Wizard", 0, 0, team=0)
    adjacent = ns['_make_pin']('unit', unit=FakeUnit("Imp", 1, 1))
    assert ns['_pin_update_line'](_SeeLevel(), player, adjacent) is None
    far = ns['_make_pin']('unit', unit=FakeUnit("Imp", 0, 5))
    assert ns['_pin_update_line'](_SeeLevel(), player, far) == "No path to Imp."


# ---- Wiring pins (source shape) ----

def test_alt_k_dispatch_and_cycle_wiring_present():
    assert "elif evt.key == pygame.K_k:" in _src
    k_branch = _src[_src.index("elif evt.key == pygame.K_k:"):]
    k_branch = k_branch[:k_branch.index("\n                elif ")]
    assert "_toggle_pin(self)" in k_branch
    assert "_query_pins(self," in k_branch
    assert "block_skip=bool(mods & pygame.KMOD_CTRL)" in k_branch


def test_pin_scanner_joins_reset_and_turn_reset_machinery():
    assert "if evt.key != pygame.K_k:\n                    _pin_scanner.reset()" in _src
    assert _src.count("_pin_scanner.turn_reset()") == 2


def test_turn_sites_call_pin_updates_not_the_old_mark():
    assert _src.count("_speak_pin_turn_updates(self)") == 2
    assert "_speak_mark_turn_update" not in _src
    assert "_marked_target" not in _src


def test_scan_formatters_tag_pinned():
    assert _src.count('", pinned" if _is_pinned(level, unit)') == 3
    assert '_is_pinned(level, (name, tx, ty))' in _src


def test_shift_p_targets_focused_pin():
    assert "_query_path_to_focused_pin(self)" in _src
    assert "_query_path_to_marked_target" not in _src


def test_bare_p_falls_back_to_last_scanned_read_only():
    # Dispatch stays two-way: Shift to the focused pin, plain to the cursor
    # query — the last-scanned fallback lives inside the cursor query, not
    # on its own chord (Ctrl+P was built then retired pre-ship, 2026-07-13).
    assert "elif evt.key == pygame.K_p:" in _src
    p_branch = _src[_src.index("elif evt.key == pygame.K_p:"):]
    p_branch = p_branch[:p_branch.index("\n                elif ")]
    assert "_query_path_to_focused_pin(self)" in p_branch
    assert "_query_path_to_cursor(self)" in p_branch
    assert "_query_path_to_last_scanned(self)" not in p_branch
    # No cursor = fall through to the last spoken scan result, not a
    # dead-end error line.
    cur = _src[_src.index("def _query_path_to_cursor(view):"):]
    cur = cur[:cur.index("\n    def ")]
    assert "_query_path_to_last_scanned(view)" in cur
    assert "No cursor target" not in cur
    # Read-only contract: the fallback never pins, never moves the cursor.
    fn = _src[_src.index("def _query_path_to_last_scanned(view):"):]
    fn = fn[:fn.index("\n    def ")]
    assert "_make_pin" not in fn
    assert "_set_focus" not in fn
    assert "cur_spell_target" not in fn
    assert "choose_spell" not in fn
    assert "_announce_mark_full_path(view, target)" in fn


def test_pin_speak_all_setting_declared():
    assert "'pin_speak_all', 'false'" in _src
    assert "pin_speak_all = _settings.getboolean" in _src
