# Tests for slice 6 of the cursor-tool pass: the threat-query redesign.
#
# Behaviors pinned (CURSOR_TOOL_UX_PASS.md §5.2, owner-ruled 2026-07-06):
# - Global T answers "Threatened"/"Safe" by membership in the game's own
#   threat_zone, built by INVOKING draw_threat (zero mirrored logic, shared
#   cache, staleness check is the game's own).
# - The zone variant matters: draw_threat narrows to ANY examined unit,
#   allies included — with an ally examined the mod must NOT read the
#   narrowed zone for the am-I-threatened question (one-point fallback).
# - Per-unit attribution: examine a hostile + T = "Threatens you" /
#   "Can't hit you"; examine an ally + T = "You're in its reach" / "Out of
#   its reach" (ally-reach coverage fix — reach vocabulary, never threat;
#   hostility is live, so a berserk ally answers as threat).
# - The legacy enumeration is restored VERBATIM behind
#   threat_enumeration_legacy (default off) — a time capsule.
# - Perf shape: with a fresh zone, no per-unit can_threaten work happens.

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

from helpers import _direction_offset

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


class FakeUnit:
    def __init__(self, name, x=0, y=0, team=1, cur_hp=10, player=False):
        self.name = name
        self.x = x
        self.y = y
        self.team = team
        self.cur_hp = cur_hp
        self.is_player_controlled = player


class _Speech:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class FakeView:
    """View stub: draw_threat() assigns the prepared zone and counts calls."""
    def __init__(self, player, units=(), zone=None, examine=None):
        level = types.SimpleNamespace(units=[player, *units])
        self.game = types.SimpleNamespace(p1=player, cur_level=level)
        self.examine_target = examine
        self._zone = zone
        self.threat_zone = None
        self.draw_calls = 0

    def draw_threat(self):
        self.draw_calls += 1
        self.threat_zone = self._zone


def _ns(legacy=False, threatens=()):
    """Exec the legacy + query functions with spies. `threatens` = names of
    units whose one-point test answers True; calls are counted."""
    speech = _Speech()
    calls = []

    def _threatens_spy(unit, x, y):
        calls.append(unit.name)
        return unit.name in threatens

    ns = {
        'Level': types.SimpleNamespace(
            Unit=FakeUnit,
            are_hostile=lambda a, b: getattr(a, 'team', 0) != getattr(b, 'team', 0)),
        'cfg': types.SimpleNamespace(threat_enumeration_legacy=legacy),
        'async_tts': speech,
        'log': lambda *a: None,
        '_is_player': lambda u: getattr(u, 'is_player_controlled', False),
        '_name': lambda o, fb="something": getattr(o, 'name', fb) or fb,
        '_direction_offset': _direction_offset,
        '_unit_threatens_point': _threatens_spy,
    }
    exec(_extract("    def _threat_enumeration_legacy(level",
                  "    def _query_space(view"), ns)
    return ns, speech, calls


def _player():
    return FakeUnit("Wizard", 5, 5, team=0, player=True)


# ---- Global membership via the game's zone ----

def test_zone_membership_speaks_threatened_and_safe():
    p = _player()
    imp = FakeUnit("Imp", 8, 5)
    view = FakeView(p, [imp], zone={(8, 5), (5, 5)})
    ns, speech, calls = _ns()
    ns['_query_threat'](view)
    assert speech.spoken == ["Threatened"]
    assert view.draw_calls == 1
    assert calls == []          # perf pin: zone answered, zero can_threaten work

    safe_view = FakeView(p, [imp], zone={(8, 5)})
    ns['_query_threat'](safe_view)
    assert speech.spoken[-1] == "Safe"


def test_ref_point_and_qualifier_ride_the_membership():
    p = _player()
    view = FakeView(p, [], zone={(2, 2)})
    ns, speech, _calls = _ns()
    ns['_query_threat'](view, ref_point=types.SimpleNamespace(x=2, y=2),
                        qualifier="cursor")
    assert speech.spoken == ["From cursor. Threatened"]


