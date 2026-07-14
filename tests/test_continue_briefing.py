# Tests for the continue-run briefing (owner-designed 2026-07-14).
#
# Behaviors pinned:
# - Composition follows the owner's script: "Welcome back, Wizard. You are
#   on Realm X. You have [HP, shields, SP]. Status: ... You have A allies.
#   B enemies and C spawners remain. Good hunting."
# - Shields, status, and allies are omitted when absent; a cleared level
#   says "No enemies remain."
# - Status speaks bless/curse only (buff_type 1/2) — equipment passives
#   (type 0) stay silent; debuffs carry the F-vitals "Cursed" prefix;
#   turns are singular-correct ("1 turn", never "1 turns").
# - Hostility comes from the level's own are_hostile — a berserked ally
#   (team player, hostile) counts as an enemy, the tint every other mod
#   surface already follows.
# - The briefing fires AFTER the original load_game and never raises.

import sys
import textwrap
import types
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
GAME = MOD.parents[1]
for p in (str(GAME), str(MOD)):
    if p not in sys.path:
        sys.path.insert(0, p)

from helpers import _cardinal_direction

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


spoken = []
_ns = {
    'Level': types.SimpleNamespace(TEAM_PLAYER=0),
    '_name': lambda o, fb="something": getattr(o, 'name', fb) or fb,
    'async_tts': types.SimpleNamespace(speak=lambda t: spoken.append(t)),
    'log': lambda *a, **k: None,
    '_original_load_game': lambda self, filename=None: None,
    '_cardinal_direction': _cardinal_direction,
    'are_adjacent': lambda a, b: max(abs(a.x - b.x), abs(a.y - b.y)) <= 1,
}
exec(_extract("    def _continue_status_text(player):",
              "    _PyGameView.load_game = patched_load_game"), _ns)


def _buff(name, btype, turns=0):
    return types.SimpleNamespace(name=name, buff_type=btype, turns_left=turns)


def _unit(team=1, is_lair=False, name="Imp", x=10, y=10):
    return types.SimpleNamespace(team=team, is_lair=is_lair, name=name,
                                 x=x, y=y)


class _FakeLevel:
    def __init__(self, units, hostile_ids=()):
        self.units = units
        self._hostile = set(hostile_ids)

    def are_hostile(self, a, b):
        return id(b) in self._hostile


def _player(hp=40, max_hp=50, shields=0, xp=12, buffs=()):
    return types.SimpleNamespace(cur_hp=hp, max_hp=max_hp, shields=shields,
                                 xp=xp, buffs=list(buffs), x=5, y=5)


def _view(player, units=(), hostile=(), realm=5):
    level = _FakeLevel([player] + list(units),
                       hostile_ids=[id(u) for u in hostile])
    game = types.SimpleNamespace(p1=player, level_num=realm, cur_level=level)
    return types.SimpleNamespace(game=game)


def _brief(view):
    spoken.clear()
    _ns['patched_load_game'](view)
    return spoken[0] if spoken else ""


# ---- composition ----

def test_full_briefing_composition():
    enemies = [_unit(), _unit()]
    spawner = _unit(is_lair=True, name="Imp Spawner")
    ally = _unit(team=0, name="Wolf")
    p = _player(shields=2, buffs=[_buff("Poison", 2, 3)])
    v = _view(p, units=enemies + [spawner, ally], hostile=enemies + [spawner])
    assert _brief(v) == (
        "Welcome back, Wizard. You are on Realm 5. "
        "You have 40 of 50 HP, 2 shields, 12 SP. "
        "Status: Cursed Poison, 3 turns. "
        "You have 1 ally. "
        "2 enemies and 1 spawner remain. "
        "Good hunting."
    )


def test_lean_briefing_omits_absent_parts():
    # No shields, no statuses, no allies, cleared level.
    v = _view(_player())
    assert _brief(v) == (
        "Welcome back, Wizard. You are on Realm 5. "
        "You have 40 of 50 HP, 12 SP. "
        "No enemies remain. "
        "Good hunting."
    )


