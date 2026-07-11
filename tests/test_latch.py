# Tests for slice 7 of the cursor-tool pass: overlay latches (Alt+L / Alt+T).
#
# Behaviors pinned (CURSOR_TOOL_UX_PASS.md §5.1 + the 2026-07-07 full gate,
# two adversarial skeptics; findings cited as render #N / lifetime #N):
# - Origin pins to the ATTENTION OBJECT: normal play follows the wizard,
#   Look/targeting/deploy freeze that tile. One latch at a time.
# - Lifetime is an ACTIVE state-clearing service (lifetime #1), checked
#   level-identity-first (lifetime #3), with liveness as list membership,
#   never the poisoned removal flag (lifetime #2).
# - The draw wrap suppresses the chain's targeting render and reasserts it
#   above the latch tint exactly once (render #2); held overlay keys preempt
#   via the drew-this-frame flag (render #4); gameover and screenshot frames
#   never receive latch pixels (render #3/#6).
# - _draw_threat_pinned swaps the THREE private examine fields directly and
#   restores them exactly, even on exception (render #1).
# - Tokens answer from the origin's current position; a flipped (charmed)
#   narrow_unit gets in-reach wording, not threat wording (lifetime #4).

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

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


class _Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class FakeUnit:
    def __init__(self, name, x=0, y=0, team=1, cur_hp=10, player=False):
        self.name = name
        self.x = x
        self.y = y
        self.team = team
        self.cur_hp = cur_hp
        self.is_player_controlled = player


class FakeLevel:
    def __init__(self, w=18, h=18, see=True):
        self.width = w
        self.height = h
        self.units = []
        self.see_log = []
        self._see = see

    def can_see(self, ox, oy, x, y):
        self.see_log.append((ox, oy, x, y))
        return self._see if not callable(self._see) else self._see(ox, oy, x, y)

    def can_stand(self, x, y, unit):
        return True


class _Speech:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


def _make_ns():
    """Exec the latch state+logic span with spies."""
    speech = _Speech()
    ns = {
        'weakref': weakref,
        'types': types,
        '_main': types.SimpleNamespace(SPRITE_SIZE=32),
        'Level': types.SimpleNamespace(
            Point=_Point, Unit=FakeUnit,
            are_hostile=lambda a, b: getattr(a, 'team', 0) != getattr(b, 'team', 0)),
        'async_tts': speech,
        'log': lambda *a: None,
        '_name': lambda o, fb="something": getattr(o, 'name', fb) or fb,
        '_is_player': lambda u: getattr(u, 'is_player_controlled', False),
        '_unit_threatens_point': lambda u, x, y: getattr(u, 'reaches', False),
        '_threat_membership': lambda v, l, p, x, y: getattr(l, 'zone_answer', False),
        'cfg': types.SimpleNamespace(latch_visual_overlay=True),
    }
    exec(_extract("    _SPRITE_SIZE = getattr(_main, 'SPRITE_SIZE', 32)",
                  "    def _draw_los_from(view"), ns)
    return ns, speech


class LatchView:
    def __init__(self, cur_level, next_level=None, deploying=False,
                 cur_spell=None, examine=None):
        self.game = types.SimpleNamespace(
            p1=FakeUnit("Wizard", 5, 5, team=0, player=True),
            cur_level=cur_level, next_level=next_level, deploying=deploying,
            is_awaiting_input=lambda: True)
        self.cur_spell = cur_spell
        self.cur_spell_target = _Point(3, 4) if cur_spell else None
        self.deploy_target = _Point(9, 9) if deploying else None
        self.examine_target = examine
        cur_level.units.append(self.game.p1)

    def get_display_level(self):
        return self.game.next_level or self.game.cur_level


# ---- Toggle: origin selection, exclusivity ----

def test_normal_play_latch_follows_the_wizard():
    ns, speech = _make_ns()
    view = LatchView(FakeLevel())
    ns['_toggle_latch'](view, 'los')
    latch = ns['_latch'][0]
    assert latch['origin_unit'] is view.game.p1
    assert latch['origin_point'] is None
    assert speech.spoken == ["Latched: line of sight, following you"]
    view.game.p1.x, view.game.p1.y = 7, 8
    assert ns['_latch_origin_xy'](latch) == (7, 8)      # FOLLOWS