def test_unavailable_zone_falls_back_to_one_point_test():
    p = _player()
    imp = FakeUnit("Imp", 8, 5)
    view = FakeView(p, [imp], zone=None)     # draw_threat yields no zone
    ns, speech, calls = _ns(threatens=("Imp",))
    ns['_query_threat'](view)
    assert speech.spoken == ["Threatened"]
    assert view.draw_calls == 1
    assert calls == ["Imp"]                  # fallback exercised


def test_fallback_early_exits_at_first_threatener():
    p = _player()
    units = [FakeUnit("Imp", 8, 5), FakeUnit("Wolf", 9, 5), FakeUnit("Ogre", 10, 5)]
    view = FakeView(p, units, zone=None)
    ns, speech, calls = _ns(threatens=("Imp",))
    ns['_query_threat'](view)
    assert speech.spoken == ["Threatened"]
    assert calls == ["Imp"]                  # never reached Wolf or Ogre


def test_enemy_occupied_tile_is_threatened_in_the_fallback():
    # The game's zone contains hostile POSITIONS (RiftWizard3.py:5654), and
    # the cursor resting on an enemy always narrows examine onto it, forcing
    # the fallback path — so the fallback must count the occupied tile as
    # threatened or every enemy tile speaks "clear" under red pixels
    # (field find, 2026-07-07).
    p = _player()
    imp = FakeUnit("Imp", 8, 5)
    view = FakeView(p, [imp], zone=None, examine=imp)
    ns, speech, calls = _ns()          # imp threatens nothing by reach
    ns['_query_threat'](view, ref_point=types.SimpleNamespace(x=8, y=5))
    # (hostile examined -> per-unit branch would answer; go through the
    # membership helper directly, as the latch token does)
    assert ns['_threat_membership'](view, view.game.cur_level, p, 8, 5) is True
    assert ns['_threat_membership'](view, view.game.cur_level, p, 2, 2) is False


# ---- Zone-variant guard: examined units narrow the game's zone ----

def test_ally_examined_never_reads_the_narrowed_zone():
    # The GLOBAL question (the latch token's, since examine+T now answers
    # per-unit for allies too) must not read a zone narrowed to the ally —
    # go through the membership helper directly, as the token does.
    p = _player()
    blade = FakeUnit("Dancing Blade", 6, 5, team=0)   # friendly, not the wizard
    imp = FakeUnit("Imp", 8, 5)
    view = FakeView(p, [blade, imp], zone={(5, 5)}, examine=blade)
    ns, speech, calls = _ns(threatens=("Imp",))
    assert ns['_threat_membership'](view, view.game.cur_level, p, 5, 5) is True
    assert view.draw_calls == 0              # pixels would show the ally variant
    assert "Imp" in calls                    # answered by the one-point test


def test_wizard_examined_still_uses_the_global_zone():
    p = _player()
    view = FakeView(p, [], zone={(5, 5)}, examine=p)  # game clears wizard narrowing
    ns, speech, _calls = _ns()
    ns['_query_threat'](view)
    assert view.draw_calls == 1
    assert speech.spoken == ["Threatened"]


def test_non_unit_examine_does_not_narrow():
    p = _player()
    view = FakeView(p, [], zone=set(), examine=types.SimpleNamespace(name="Fireball"))
    ns, speech, _calls = _ns()
    ns['_query_threat'](view)
    assert view.draw_calls == 1
    assert speech.spoken == ["Safe"]


# ---- Per-unit attribution untouched ----

def test_hostile_examined_speaks_per_unit_not_zone():
    p = _player()
    imp = FakeUnit("Imp", 8, 5)
    view = FakeView(p, [imp], zone={(5, 5)}, examine=imp)
    ns, speech, calls = _ns(threatens=("Imp",))
    ns['_query_threat'](view)
    assert speech.spoken == ["Threatens you"]
    assert view.draw_calls == 0
    misser = FakeUnit("Wolf", 9, 9)
    view2 = FakeView(p, [misser], zone={(5, 5)}, examine=misser)
    ns['_query_threat'](view2)
    assert speech.spoken[-1] == "Can't hit you"


