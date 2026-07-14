# Tests for slice 5 of the cursor-tool pass: cursor routing + the J bridge.
#
# Behaviors pinned (CURSOR_TOOL_UX_PASS.md Layer 5, owner-ruled 2026-07-06):
# - Scan and pin-cycle presses ROUTE the cursor wherever the cursor means a
#   place: Look, deploy, and spell targeting IFF Translocation-tagged with
#   zero placement extent (radius 0), plus a per-spell override registry.
#   Disperse (Translocation + radius 3, tuned AoE aim) must NOT route.
# - The routed tile announce is suppressed (the scan line is the utterance);
#   try_examine_tile still runs so examine_target lands on the result.
# - Cycle-ref freeze: a live cycle keeps its start reference, so routing +
#   attention-relativity can't cycle the nearest result forever.
# - The "From destination" qualifier consults the SAME predicate as routing
#   (they can never disagree about what the cursor means).
# - J: jump to the last spoken result; enters Look from normal play; "gone"
#   with NO move for a dead/collected target; Shift+J one-slot bounce.

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

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


# ---- Shared stubs ----

_TRANSLOCATION = object()   # sentinel standing in for Level.Tags.Translocation


class _Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def _level_stub():
    return types.SimpleNamespace(Tags=types.SimpleNamespace(Translocation=_TRANSLOCATION),
                                 Point=_Point)


class FakeSpell:
    def __init__(self, tags=(), radius=None, name='FakeSpell', stat_radius=None,
                 range=5, self_target=False):
        self.tags = list(tags)
        if radius is not None:
            self.radius = radius
        self._stat_radius = stat_radius if stat_radius is not None else (radius or 0)
        self.name = name
        self.range = range
        self.self_target = self_target

    def get_stat(self, attr):
        assert attr == 'radius'
        return self._stat_radius


class LookSpell(FakeSpell):   # name-matched: predicate checks type(...).__name__
    pass


class _SpeechLog:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


# ---- The routing predicate ----

_pred_ns = {'Level': _level_stub(), 'log': lambda *a: None}
exec(_extract("    _ROUTING_OVERRIDES = {", "    def _route_cursor_to"), _pred_ns)
_routes = _pred_ns['_routes_targeting']


def test_pure_teleport_routes_area_aim_does_not():
    blink = FakeSpell(tags=[_TRANSLOCATION])                       # radius never declared
    disperse = FakeSpell(tags=[_TRANSLOCATION], radius=3)
    plain = FakeSpell(tags=[])
    assert _routes(blink)
    assert not _routes(disperse)
    assert not _routes(plain)
    assert not _routes(None)


def test_extent_is_the_attribute_not_the_contaminated_stat():
    # Skeptic-pass finding (2026-07-06): equipment radius bonuses (Geometer's
    # Staff, global radius +2) inflate get_stat('radius') for spells with NO
    # extent — the game still draws no AoE ring (its own gate is hasattr,
    # Level.py:724). A geared-up Blink must keep routing.
    geared_blink = FakeSpell(tags=[_TRANSLOCATION], stat_radius=2)  # no radius attr
    assert _routes(geared_blink)
    # An upgrade PURCHASE setattrs the radius attribute (Level.py:1622):
    # extent now exists, routing stops.
    upgraded = FakeSpell(tags=[_TRANSLOCATION], radius=2)
    assert not _routes(upgraded)


def test_no_destination_cursor_means_no_routing():
    # Range-0 / self-target spells (Lightning Form, Word spells, tag-grafted
    # rituals) never present a destination to route.
    assert not _routes(FakeSpell(tags=[_TRANSLOCATION], range=0))
    assert not _routes(FakeSpell(tags=[_TRANSLOCATION], self_target=True))


def test_shipped_registry_excludes_the_known_convention_breakers():
    for cls_name in ('BlackHoleSpell', 'SummonLeghead', 'SummonWolfSpell',
                     'HeavenlyIdol'):
        breaker = type(cls_name, (FakeSpell,), {})(tags=[_TRANSLOCATION])
        assert not _routes(breaker), cls_name


def test_registry_overrides_beat_the_rule_both_ways():
    _pred_ns['_ROUTING_OVERRIDES']['FakeSpell'] = False
    try:
        blink_like = FakeSpell(tags=[_TRANSLOCATION])
        assert not _routes(blink_like)                             # off-override wins
    finally:
        del _pred_ns['_ROUTING_OVERRIDES']['FakeSpell']
    _pred_ns['_ROUTING_OVERRIDES']['FakeSpell'] = True
    try:
        untagged = FakeSpell(tags=[])
        assert _routes(untagged)                                   # on-override wins
    finally:
        del _pred_ns['_ROUTING_OVERRIDES']['FakeSpell']


# ---- _route_cursor_to ----