def test_cursor_context_latch_freezes_the_tile():
    ns, speech = _make_ns()
    view = LatchView(FakeLevel(), cur_spell=types.SimpleNamespace(name='look'))
    ns['_toggle_latch'](view, 'los')
    latch = ns['_latch'][0]
    assert latch['origin_unit'] is None
    assert (latch['origin_point'].x, latch['origin_point'].y) == (3, 4)
    view.cur_spell_target = _Point(9, 9)                # cursor moves on
    assert ns['_latch_origin_xy'](latch) == (3, 4)      # FROZEN
    assert speech.spoken == ["Latched: line of sight from this tile"]


def test_deploy_latch_pins_next_level():
    ns, _speech = _make_ns()
    nxt = FakeLevel()
    view = LatchView(FakeLevel(), next_level=nxt, deploying=True)
    ns['_toggle_latch'](view, 'los')
    latch = ns['_latch'][0]
    assert latch['level']() is nxt
    assert (latch['origin_point'].x, latch['origin_point'].y) == (9, 9)


def test_threat_latch_pins_examined_hostile_else_global():
    ns, speech = _make_ns()
    level = FakeLevel()
    hag = FakeUnit("Night Hag", 2, 2)
    level.units.append(hag)
    view = LatchView(level, examine=hag)
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch'][0]['narrow_unit'] is hag
    assert speech.spoken == ["Latched: Night Hag's threat"]
    ns['_toggle_latch'](view, 'threat')                 # same chord unlatches
    assert ns['_latch'][0] is None
    assert speech.spoken[-1] == "Unlatched"
    view.examine_target = None
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch'][0]['narrow_unit'] is None
    assert speech.spoken[-1] == "Latched: threat"


def test_threat_latch_pins_examined_ally_and_says_reach():
    # Ally-reach coverage fix (BUG_QUEUE 2026-07-09): the old hostility
    # gate silently latched the GLOBAL zone with an ally examined. Allies
    # latch like anyone else, announced as reach — never threat (owner
    # ruling 2026-07-10).
    ns, speech = _make_ns()
    level = FakeLevel()
    blade = FakeUnit("Dancing Blade", 2, 2, team=0)     # wizard's team
    level.units.append(blade)
    view = LatchView(level, examine=blade)
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch'][0]['narrow_unit'] is blade
    assert speech.spoken == ["Latched: Dancing Blade's reach"]


def test_latches_are_exclusive():
    ns, _speech = _make_ns()
    view = LatchView(FakeLevel())
    ns['_toggle_latch'](view, 'los')
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch'][0]['overlay'] == 'threat'       # replaced, not stacked


# ---- Lifetime: order, membership, active service ----

def test_realm_transition_expires_as_level_change_not_origin_death():
    # lifetime #3: try_deploy removes p1 from the old level BEFORE the swap;
    # the level-identity check must win or every realm reads as a death.
    ns, _speech = _make_ns()
    old = FakeLevel()
    view = LatchView(old)
    ns['_toggle_latch'](view, 'los')
    old.units.remove(view.game.p1)                      # Game.py:819
    view.game.cur_level = FakeLevel()                   # :827, new display level
    assert ns['_latch_expiry_reason'](view, ns['_latch'][0]) == 'level'


def test_deploy_carry_same_object_survives():
    ns, _speech = _make_ns()
    nxt = FakeLevel()
    view = LatchView(FakeLevel(), next_level=nxt, deploying=True)
    ns['_toggle_latch'](view, 'los')
    # next_level becomes cur_level, SAME object (Game.py:827)
    view.game.cur_level = nxt
    view.game.next_level = None
    view.game.deploying = False
    assert ns['_latch_expiry_reason'](view, ns['_latch'][0]) is None


def test_dead_origin_expires_and_service_announces_once():
    ns, speech = _make_ns()
    level = FakeLevel()
    hag = FakeUnit("Night Hag", 2, 2)
    level.units.append(hag)
    view = LatchView(level, examine=hag)
    ns['_toggle_latch'](view, 'threat')
    level.units.remove(hag)
    assert ns['_latch_service'](view) is None
    assert speech.spoken[-1] == "Latch released, Night Hag gone"
    count = len(speech.spoken)
    assert ns['_latch_service'](view) is None           # already cleared:
    assert len(speech.spoken) == count                  # no second announce


def test_level_expiry_is_active_state_clearing():
    # lifetime #1: the abort->re-enter portal cache would resurrect a
    # stateless latch; the service must CLEAR at the first mismatch.
    ns, speech = _make_ns()
    deploy_level = FakeLevel()
    view = LatchView(FakeLevel(), next_level=deploy_level, deploying=True)
    ns['_toggle_latch'](view, 'los')
    view.game.next_level = None                         # deploy aborted
    view.game.deploying = False
    assert ns['_latch_service'](view) is None
    assert speech.spoken[-1] == "Latch released"
    view.game.next_level = deploy_level                 # re-entered: same object
    view.game.deploying = True
    assert ns['_latch'][0] is None                      # stays dead