def test_enemies_only_singular():
    e = _unit()
    v = _view(_player(), units=[e], hostile=[e])
    assert "1 enemy remains." in _brief(v)


def test_spawners_only():
    s = _unit(is_lair=True)
    v = _view(_player(), units=[s], hostile=[s])
    assert "1 spawner remains." in _brief(v)


# ---- status filter ----

def test_passive_equipment_buffs_stay_silent():
    p = _player(buffs=[_buff("Sorcery Staff", 0),
                       _buff("Regeneration", 1, 5)])
    text = _brief(_view(p))
    assert "Sorcery Staff" not in text
    assert "Status: Regeneration, 5 turns." in text


def test_singular_turn_never_one_turns():
    p = _player(buffs=[_buff("Frozen", 2, 1)])
    text = _brief(_view(p))
    assert "Cursed Frozen, 1 turn." in text
    assert "1 turns" not in text


def test_durationless_buff_speaks_bare_name():
    p = _player(buffs=[_buff("Channeling", 1, 0)])
    assert "Status: Channeling." in _brief(_view(p))


# ---- adjacency (owner slot: after vitals, before counts) ----

def test_adjacent_hostiles_named_with_directions():
    close_w = _unit(name="Ghost", x=4, y=5)      # west of (5,5)
    close_ne = _unit(name="Imp", x=6, y=4)       # northeast (screen y+ = south)
    far = _unit(name="Goblin", x=10, y=10)
    v = _view(_player(), units=[close_w, close_ne, far],
              hostile=[close_w, close_ne, far])
    text = _brief(v)
    # Name and direction comma-separated (scan vocabulary), pairs split by
    # semicolons — designations must not run together (owner 2026-07-14).
    assert "2 adjacent: Ghost, west; Imp, northeast." in text
    # Slot order: adjacency sits between vitals and the counts.
    assert text.index("SP.") < text.index("2 adjacent:") < text.index("3 enemies")


def test_no_adjacency_line_when_clear():
    far = _unit(x=10, y=10)
    v = _view(_player(), units=[far], hostile=[far])
    assert "adjacent" not in _brief(v)


def test_adjacent_ally_not_alarmed():
    # Contact vocabulary is hostiles-only; a friendly standing next to you
    # is not a warning.
    ally = _unit(team=0, name="Wolf", x=5, y=4)
    v = _view(_player(), units=[ally])
    assert "adjacent" not in _brief(v)


def test_allies_are_their_own_sentence():
    # Final owner ruling 2026-07-14: allies standalone, then the
    # enemies-and-spawners pairing as a separate contained sentence.
    allies = [_unit(team=0, name="Wolf"), _unit(team=0, name="Bear")]
    e = _unit()
    v = _view(_player(), units=allies + [e], hostile=[e])
    assert "You have 2 allies. 1 enemy remains." in _brief(v)


def test_allies_with_cleared_level():
    ally = _unit(team=0, name="Wolf")
    v = _view(_player(), units=[ally])
    assert "You have 1 ally. No enemies remain." in _brief(v)


# ---- team classification ----

def test_berserked_ally_counts_as_enemy():
    # team player but are_hostile True — the game tints it enemy, so do we.
    berserker = _unit(team=0, name="Wolf")
    v = _view(_player(), units=[berserker], hostile=[berserker])
    text = _brief(v)
    assert "1 enemy remains." in text
    assert "allies" not in text and "1 ally" not in text


def test_neutral_units_uncounted():
    neutral = _unit(team=2)
    v = _view(_player(), units=[neutral])
    assert "No enemies remain." in _brief(v)


# ---- resilience ----

def test_no_game_is_silent_and_safe():
    v = types.SimpleNamespace(game=None)
    assert _brief(v) == ""
