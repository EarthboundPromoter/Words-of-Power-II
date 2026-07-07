# Tests for prop speech on cursor-over and under units.
#
# Two owner rulings, 2026-07-06 (from Neurrone's field reports):
# 1. Props the game routes to its plain renderer (draw_examine_misc,
#    RiftWizard3.py:7207-7225) — HeartDot, MemoryOrb, the walk-on shrines —
#    speak their full description on both cursor paths (Look mode +
#    targeting brief), matching the game's panel on cursor-over. The two
#    common pickups respect speak_pickup_effects (occupancy-independent);
#    shrines always speak.
# 2. A unit standing on a prop must not hide it: the targeting brief, the
#    Tab-cycle read, and Look mode's portal branch all speak what's beneath
#    (the game's map holds the prop and highlight-objects redraws it on top,
#    RiftWizard3.py:5724-5733). Reverse direction too: a unit standing on a
#    portal speaks before the portal chunks.
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


class _FakeTTS:
    def __init__(self):
        self.spoken = []
        self.batched = []

    def speak(self, text):
        self.spoken.append(text)

    def speak_batched(self, chunks):
        self.batched.append(list(chunks))


_tts = _FakeTTS()
_cfg = types.SimpleNamespace(speak_pickup_effects=True, show_coordinates=False)

