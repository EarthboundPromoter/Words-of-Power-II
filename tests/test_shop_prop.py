# Tests for _describe_shop_prop — the shared Shop/Shrine body behind the
# tooltip cycle (PgUp/PgDn at rift selection) and the D-key prop detail.
# It lives nested inside the installer closure in screen_reader.py — not
# importable — so, like test_view_targeting.py, this file extracts its source
# by signature markers and execs it. A renamed/moved function breaks
# extraction LOUDLY at collection; it can never pass silently.
#
# Why this exists: rift-selection portals append their shrine as a Shop
# object (Portal.get_extra_examine_tooltips, Level.py:2773-2776), and the
# tooltip cycle had no Shop branch — shrines fell to the name-only fallback
# and their descriptions were never spoken (field report 2026-07-05,
# yujin0986). The body mirrors draw_examine_shop (RiftWizard3.py:7516):
# name; description ONLY when item-less (the shrine case); else item names.
#
# Run from the game root (Level/LevelRewards import from cwd; paths also
# derived from __file__ so `-m pytest <mod>/tests/test_shop_prop.py` works).

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
from LevelRewards import DuplicationShop, ShrineOfKnowledge
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
}
# module-level text plumbing (read_text + _fmt_of + _desc_text, 0-indent)
exec(_extract("def read_text(value, fmt=None):",
              "# Helper: identify if a unit is the player", False), _ns)
_ns['_clean_desc'] = lambda desc: _clean_desc_raw(_ns['read_text'](desc))
# the nested shop body (4-space indent)
exec(_extract("    def _describe_shop_prop(shop):",
              "    def _describe_examine_tooltip(view):", True), _ns)

_shop = _ns['_describe_shop_prop']


# ---- real shrines (item-less at rift selection -> description spoken) ----


def test_shrine_of_knowledge_speaks_description():
    text = _shop(ShrineOfKnowledge())
    assert text.startswith("Shrine of Knowledge")
    assert "Sacrifice a component" in text
    # harness convention: second call identical (no consumed state)
    assert _shop(ShrineOfKnowledge()) == text


def test_duplication_shrine_speaks_description():
    text = _shop(DuplicationShop())
    assert text.startswith("Duplication Shrine")
    assert "duplicate of a piece of equipment" in text


# ---- the game's items gate (draw_examine_shop, RiftWizard3.py:7535-7541) ----


def test_stocked_shop_lists_items_and_suppresses_description():
    shop = Level.Shop()
    shop.name = "Test Shop"
    shop.description = "Should not be heard while stocked"
    shop.items = [types.SimpleNamespace(name="Fireball"),
                  types.SimpleNamespace(name="Blink")]
    text = _shop(shop)
    assert text == "Test Shop. Items: Fireball, Blink"


def test_bare_shop_blank_description_reads_name_only():
    # Base Shop ships description " " (Level.py:2834) — must not leave a
    # dangling separator or speak whitespace
    assert _shop(Level.Shop()) == "Shop"