def test_liveness_is_membership_never_the_removal_flag():
    latch_section = _extract("    # ---- Overlay latches (cursor-tool pass, slice 7) ----",
                             "    def patched_try_examine_tile")
    assert "unit not in level.units" in latch_section
    assert ".removed" not in latch_section              # lifetime #2 trap


# ---- Tokens ----

def test_los_token_answers_from_the_following_origin():
    ns, _speech = _make_ns()
    level = FakeLevel(see=lambda ox, oy, x, y: (ox, oy) == (5, 5))
    view = LatchView(level)
    ns['_toggle_latch'](view, 'los')
    assert ns['_latch_token'](view, level, 8, 8) == ", in sight"
    view.game.p1.x, view.game.p1.y = 1, 1               # wizard moved
    assert ns['_latch_token'](view, level, 8, 8) == ", out of sight"


def test_threat_token_wording_follows_live_hostility():
    # lifetime #4: a charmed narrow_unit's reach is not a "threat".
    ns, _speech = _make_ns()
    level = FakeLevel()
    hag = FakeUnit("Night Hag", 2, 2)
    hag.reaches = True
    level.units.append(hag)
    view = LatchView(level, examine=hag)
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch_token'](view, level, 4, 4) == ", threatened"
    # The pinned unit's own tile is in the narrowed zone (RiftWizard3.py:
    # 5662) — position counts even with no reach (field find 2026-07-07).
    hag.reaches = False
    assert ns['_latch_token'](view, level, 2, 2) == ", threatened"
    hag.reaches = True
    hag.team = 0                                        # charmed
    assert ns['_latch_token'](view, level, 4, 4) == ", in reach"
    hag.reaches = False
    assert ns['_latch_token'](view, level, 4, 4) == ", out of reach"


def test_ally_latch_tokens_and_berserk_flip():
    # An ally latched directly rides the same live-hostility pair as a
    # charmed hostile (ally-reach coverage fix) — and a berserk flip
    # (are_hostile goes True, Level.py:138) swings it back to threat
    # wording with no re-latch.
    ns, _speech = _make_ns()
    level = FakeLevel()
    blade = FakeUnit("Dancing Blade", 2, 2, team=0)
    blade.reaches = True
    level.units.append(blade)
    view = LatchView(level, examine=blade)
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch_token'](view, level, 4, 4) == ", in reach"
    blade.reaches = False
    assert ns['_latch_token'](view, level, 4, 4) == ", out of reach"
    # Own-tile membership counts for allies too (RiftWizard3.py:5662).
    assert ns['_latch_token'](view, level, 2, 2) == ", in reach"
    blade.reaches = True
    blade.team = 7                                      # berserk-flipped
    assert ns['_latch_token'](view, level, 4, 4) == ", threatened"


def test_global_threat_token_rides_the_shared_membership():
    ns, _speech = _make_ns()
    level = FakeLevel()
    level.zone_answer = True
    view = LatchView(level)
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch_token'](view, level, 4, 4) == ", threatened"
    level.zone_answer = False
    assert ns['_latch_token'](view, level, 4, 4) == ", safe"


def test_token_is_silent_off_level_and_when_unlatched():
    ns, _speech = _make_ns()
    level = FakeLevel()
    view = LatchView(level)
    assert ns['_latch_token'](view, level, 1, 1) == ""  # no latch
    ns['_toggle_latch'](view, 'los')
    assert ns['_latch_token'](view, FakeLevel(), 1, 1) == ""  # other level


def test_vitals_line_forms():
    ns, _speech = _make_ns()
    level = FakeLevel()
    view = LatchView(level)
    assert ns['_latch_vitals_line'](view) == ""
    ns['_toggle_latch'](view, 'los')
    assert ns['_latch_vitals_line'](view) == "Line of sight latched, following you"
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch_vitals_line'](view) == "Threat latched"