def _route_ns(deploy_scan_routing=True):
    ns = {
        'Level': _level_stub(),
        'log': lambda *a: None,
        'cfg': types.SimpleNamespace(deploy_scan_routing=deploy_scan_routing),
        '_last_examine_xy': [('sentinel', 'sentinel')],
        '_deploy_tile_suppress': [False],
        '_route_tile_suppress': [False],
        '_routes_targeting': _routes,
    }
    exec(_extract("    def _route_cursor_to(view, tx, ty):",
                  "    def _get_scan_reference(view):"), ns)
    return ns


class FakeLevelGrid:
    def __init__(self, w=18, h=18):
        self.w, self.h = w, h

    def is_point_in_bounds(self, p):
        return 0 <= p.x < self.w and 0 <= p.y < self.h


class FakeView:
    def __init__(self, deploying=False, cur_spell=None):
        self.game = types.SimpleNamespace(
            deploying=deploying,
            cur_level=FakeLevelGrid(),
            next_level=FakeLevelGrid(),
            p1=types.SimpleNamespace(x=4, y=4))
        self.cur_spell = cur_spell
        self.cur_spell_target = _Point(4, 4) if cur_spell else None
        self.deploy_target = _Point(9, 9) if deploying else None
        self.examined = []
        self.chosen = []

    def can_execute_inputs(self):
        return True

    def try_examine_tile(self, point):
        self.examined.append((point.x, point.y))

    def choose_spell(self, spell):
        self.chosen.append(spell)
        prev = self.cur_spell
        self.cur_spell = spell
        if not prev:
            self.cur_spell_target = _Point(self.game.p1.x, self.game.p1.y)


def test_look_mode_routes_and_suppresses():
    ns = _route_ns()
    view = FakeView(cur_spell=LookSpell(name='look'))
    assert ns['_route_cursor_to'](view, 7, 3) is True
    assert (view.cur_spell_target.x, view.cur_spell_target.y) == (7, 3)
    assert ns['_route_tile_suppress'][0] is True
    assert ns['_last_examine_xy'][0] is None          # dedup reset: examine must run
    assert view.examined == [(7, 3)]


def test_deploy_routes_via_deploy_flag():
    ns = _route_ns()
    view = FakeView(deploying=True)
    assert ns['_route_cursor_to'](view, 2, 15) is True
    assert (view.deploy_target.x, view.deploy_target.y) == (2, 15)
    assert ns['_deploy_tile_suppress'][0] is True
    assert ns['_route_tile_suppress'][0] is False
    assert view.examined == [(2, 15)]


def test_deploy_scan_routing_off_parks_the_cursor():
    # deploy_scan_routing=false (owner ruling 2026-07-09): in deploy the
    # cursor is the only referent — scans speak, the cursor stays parked,
    # J alone moves it. Deploy-only: level-side Look still routes.
    ns = _route_ns(deploy_scan_routing=False)
    view = FakeView(deploying=True)
    assert ns['_route_cursor_to'](view, 2, 15) is False
    assert (view.deploy_target.x, view.deploy_target.y) == (9, 9)
    assert ns['_deploy_tile_suppress'][0] is False    # no armed flag left behind
    assert view.examined == []                        # examine stays parked too
    look_view = FakeView(cur_spell=LookSpell(name='look'))
    assert ns['_route_cursor_to'](look_view, 7, 3) is True
    assert (look_view.cur_spell_target.x, look_view.cur_spell_target.y) == (7, 3)


def test_eligible_teleport_routes_tuned_aim_does_not():
    ns = _route_ns()
    blink_view = FakeView(cur_spell=FakeSpell(tags=[_TRANSLOCATION]))
    assert ns['_route_cursor_to'](blink_view, 6, 6) is True
    assert (blink_view.cur_spell_target.x, blink_view.cur_spell_target.y) == (6, 6)
    assert ns['_route_tile_suppress'][0] is True
    ns['_route_tile_suppress'][0] = False      # consumed by the announce in real flow

    disperse_view = FakeView(cur_spell=FakeSpell(tags=[_TRANSLOCATION], radius=3))
    assert ns['_route_cursor_to'](disperse_view, 6, 6) is False
    assert (disperse_view.cur_spell_target.x, disperse_view.cur_spell_target.y) == (4, 4)
    assert disperse_view.examined == []
    assert ns['_route_tile_suppress'][0] is False     # no armed flag left behind


def test_normal_play_and_out_of_bounds_do_not_route():
    ns = _route_ns()
    assert ns['_route_cursor_to'](FakeView(), 5, 5) is False
    look_view = FakeView(cur_spell=LookSpell(name='look'))
    assert ns['_route_cursor_to'](look_view, 99, 99) is False
    assert ns['_route_tile_suppress'][0] is False


