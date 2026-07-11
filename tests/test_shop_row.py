# Tests for the composable shop row (owner-ruled 2026-07-10).
#
# Behaviors pinned:
# - The row composes from four parts — name, cost, owned, details — in
#   _SHOP_ROW_ORDER, empty parts dropped. Name LEADS (was cost-first): the
#   name is what you're scanning for.
# - _shop_item_cost returns separate (cost, owned) slots; at most one is
#   non-empty today (ownership replaces price, as in the game's panel).
#   Locked rides the cost slot.
# - Upgrades route details through the game-order upgrade body, never the
#   spell-shaped reading (the Blood Horizon guard); spell details are the
#   describe segments minus the name — the name is its own slot.
# - The order tuple is the future config surface: reordering it reorders
#   the utterance with no other change (the skeleton for a settings key).

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


class FakeUpgrade:
    def __init__(self, name="Blood Lure", prereq=None):
        self.name = name
        self.prereq = prereq


_ns = {
    'Level': types.SimpleNamespace(
        CURRENCY_PICK=0, CURRENCY_MAX_HP=1, CURRENCY_GOLD=2,
        Upgrade=FakeUpgrade),
    '_main': types.SimpleNamespace(SHOP_TYPE_SHOP=3),
    '_SHOP_TYPE_SPELLS': 0,
    '_SHOP_TYPE_BESTIARY': 7,
    '_SHOP_TYPE_CRAFTING': 8,
    '_SHOP_TYPE_COMPONENT_SELECTION': 9,
    '_name': lambda o, fb="something": getattr(o, 'name', fb) or fb,
    '_describe_bestiary_entry': lambda t: f"bestiary:{t.name}",
    '_describe_craft_blueprint': lambda v, t: f"blueprint:{t.name}",
    '_describe_component': lambda v, t: f"component:{t.name}",
    '_describe_upgrade_body': lambda u: [('body', 'Summons a blood thing')],
    '_describe_spell_segments': lambda s: [
        ('name', s.name), ('tags', 'Dark, Fire'), ('level', 'Level 1')],
}
exec(_extract("    def _shop_item_cost(view, target):",
              "    _last_shop_target = [None]"), _ns)
exec(_extract("    _SHOP_ROW_ORDER = (",
              "    def _shop_search_landing(view):"), _ns)

_cost = _ns['_shop_item_cost']
_parts = _ns['_shop_row_parts']
_row = _ns['_describe_shop_row']


def _spell(name="Fireball"):
    return types.SimpleNamespace(name=name)


def _view(shop_type=0, cost=3, affordable=True, owned=False):
    game = types.SimpleNamespace(
        p1=types.SimpleNamespace(),
        cur_level=None,
        get_upgrade_cost=lambda t: cost,
        can_buy_upgrade=lambda t: affordable,
        has_upgrade=lambda t: owned,
        spell_is_upgraded=lambda s: True)
    return types.SimpleNamespace(game=game, shop_type=shop_type)


# ---- the four-part composition ----

def test_unowned_spell_row_leads_with_the_name():
    text = _row(_view(), _spell())
    assert text == "Fireball. Cost 3 SP. Dark, Fire. Level 1"


def test_owned_spell_row_speaks_owned_after_the_name():
    text = _row(_view(owned=True), _spell())
    assert text == ("Fireball. Owned, enter to view upgrades. "
                    "Dark, Fire. Level 1")


def test_cannot_afford_rides_the_cost_slot():
    text = _row(_view(affordable=False), _spell())
    assert text == "Fireball. Cost 3 SP, cannot afford. Dark, Fire. Level 1"


def test_upgrade_rows_route_the_upgrade_body():
    # The Blood Horizon guard: never the spell-shaped reading for upgrades.
    text = _row(_view(shop_type=1), FakeUpgrade())
    assert text == "Blood Lure. Cost 3 SP. Summons a blood thing"


def test_locked_rides_the_cost_slot():
    upg = FakeUpgrade(prereq=object())
    cost, owned = _cost(_view(shop_type=1, affordable=False), upg)
    assert cost == "Locked, 1 upgrade per spell"
    assert owned == ""


def test_owned_slots_are_exclusive_and_typed():
    cost, owned = _cost(_view(owned=True), _spell())
    assert (cost, owned) == ("", "Owned, enter to view upgrades")
    cost, owned = _cost(_view(shop_type=1, owned=True), _spell())
    assert (cost, owned) == ("", "Owned")


def test_free_pick_shop_drops_the_cost_part_entirely():
    v = _view(shop_type=3)
    v.game.cur_level = types.SimpleNamespace(
        cur_shop=types.SimpleNamespace(currency=0))
    text = _row(v, _spell())
    assert text == "Fireball. Dark, Fire. Level 1"


# ---- the config skeleton ----

def test_order_tuple_is_the_config_surface():
    # Reordering _SHOP_ROW_ORDER reorders the utterance with no other
    # change — the seam a future settings key plugs into.
    saved = _ns['_SHOP_ROW_ORDER']
    try:
        _ns['_SHOP_ROW_ORDER'] = ('cost', 'name', 'owned', 'details')
        text = _row(_view(), _spell())
        assert text == "Cost 3 SP. Fireball. Dark, Fire. Level 1"
    finally:
        _ns['_SHOP_ROW_ORDER'] = saved


def test_non_cost_shop_types_route_untouched():
    assert _row(_view(shop_type=7), _spell("Imp")) == "bestiary:Imp"
    assert _row(_view(shop_type=8), _spell("Helm")) == "blueprint:Helm"
    assert _row(_view(shop_type=9), _spell("Fang")) == "component:Fang"
