# Tests for misc-prop descriptions on cursor-over — Look mode (_describe_tile)
# and the spell-targeting brief (_describe_tile_brief) speak the full
# description of props the game routes to its plain renderer
# (draw_examine_misc, RiftWizard3.py:7207-7225): HeartDot, MemoryOrb, the
# walk-on shrines. Owner ruling 2026-07-06 (from Neurrone's Ruby Heart
# report): the game's panel shows the description on mere cursor-over, and
# stepping on the tile commits the effect — so both cursor paths speak it.
# Portals, shops, and components keep their existing gated treatments.
#
# The describers live nested inside the installer closure in screen_reader.py
# — not importable — so, like test_shop_prop.py, this file extracts their
# source by signature markers and execs it. A renamed/moved function breaks
# extraction LOUDLY at collection; it can never pass silently.
#
# Run from the game root (Level/LevelRewards import from cwd; paths also
# derived from __file__ so `-m pytest <mod>/tests/<this file>` works).

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
from LevelRewards import ShrineOfPerfection, SpiderShrine, SoulShrine
from helpers import _clean_desc as _clean_desc_raw

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator, dedent):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    block = _src[start:end]
    return textwrap.dedent(block) if dedent else block


_ns = {
    'Level': Level,
    '_name': lambda o, fb="": (getattr(o, 'name', None) or fb),
    'log': lambda *a, **k: None,
    '_is_player': lambda u: getattr(u, 'is_player_controlled', False),
    '_describe_unit_tier1': lambda u: f"UNIT:{getattr(u, 'name', '?')}",
    '_describe_portal': lambda p, v: "PORTAL",
    '_get_on_death_text': lambda u: None,
}
# module-level text plumbing (read_text + _fmt_of + _desc_text, 0-indent)
exec(_extract("def read_text(value, fmt=None):",
              "# Helper: identify if a unit is the player", False), _ns)
_ns['_clean_desc'] = lambda desc: _clean_desc_raw(_ns['read_text'](desc))
# the misc-prop class gate + both tile describers (4-space indent)
exec(_extract("    def _is_misc_prop(prop):",
              "    def _describe_tile_brief(view, point):", True), _ns)
exec(_extract("    def _describe_tile_brief(view, point):",
              "    def _describe_portal_chunks(portal, view):", True), _ns)

_is_misc_prop = _ns['_is_misc_prop']
_tile = _ns['_describe_tile']
_brief = _ns['_describe_tile_brief']


def _view_with(tile):
    level = types.SimpleNamespace(
        tiles=[[tile]],
        is_point_in_bounds=lambda p: True,
    )
    return types.SimpleNamespace(game=types.SimpleNamespace(cur_level=level))


def _prop_tile(prop):
    return types.SimpleNamespace(
        prop=prop, unit=None, cloud=None,
        is_chasm=False, is_wall=lambda: False,
    )


_PT = Level.Point(0, 0)


# ---- the reported case: Ruby Heart speaks its effect on cursor-over ----


def test_ruby_heart_look_mode_speaks_effect():
    text = _tile(_view_with(_prop_tile(Level.HeartDot())), _PT)
    assert text == "Ruby Heart. Increase max HP by 25 and restore all health"


def test_ruby_heart_targeting_brief_speaks_effect():
    text = _brief(_view_with(_prop_tile(Level.HeartDot())), _PT)
    assert text == "Ruby Heart. Increase max HP by 25 and restore all health"


def test_memory_orb_speaks_effect():
    assert _brief(_view_with(_prop_tile(Level.MemoryOrb())), _PT) == \
        "Memory Orb. Grants 1 SP"


# ---- walk-on shrines: build-committing effects, markup transcoded ----


def test_spider_shrine_speaks_description_with_markup_transcoded():
    text = _tile(_view_with(_prop_tile(SpiderShrine())), _PT)
    assert text.startswith("Shrine of Spiders")
    assert "Gain the Spider tag" in text
    assert "Webs will no longer immobilize you" in text
    assert "[" not in text


def test_soul_shrine_speaks_description():
    text = _brief(_view_with(_prop_tile(SoulShrine())), _PT)
    assert text.startswith("Shrine of Necromancy")
    assert "Gain the Undead tag" in text
    assert "[" not in text


def test_perfection_shrine_speaks_description():
    text = _brief(_view_with(_prop_tile(ShrineOfPerfection())), _PT)
    assert text == ("Shrine of Perfection. "
                    "A random spell you know gains all upgrades")


# ---- the class gate: gated prop types keep their existing treatments ----


def test_shop_prop_stays_name_only_on_cursor():
    shop = Level.Shop()
    shop.name = "Test Shop"
    shop.description = "Must stay behind the shop gate"
    assert _brief(_view_with(_prop_tile(shop)), _PT) == "Test Shop"
    assert _tile(_view_with(_prop_tile(shop)), _PT) == "Test Shop"


def test_component_pickup_stays_name_only_on_cursor():
    pickup = object.__new__(Level.ComponentPickup)
    pickup.name = "Bone"
    pickup.description = "Component text stays on the D key"
    assert _brief(_view_with(_prop_tile(pickup)), _PT) == "Bone"


def test_class_gate_mirrors_draw_examine_dispatch():
    assert _is_misc_prop(Level.HeartDot())
    assert _is_misc_prop(Level.MemoryOrb())
    assert _is_misc_prop(SoulShrine())
    assert not _is_misc_prop(Level.Shop())
    assert not _is_misc_prop(object.__new__(Level.Portal))
    assert not _is_misc_prop(object.__new__(Level.ComponentPickup))