# ---- Qualifier agreement ----

def _scanref_ns():
    ns = {
        'Level': _level_stub(),
        '_routes_targeting': _routes,
    }
    exec(_extract("    def _get_scan_reference(view):",
                  "    def _query_enemies(view"), ns)
    return ns


def test_destination_qualifier_follows_the_routing_predicate():
    ns = _scanref_ns()
    getref = ns['_get_scan_reference']
    blink_view = FakeView(cur_spell=FakeSpell(tags=[_TRANSLOCATION]))
    _ref, _lvl, qual = getref(blink_view)
    assert qual == "destination"
    disperse_view = FakeView(cur_spell=FakeSpell(tags=[_TRANSLOCATION], radius=3))
    _ref, _lvl, qual = getref(disperse_view)
    assert qual == "aim"    # vocabulary ruling 2026-07-14: "target" = entities only
    look_view = FakeView(cur_spell=LookSpell(name='look'))
    _ref, _lvl, qual = getref(look_view)
    assert qual == "cursor"


def test_qualifier_and_routing_consult_one_predicate():
    # The source must contain no independent bare-tag test: Translocation
    # membership is tested exactly once, inside _routes_targeting.
    assert _src.count("Tags.Translocation") == 1
    assert '"destination" if' not in _src  # qualifier branch is the predicate call
    assert _src.count("_routes_targeting(spell)") >= 2  # routing + qualifier


# ---- Cycle-ref freeze ----

_freeze_ns = {}
exec(_extract("    class CycleScanner:", "    _enemy_scanner = CycleScanner"), _freeze_ns)
exec(_extract("    def _cycle_ref(scanner, ref_point):",
              "    # ---- Unified pin system"), _freeze_ns)


def test_live_cycle_keeps_start_reference():
    scanner = _freeze_ns['CycleScanner']("test")
    start = _Point(4, 4)
    moved = _Point(9, 9)   # routing parked the cursor on result #1
    assert _freeze_ns['_cycle_ref'](scanner, start) is start      # empty: fresh ref
    scanner.set_list([('enemy1', 1.0), ('enemy2', 2.0)], start)
    scanner.advance()
    assert _freeze_ns['_cycle_ref'](scanner, moved) is start      # frozen
    # And the frozen ref defeats the rebuild check: continuation advances.
    assert not scanner.needs_rebuild(start)
    idx, _total, _sc = scanner.advance()
    assert scanner.items[idx][0] == 'enemy2'                      # not enemy1 again
    scanner.reset()
    assert _freeze_ns['_cycle_ref'](scanner, moved) is moved      # reset: re-derive


def test_every_cycling_query_freezes_before_rebuild():
    for scanner in ('_enemy_scanner', '_ally_scanner', '_spawner_scanner',
                    '_landmark_scanner', '_pin_scanner'):
        freeze = f"_cycle_ref({scanner}, ref"
        rebuild = f"{scanner}.needs_rebuild(ref"
        assert freeze in _src, scanner
        assert _src.index(freeze) < _src.index(rebuild), scanner


# ---- The J bridge ----

def _jump_ns():
    speech = _SpeechLog()
    ns = {
        'Level': _level_stub(),
        'log': lambda *a: None,
        'async_tts': speech,
        '_name': lambda obj, fallback="something": getattr(obj, 'name', fallback) or fallback,
        '_last_scanned_target': [None],
        '_last_examine_xy': [None],
        '_main': types.SimpleNamespace(LookSpell=LookSpell),
    }
    exec(_extract("    _jump_back_pos = [None]",
                  "    # ---- Pin pathfinding ----"), ns)
    return ns, speech


class FakeJLevel(FakeLevelGrid):
    def __init__(self):
        super().__init__()
        self.units = []
        self.tiles = [[types.SimpleNamespace(prop=None) for _y in range(18)]
                      for _x in range(18)]


def _jview(**kw):
    view = FakeView(**kw)
    view.game.cur_level = FakeJLevel()
    view.game.next_level = FakeJLevel()
    return view


def test_j_with_nothing_scanned_speaks_and_stays():
    ns, speech = _jump_ns()
    view = _jview()
    ns['_jump_to_last_spoken'](view)
    assert speech.spoken == ["Nothing scanned"]
    assert view.chosen == [] and view.examined == []


def test_j_never_asserts_stale_truth():
    ns, speech = _jump_ns()
    view = _jview(cur_spell=LookSpell(name='look'))
    dead = types.SimpleNamespace(name="Imp", x=3, y=3)
    ns['_last_scanned_target'][0] = dead      # not in level.units -> gone
    ns['_jump_to_last_spoken'](view)
    assert speech.spoken == ["Imp gone"]
    assert (view.cur_spell_target.x, view.cur_spell_target.y) == (4, 4)
    assert ns['_jump_back_pos'][0] is None    # a refused jump stores no bounce
    # Collected landmark: same ruling.
    ns['_last_scanned_target'][0] = ("Memory Orb", 5, 5)
    ns['_jump_to_last_spoken'](view)
    assert speech.spoken[-1] == "Memory Orb gone"