def test_vitals_line_narrow_wording_is_live_hostility():
    # F reports reach for an ally's pin, threat for a hostile's — and the
    # words follow a mid-latch team flip, same as the token.
    ns, _speech = _make_ns()
    level = FakeLevel()
    blade = FakeUnit("Dancing Blade", 2, 2, team=0)
    level.units.append(blade)
    view = LatchView(level, examine=blade)
    ns['_toggle_latch'](view, 'threat')
    assert ns['_latch_vitals_line'](view) == "Dancing Blade's reach latched"
    blade.team = 7                                      # berserk-flipped
    assert ns['_latch_vitals_line'](view) == "Dancing Blade's threat latched"


# ---- Draw wrap: suppress/reassert, preempt, gates ----

def _draw_ns():
    ns, speech = _make_ns()

    class StubPGV:
        def draw_level(self):
            self.calls.append('orig_level')
            if getattr(self, 'held_overlay', False):
                self.draw_los()                          # the chain's held branch
            elif getattr(self, 'cur_spell', None) is not None:
                self.draw_targeting()                    # the chain's elif tail
        def draw_targeting(self):
            self.calls.append('orig_targeting')
        def draw_los(self):
            self.calls.append('orig_los')
        def draw_threat(self):
            self.calls.append('orig_threat')
        def highlight_units(self, allied=True):
            self.calls.append('orig_hl_units')
        def highlight_objects(self):
            self.calls.append('orig_hl_objects')

    ns['_PyGameView'] = StubPGV
    exec(_extract("    def _draw_los_from(view",
                  "    _PyGameView.draw_level = patched_draw_level"), ns)
    return ns, speech, StubPGV


class DrawView(LatchView):
    def __init__(self, ns, *a, **k):
        super().__init__(*a, **k)
        self._ns = ns
        self.calls = []
        self.gameover_frames = 0
        self.held_overlay = False
        self.tile_visible_image = object()
        self.level_display = types.SimpleNamespace(
            blits=lambda seq: self.calls.append(('blits', len(list(seq)))),
            blit=lambda *a: self.calls.append('blit'))
        self._examine_target = None
        self._examine_index = 3
        self._examine_extras = ['extra']

    # Route the "class methods" through the patched closures, as the real
    # installation does.
    def draw_targeting(self):
        self._ns['patched_draw_targeting'](self)

    def draw_los(self):
        self._ns['patched_draw_los'](self)

    def draw_threat(self):
        self.calls.append(('draw_threat_seen_examine', self._examine_target,
                           self._examine_index, list(self._examine_extras)))


def test_targeting_suppressed_then_reasserted_exactly_once():
    ns, _speech, _pgv = _draw_ns()
    level = FakeLevel(see=False)
    view = DrawView(ns, level, cur_spell=types.SimpleNamespace(name='look'))
    ns['_toggle_latch'](view, 'los')
    ns['patched_draw_level'](view)
    # The chain's targeting call was muted; the reassert drew it once, above
    # the latch (render #2).
    assert view.calls.count('orig_targeting') == 1
    assert view.calls.index('orig_level') < view.calls.index('orig_targeting')
    assert ns['_latch_suppress_targeting'][0] is False  # flag never leaks


def test_held_overlay_key_preempts_the_latch_draw():
    ns, _speech, _pgv = _draw_ns()
    view = DrawView(ns, FakeLevel(see=False))
    ns['_toggle_latch'](view, 'threat')      # threat latched, L held: different overlays
    view.held_overlay = True
    before = len(view.calls)
    ns['patched_draw_level'](view)
    # The held branch drew (through the flag-setting patch); the latch's own
    # threat draw did NOT stack on top (render #4).
    tail = view.calls[before:]
    assert 'orig_los' in tail                # the game's own held draw ran
    assert not any(isinstance(c, tuple) and c[0] == 'draw_threat_seen_examine'
                   for c in tail)


def test_held_los_while_los_latched_draws_the_pinned_origin_once():
    # Ruled origin unification (§5.1 / render #9): a held L routes through
    # the patched draw_los and answers from the PINNED origin; the latch
    # does not draw a second overlay that frame.
    ns, _speech, _pgv = _draw_ns()
    level = FakeLevel(see=False)
    view = DrawView(ns, level)
    ns['_toggle_latch'](view, 'los')
    view.game.p1.x, view.game.p1.y = 7, 8
    view.held_overlay = True
    before = len(view.calls)
    ns['patched_draw_level'](view)
    tail = view.calls[before:]
    assert 'orig_los' not in tail            # original origin chain bypassed
    blits = [c for c in tail if isinstance(c, tuple) and c[0] == 'blits']
    assert len(blits) == 1                   # one overlay draw, not two
    assert all(s[:2] == (7, 8) for s in level.see_log)