def test_ally_examined_speaks_reach_never_threat():
    # Ally-reach coverage fix (BUG_QUEUE 2026-07-09): the game draws an
    # examined ally's reach in the same red; the mod had no spoken path.
    # Reach vocabulary by owner ruling 2026-07-10 — exposure, not danger.
    p = _player()
    blade = FakeUnit("Dancing Blade", 6, 5, team=0)
    view = FakeView(p, [blade], zone={(5, 5)}, examine=blade)
    ns, speech, _calls = _ns(threatens=("Dancing Blade",))
    ns['_query_threat'](view)
    assert speech.spoken == ["You're in its reach"]
    assert view.draw_calls == 0
    healer = FakeUnit("Healer", 12, 12, team=0)
    view2 = FakeView(p, [healer], zone={(5, 5)}, examine=healer)
    ns['_query_threat'](view2)
    assert speech.spoken[-1] == "Out of its reach"


def test_berserk_ally_examined_answers_as_threat():
    # Hostility is live (are_hostile is True for a berserked same-team
    # unit, Level.py:138) — the flipped ally answers in threat wording.
    p = _player()
    blade = FakeUnit("Dancing Blade", 6, 5, team=7)   # flipped hostile
    view = FakeView(p, [blade], zone={(5, 5)}, examine=blade)
    ns, speech, _calls = _ns(threatens=("Dancing Blade",))
    ns['_query_threat'](view)
    assert speech.spoken == ["Threatens you"]


def test_cursor_on_hostile_answers_for_the_wizard_not_the_tile():
    # yujin field report 2026-07-08: look-mode cursor ON an adjacent melee
    # orc + T spoke "Can't hit you" — the per-unit check tested the cursor
    # tile (the orc's own square, which melee reach never includes) instead
    # of the wizard. The branch's referent is ALWAYS the wizard, whatever
    # ref_point rode in, and the cursor qualifier is dropped (the answer is
    # about you, not the cursor).
    p = _player()                          # wizard at (5, 5)
    orc = FakeUnit("Orc", 6, 5)            # adjacent, melee reach
    view = FakeView(p, [orc], zone={(5, 5)}, examine=orc)
    ns, speech, _calls = _ns()
    seen = []

    def melee_reach(unit, x, y):
        seen.append((x, y))
        return max(abs(unit.x - x), abs(unit.y - y)) == 1

    ns['_unit_threatens_point'] = melee_reach
    ns['_query_threat'](view, ref_point=types.SimpleNamespace(x=6, y=5),
                        qualifier="cursor")
    assert speech.spoken == ["Threatens you"]   # no "From cursor." prefix
    assert seen == [(5, 5)]                     # tested the wizard's square


# ---- The legacy time capsule ----

def test_legacy_restores_the_verbatim_enumeration():
    p = _player()
    units = [FakeUnit("Imp", 8, 5), FakeUnit("Wolf", 5, 2)]
    view = FakeView(p, units, zone={(5, 5)})
    ns, speech, _calls = _ns(legacy=True, threatens=("Imp", "Wolf"))
    ns['_query_threat'](view)
    assert view.draw_calls == 0              # legacy never touches the zone
    # Equal distances keep input order (stable sort) — Imp first.
    assert speech.spoken == ["Threatened, 2. Imp, 3 east. Wolf, 3 north"]


def test_legacy_safe_and_overflow_clause():
    p = _player()
    ns, speech, _calls = _ns(legacy=True)
    ns['_query_threat'](FakeView(p, [FakeUnit("Imp", 8, 5)]))
    assert speech.spoken == ["Safe"]
    crowd = [FakeUnit(f"Imp{i}", 6 + i, 5) for i in range(10)]
    ns2, speech2, _c2 = _ns(legacy=True,
                            threatens=tuple(u.name for u in crowd))
    ns2['_query_threat'](FakeView(p, crowd))
    assert speech2.spoken[-1].startswith("Threatened, 10")
    assert speech2.spoken[-1].endswith("and 2 more")


# ---- Wiring pins ----

def test_setting_declared_and_gate_wired():
    assert "'threat_enumeration_legacy', 'false'" in _src
    assert "threat_enumeration_legacy = _settings.getboolean" in _src
    assert "if cfg.threat_enumeration_legacy:" in _src


def test_zone_comes_from_the_games_own_builder():
    body = _src[_src.index("def _query_threat"):]
    body = body[:body.index("def _query_space")]
    assert "view.draw_threat()" in body
    assert "iter_tiles" not in body          # no mirrored zone construction