def test_j_from_normal_play_enters_look_at_the_result():
    ns, _speech = _jump_ns()
    view = _jview()
    unit = types.SimpleNamespace(name="Imp", x=7, y=2)
    view.game.cur_level.units.append(unit)
    ns['_last_scanned_target'][0] = unit
    ns['_jump_to_last_spoken'](view)
    assert len(view.chosen) == 1 and type(view.chosen[0]).__name__ == 'LookSpell'
    assert (view.cur_spell_target.x, view.cur_spell_target.y) == (7, 2)
    assert view.examined == [(7, 2)]
    # Bounce target = where the Look cursor opened (the player tile).
    assert (ns['_jump_back_pos'][0].x, ns['_jump_back_pos'][0].y) == (4, 4)


def test_j_follows_a_moved_unit():
    ns, _speech = _jump_ns()
    view = _jview(cur_spell=LookSpell(name='look'))
    unit = types.SimpleNamespace(name="Imp", x=7, y=2)
    view.game.cur_level.units.append(unit)
    ns['_last_scanned_target'][0] = unit
    unit.x, unit.y = 1, 1                      # moved since it spoke
    ns['_jump_to_last_spoken'](view)
    assert (view.cur_spell_target.x, view.cur_spell_target.y) == (1, 1)


def test_j_in_aim_targeting_moves_the_aim_deliberately():
    ns, _speech = _jump_ns()
    view = _jview(cur_spell=FakeSpell(tags=[_TRANSLOCATION], radius=3))  # even Disperse
    unit = types.SimpleNamespace(name="Imp", x=6, y=6)
    view.game.cur_level.units.append(unit)
    ns['_last_scanned_target'][0] = unit
    ns['_jump_to_last_spoken'](view)
    assert view.chosen == []                   # no Look entered
    assert (view.cur_spell_target.x, view.cur_spell_target.y) == (6, 6)


def test_j_during_deploy_moves_the_deploy_cursor():
    ns, _speech = _jump_ns()
    view = _jview(deploying=True)
    view.game.next_level.tiles[3][12].prop = types.SimpleNamespace(name="Shop")
    ns['_last_scanned_target'][0] = ("Shop", 3, 12)
    ns['_jump_to_last_spoken'](view)
    assert (view.deploy_target.x, view.deploy_target.y) == (3, 12)
    assert (ns['_jump_back_pos'][0].x, ns['_jump_back_pos'][0].y) == (9, 9)


def test_shift_j_bounces_and_bounces_back():
    ns, speech = _jump_ns()
    view = _jview(cur_spell=LookSpell(name='look'))
    view.cur_spell_target = _Point(8, 8)
    ns['_jump_back_pos'][0] = _Point(2, 2)
    ns['_jump_back'](view)
    assert (view.cur_spell_target.x, view.cur_spell_target.y) == (2, 2)
    assert (ns['_jump_back_pos'][0].x, ns['_jump_back_pos'][0].y) == (8, 8)
    ns['_jump_back'](view)
    assert (view.cur_spell_target.x, view.cur_spell_target.y) == (8, 8)


def test_shift_j_refuses_gracefully():
    ns, speech = _jump_ns()
    ns['_jump_back'](_jview())                 # nothing stored
    assert speech.spoken == ["Nowhere to jump back"]
    ns['_jump_back_pos'][0] = _Point(2, 2)
    ns['_jump_back'](_jview())                 # stored, but no cursor context
    assert speech.spoken[-1] == "Nowhere to jump back"


# ---- Wiring pins (source shape) ----

def test_j_dispatch_present():
    assert "elif evt.key == pygame.K_j:" in _src
    j_branch = _src[_src.index("elif evt.key == pygame.K_j:"):]
    j_branch = j_branch[:j_branch.index("\n                elif ")]
    assert "_jump_back(self)" in j_branch
    assert "_jump_to_last_spoken(self)" in j_branch


def test_all_five_cycling_queries_route():
    assert _src.count("_route_cursor_to(view, unit.x, unit.y)") == 3
    assert "_route_cursor_to(view, tx, ty)" in _src
    assert "_route_cursor_to(view, px, py)" in _src


def test_look_and_target_announces_consume_the_suppress_flag():
    for fn in ("def _announce_look_tile(view, point):",
               "def _announce_target_tile(view, point):"):
        body = _src[_src.index(fn):]
        head = body[:body.index("        try:")]
        assert "_route_tile_suppress[0]" in head, fn