def test_gameover_and_screenshot_frames_get_no_latch_pixels():
    ns, _speech, _pgv = _draw_ns()
    view = DrawView(ns, FakeLevel(see=False))
    ns['_toggle_latch'](view, 'los')
    view.gameover_frames = 3                             # render #3
    ns['patched_draw_level'](view)
    assert not any(isinstance(c, tuple) and c[0] == 'blits' for c in view.calls)
    view.gameover_frames = 0
    ns['_latch_in_screenshot'][0] = True                 # render #6
    ns['patched_draw_level'](view)
    assert not any(isinstance(c, tuple) and c[0] == 'blits' for c in view.calls)
    ns['_latch_in_screenshot'][0] = False


def test_latched_los_draw_uses_the_pinned_origin():
    ns, _speech, _pgv = _draw_ns()
    level = FakeLevel(see=False)
    view = DrawView(ns, level)
    ns['_toggle_latch'](view, 'los')
    view.game.p1.x, view.game.p1.y = 7, 8
    ns['patched_draw_level'](view)
    assert level.see_log and all(s[:2] == (7, 8) for s in level.see_log)
    assert any(isinstance(c, tuple) and c[0] == 'blits' for c in view.calls)


def test_unlatched_draw_los_defers_to_the_original():
    ns, _speech, _pgv = _draw_ns()
    view = DrawView(ns, FakeLevel())
    view.draw_los()
    assert 'orig_los' in view.calls
    assert ns['_latch_overlay_drawn'][0] is True


def test_pinned_threat_swap_restores_the_examine_triple():
    ns, _speech, _pgv = _draw_ns()
    level = FakeLevel()
    hag = FakeUnit("Night Hag", 2, 2)
    level.units.append(hag)
    view = DrawView(ns, level, examine=hag)
    ns['_toggle_latch'](view, 'threat')
    ns['_draw_threat_pinned'](view, ns['_latch'][0])
    seen = [c for c in view.calls if isinstance(c, tuple)
            and c[0] == 'draw_threat_seen_examine'][0]
    assert seen[1] is hag and seen[2] == 0 and seen[3] == []   # pinned variant
    assert (view._examine_target, view._examine_index,
            view._examine_extras) == (None, 3, ['extra'])       # exact restore


def test_pinned_threat_restores_on_exception():
    ns, _speech, _pgv = _draw_ns()
    level = FakeLevel()
    view = DrawView(ns, level)
    ns['_toggle_latch'](view, 'threat')

    def boom():
        raise RuntimeError("draw failed")
    view.draw_threat = boom
    try:
        ns['_draw_threat_pinned'](view, ns['_latch'][0])
    except RuntimeError:
        pass
    assert (view._examine_target, view._examine_index,
            view._examine_extras) == (None, 3, ['extra'])


def test_deploy_frame_reasserts_the_deploy_cursor():
    ns, _speech, _pgv = _draw_ns()
    img = types.SimpleNamespace(get_width=lambda: 64)
    ns['_main'].get_image = lambda parts: img
    ns['_main'].cloud_frame_clock = 0
    nxt = FakeLevel(see=False)
    view = DrawView(ns, FakeLevel(), next_level=nxt, deploying=True)
    ns['_toggle_latch'](view, 'los')
    ns['patched_draw_level'](view)
    assert 'blit' in view.calls                          # render #5: cursor re-blitted
    blits_idx = max(i for i, c in enumerate(view.calls)
                    if isinstance(c, tuple) and c[0] == 'blits')
    assert view.calls.index('blit') > blits_idx          # above the tint


# ---- Wiring pins ----

def test_alt_gates_wired_on_both_mirrored_keys():
    assert "_toggle_latch(self, 'los')" in _src
    assert "_toggle_latch(self, 'threat')" in _src


def test_all_draw_wraps_installed():
    for line in ("_PyGameView.draw_level = patched_draw_level",
                 "_PyGameView.draw_targeting = patched_draw_targeting",
                 "_PyGameView.draw_los = patched_draw_los",
                 "_PyGameView.draw_threat = patched_draw_threat",
                 "_PyGameView.highlight_units = patched_highlight_units",
                 "_PyGameView.highlight_objects = patched_highlight_objects"):
        assert line in _src, line
    assert "'make_level_screenshot', 'make_level_end_screenshot'" in _src


def test_announce_funnels_carry_the_token():
    assert _src.count("_latch_token(view, level,") >= 2   # look + deploy
    assert "_latch_token(view, getattr(view.game, 'cur_level', None)" in _src
    assert "_latch_vitals_line(view)" in _src