_ns = {
    'Level': Level,
    'cfg': _cfg,
    'async_tts': _tts,
    '_telemetry': types.SimpleNamespace(emit=lambda *a, **k: None),
    '_name': lambda o, fb="": (getattr(o, 'name', None) or fb),
    'log': lambda *a, **k: None,
    '_is_player': lambda u: getattr(u, 'is_player_controlled', False),
    '_describe_unit_tier1': lambda u: f"UNIT:{getattr(u, 'name', '?')}",
    '_describe_portal': lambda p, v: "PORTAL",
    '_describe_portal_chunks': lambda p, v: ["Rift", "Contents: Imps"],
    '_get_on_death_text': lambda u: None,
    '_own_aura_clauses': lambda v, p: [],
    '_route_tile_suppress': [False],   # slice 5: routed jumps mute the announce
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
# the Tab-cycle read + its prop-beneath helper
exec(_extract("    def _prop_beneath_text(view, unit):",
              "    def patched_cycle_tab(self):", True), _ns)
# Look mode's announcer (the portal-branch unit chunk)
exec(_extract("    def _announce_look_tile(view, point):",
              "    def _announce_target_tile(view, point):", True), _ns)

_is_misc_prop = _ns['_is_misc_prop']
_tile = _ns['_describe_tile']
_brief = _ns['_describe_tile_brief']
_target = _ns['_describe_target']
_look = _ns['_announce_look_tile']


def _make_tile(prop=None, unit=None, cloud=None):
    return types.SimpleNamespace(
        prop=prop, unit=unit, cloud=cloud,
        is_chasm=False, is_wall=lambda: False,
    )


def _view_with(tile):
    level = types.SimpleNamespace(
        tiles=[[tile]],
        is_point_in_bounds=lambda p: True,
    )
    return types.SimpleNamespace(game=types.SimpleNamespace(cur_level=level))


def _enemy(name="Iron Imp"):
    return types.SimpleNamespace(name=name, cur_hp=10, max_hp=12, x=0, y=0)


_PT = Level.Point(0, 0)

_HEART_TEXT = "Ruby Heart. Increase max HP by 25 and restore all health"


# ---- the reported case: Ruby Heart speaks its effect on cursor-over ----


def test_ruby_heart_look_mode_speaks_effect():
    assert _tile(_view_with(_make_tile(prop=Level.HeartDot())), _PT) == _HEART_TEXT


def test_ruby_heart_targeting_brief_speaks_effect():
    assert _brief(_view_with(_make_tile(prop=Level.HeartDot())), _PT) == _HEART_TEXT


def test_memory_orb_speaks_effect():
    assert _brief(_view_with(_make_tile(prop=Level.MemoryOrb())), _PT) == \
        "Memory Orb. Grants 1 SP"


# ---- walk-on shrines: build-committing effects, markup transcoded ----


def test_spider_shrine_speaks_description_with_markup_transcoded():
    text = _tile(_view_with(_make_tile(prop=SpiderShrine())), _PT)
    assert text.startswith("Shrine of Spiders")
    assert "Gain the Spider tag" in text
    assert "Webs will no longer immobilize you" in text
    assert "[" not in text


def test_soul_shrine_speaks_description():
    text = _brief(_view_with(_make_tile(prop=SoulShrine())), _PT)
    assert text.startswith("Shrine of Necromancy")
    assert "Gain the Undead tag" in text
    assert "[" not in text


def test_perfection_shrine_speaks_description():
    assert _brief(_view_with(_make_tile(prop=ShrineOfPerfection())), _PT) == \
        "Shrine of Perfection. A random spell you know gains all upgrades"


# ---- the class gate: gated prop types keep their existing treatments ----


def test_shop_prop_stays_name_only_on_cursor():
    shop = Level.Shop()
    shop.name = "Test Shop"
    shop.description = "Must stay behind the shop gate"
    assert _brief(_view_with(_make_tile(prop=shop)), _PT) == "Test Shop"
    assert _tile(_view_with(_make_tile(prop=shop)), _PT) == "Test Shop"


def test_component_pickup_stays_name_only_on_cursor():
    pickup = object.__new__(Level.ComponentPickup)
    pickup.name = "Bone"
    pickup.description = "Component text stays on the D key"
    assert _brief(_view_with(_make_tile(prop=pickup)), _PT) == "Bone"


def test_class_gate_mirrors_draw_examine_dispatch():
    assert _is_misc_prop(Level.HeartDot())
    assert _is_misc_prop(Level.MemoryOrb())
    assert _is_misc_prop(SoulShrine())
    assert not _is_misc_prop(Level.Shop())
    assert not _is_misc_prop(object.__new__(Level.Portal))
    assert not _is_misc_prop(object.__new__(Level.ComponentPickup))


# ---- unit standing on a prop must not hide it (Neurrone report 2) ----


def test_unit_on_heart_targeting_brief_reads_both():
    tile = _make_tile(prop=Level.HeartDot(), unit=_enemy())
    assert _brief(_view_with(tile), _PT) == \
        f"Iron Imp. 10 of 12 HP. {_HEART_TEXT}"


def test_unit_in_cloud_targeting_brief_reads_both():
    tile = _make_tile(unit=_enemy(),
                      cloud=types.SimpleNamespace(name="Poison Cloud"))
    assert _brief(_view_with(tile), _PT) == \
        "Iron Imp. 10 of 12 HP. Poison Cloud"


def test_unit_on_heart_look_mode_reads_both():
    tile = _make_tile(prop=Level.HeartDot(), unit=_enemy())
    assert _tile(_view_with(tile), _PT) == f"UNIT:Iron Imp. {_HEART_TEXT}"


def test_tab_cycle_reads_prop_beneath():
    unit = _enemy()
    tile = _make_tile(prop=Level.HeartDot(), unit=unit)
    view = _view_with(tile)
    view._examine_target = unit
    assert _target(view) == f"UNIT:Iron Imp. {_HEART_TEXT}"


def test_tab_cycle_stale_position_guard():
    # The tile at the unit's coordinates holds a DIFFERENT unit — the prop
    # must not be attributed to the described one
    unit = _enemy()
    tile = _make_tile(prop=Level.HeartDot(), unit=_enemy("Other Imp"))
    view = _view_with(tile)
    view._examine_target = unit
    assert _target(view) == "UNIT:Iron Imp"


def test_look_mode_unit_on_portal_speaks_unit_first():
    portal = types.SimpleNamespace(name="Rift", level_gen_params=object())
    tile = _make_tile(prop=portal, unit=_enemy())
    _tts.batched.clear()
    _look(_view_with(tile), _PT)
    assert _tts.batched == [["UNIT:Iron Imp", "Rift", "Contents: Imps"]]


def test_look_mode_portal_coordinates_ride_the_unit_chunk():
    portal = types.SimpleNamespace(name="Rift", level_gen_params=object())
    tile = _make_tile(prop=portal, unit=_enemy())
    _tts.batched.clear()
    _cfg.show_coordinates = True
    try:
        _look(_view_with(tile), _PT)
    finally:
        _cfg.show_coordinates = False
    assert _tts.batched == [["UNIT:Iron Imp (0,0)", "Rift", "Contents: Imps"]]


# ---- the speak_pickup_effects config (owner 2026-07-06): common pickups
# ---- go name-only when false; shrines unaffected; occupancy-independent ----


def test_pickup_suppression_silences_heart_and_orb_only():
    _cfg.speak_pickup_effects = False
    try:
        assert _brief(_view_with(_make_tile(prop=Level.HeartDot())), _PT) == \
            "Ruby Heart"
        assert _brief(_view_with(_make_tile(prop=Level.MemoryOrb())), _PT) == \
            "Memory Orb"
        # shrines always speak
        text = _brief(_view_with(_make_tile(prop=SoulShrine())), _PT)
        assert "Gain the Undead tag" in text
    finally:
        _cfg.speak_pickup_effects = True


def test_pickup_suppression_applies_under_a_unit_too():
    _cfg.speak_pickup_effects = False
    try:
        tile = _make_tile(prop=Level.HeartDot(), unit=_enemy())
        assert _brief(_view_with(tile), _PT) == \
            "Iron Imp. 10 of 12 HP. Ruby Heart"
    finally:
        _cfg.speak_pickup_effects = True
