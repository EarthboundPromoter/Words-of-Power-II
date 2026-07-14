# The suite's first VIEW-LAYER test file (promoted from the S36 behavioral
# harness, owner ruling 2026-07-03). The targeting/Look functions live nested
# inside the installer closure in screen_reader.py — not importable — so this
# file extracts their source by signature markers and execs them against a
# REAL Level with REAL game spells (Fireball / Blink / Melt+MassMelt). A
# renamed/moved function breaks extraction LOUDLY at collection (update the
# marker); it can never pass silently.
#
# Why this exists: the 898-test suite was structurally blind to the view
# layer — the get_points_in_ball generator-exhaustion bug silenced every AoE
# census while the suite stayed green (caught by ear, S36). Every check here
# calls the function TWICE and asserts identical output: a reintroduced
# generator (or any consumed-iterator regression) fails the second call.
#
# Run from the game root (Level/Spells import from cwd; paths also derived
# from __file__ so `-m pytest <mod>/tests/test_view_targeting.py` works).

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
import Spells
import CommonContent
from helpers import _pluralize  # noqa: F401

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator, dedent):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    block = _src[start:end]
    return textwrap.dedent(block) if dedent else block


_cfg = types.SimpleNamespace(aoe_group_names=True, show_coordinates=True)
_ns = {
    'Level': Level,
    '_pluralize': _pluralize,
    'log': lambda m: None,
    '_name': lambda o, fb="": (getattr(o, 'name', None) or fb),
    '_is_player': lambda u: bool(getattr(u, 'is_player_controlled', False)),
    'cfg': _cfg,
}
# module-level helper (0-indent)
exec(_extract("def _get_cast_failure_reason(spell, x, y):", "# ====", False), _ns)
# nested view functions (4-space indent)
exec(_extract("    def _check_aoe_warning(view):",
              "    def _describe_target(view):", True), _ns)
exec(_extract("    def _own_aura_clauses(view, point):",
              "    def _announce_look_tile(view, point):", True), _ns)

_check = _ns['_check_aoe_warning']
_aura = _ns['_own_aura_clauses']
_reason = _ns['_get_cast_failure_reason']


# ---- world builders (fresh per test — no cross-test level state) ----


def _unit(name, hp=20, player=False, team=None):
    u = Level.Unit()
    u.name = name
    u.max_hp = hp
    u.is_player_controlled = player
    if team is not None:
        u.team = team
    return u


def _world():
    """Wizard at (7,7); Orc (3,7), Goblins (3,6)/(3,5), ally Wolf (4,7)."""
    lvl = Level.Level(15, 15)
    wiz = _unit("Wizard", hp=50, player=True, team=Level.TEAM_PLAYER)
    lvl.add_obj(wiz, 7, 7)
    lvl.add_obj(_unit("Orc"), 3, 7)
    lvl.add_obj(_unit("Goblin"), 3, 6)
    lvl.add_obj(_unit("Goblin"), 3, 5)
    lvl.add_obj(_unit("Wolf", team=Level.TEAM_PLAYER), 4, 7)
    return lvl, wiz


def _give(wizard, spell):
    spell.caster = wizard
    spell.owner = wizard
    spell.statholder = wizard
    spell.cur_charges = getattr(spell, 'max_charges', 1) or 1
    return spell


def _view(lvl, wizard, spell, tx, ty):
    return types.SimpleNamespace(
        cur_spell=spell,
        cur_spell_target=Level.Point(tx, ty),
        game=types.SimpleNamespace(p1=wizard, cur_level=lvl),
    )


def _twice(v):
    """The generator-regression pin: every check runs the function twice and
    both calls must agree (a consumed iterator diverges on call two)."""
    a = _check(v)
    b = _check(v)
    assert a == b, f"repeat-call mismatch (generator class): {a!r} vs {b!r}"
    return b


# ---- the S36 composition rulings, pinned ----


def test_fireball_census_allies_before_enemies():
    lvl, wiz = _world()
    fb = _give(wiz, Spells.FireballSpell())
    rw, info, suffix = _twice(_view(lvl, wiz, fb, 3, 7))
    assert rw == ""
    assert info == "Within AoE 1 ally, 3 enemies."   # You-ALLIES-enemies order
    assert suffix == ""


def test_blink_valid_tile_is_silent():
    # Single-tile footprint: census only when footprint exceeds the cursor
    # tile — the #17 Blink false-positive class dies by construction.
    lvl, wiz = _world()
    bl = _give(wiz, Spells.BlinkSpell())
    assert _twice(_view(lvl, wiz, bl, 8, 8)) == ("", "", "")


def test_blink_out_of_range_speaks_the_balls_truth():
    lvl, wiz = _world()
    bl = _give(wiz, Spells.BlinkSpell())
    assert _twice(_view(lvl, wiz, bl, 14, 14)) == ("Out of range. ", "", "")


def test_fireball_no_los_reason_at_cursor():
    lvl, wiz = _world()
    fb = _give(wiz, Spells.FireballSpell())
    for wy in range(4, 10):
        lvl.make_wall(5, wy)
    assert _twice(_view(lvl, wiz, fb, 3, 7)) == ("No line of sight. ", "", "")


def test_mass_melt_chain_names_target_first_cursor_excluded():
    lvl, wiz = _world()
    melt = _give(wiz, Spells.MeltSpell())
    melt.mass_melt = 1
    melt.num_targets = 6
    rw, info, suffix = _twice(_view(lvl, wiz, melt, 3, 7))
    assert info == ""                # linked group: no count census
    assert suffix == "2 Goblins."    # cursor unit (Orc) excluded, dup-grouped


def test_mass_melt_toggle_off_falls_back_to_count():
    lvl, wiz = _world()
    melt = _give(wiz, Spells.MeltSpell())
    melt.mass_melt = 1
    melt.num_targets = 6
    _cfg.aoe_group_names = False
    try:
        rw, info, suffix = _twice(_view(lvl, wiz, melt, 3, 7))
        # Count census includes the cursor unit (total-blast rule): 3, not 2.
        assert info == "Within AoE 3 enemies."
        assert suffix == ""
    finally:
        _cfg.aoe_group_names = True


def test_plain_melt_single_unit_footprint_is_silent():
    lvl, wiz = _world()
    melt = _give(wiz, Spells.MeltSpell())
    assert _twice(_view(lvl, wiz, melt, 3, 7)) == ("", "", "")


def test_own_aura_look_clauses():
    lvl, wiz = _world()
    ab = CommonContent.DamageAuraBuff(damage=6, damage_type=Level.Tags.Fire,
                                      radius=5)
    ab.name = "Fire Aura"
    ab.owner = wiz
    wiz.buffs.append(ab)
    v = types.SimpleNamespace(game=types.SimpleNamespace(p1=wiz))
    assert _aura(v, Level.Point(9, 9)) == ["In your Fire Aura."]
    assert _aura(v, Level.Point(14, 1)) == []


# ---- cursor-over-self exception (owner ruling 2026-07-03) ----


def test_cursor_over_self_reason_suppressed_tile_unburied():
    # Hover on the wizard's own tile with a can_target_self=False spell:
    # the reason prefix is suppressed so the tile data (the player
    # character) speaks unburied.
    lvl, wiz = _world()
    fb = _give(wiz, Spells.FireballSpell())   # can_target_self False (Level.py:557)
    assert _twice(_view(lvl, wiz, fb, 7, 7)) == ("", "", "")


def test_cursor_over_self_reason_still_exists_for_confirm():
    # The exception is hover-only: the confirm path ([Cast Fail],
    # patched_cast_cur_spell) reads the same vocabulary, which must
    # still produce the reason on an actual Enter press.
    lvl, wiz = _world()
    fb = _give(wiz, Spells.FireballSpell())
    assert _reason(fb, 7, 7) == "can't target self"


def test_tile_dynamic_reason_at_own_tile_still_speaks():
    # Scope pin: ONLY the can't-target-self string is excepted. A
    # must_target_empty spell hovered on the wizard's own (occupied) tile
    # keeps its tile-dynamic reason.
    lvl, wiz = _world()
    fb = _give(wiz, Spells.FireballSpell())
    fb.can_target_self = True
    fb.must_target_empty = True
    assert _twice(_view(lvl, wiz, fb, 7, 7)) == ("Tile occupied. ", "", "")


# ---- Blood Bullet obstruction (owner-ruled 2026-07-14) ----
# The mod's first per-spell stop rule. Pins: the first unit that would
# actually eat the shot is named problems-first, Ally-tagged when friendly
# (friendly fire); the cursor's own unit never counts as an obstruction;
# Blessed Blood passes Dark/Demon/Undead; spells without a stop rule can
# never borrow the warning (the disjointness pin).


def test_blood_bullet_ally_obstruction_named():
    # Wolf (4,7) stands on the line to Orc (3,7): the bullet stops there.
    lvl, wiz = _world()
    bb = _give(wiz, Spells.BloodBullet())
    rw, _info, _suffix = _twice(_view(lvl, wiz, bb, 3, 7))
    assert rw == "Obstructed by Ally Wolf (4,7). "


def test_blood_bullet_cursor_on_first_unit_is_clean():
    lvl, wiz = _world()
    ghost = _unit("Ghost")
    ghost.tags = [Level.Tags.Dark]
    lvl.add_obj(ghost, 5, 7)
    bb = _give(wiz, Spells.BloodBullet())
    rw, _info, _suffix = _twice(_view(lvl, wiz, bb, 5, 7))
    assert rw == ""


def test_blood_bullet_clear_line_no_warning():
    lvl, wiz = _world()
    bb = _give(wiz, Spells.BloodBullet())
    rw, _info, _suffix = _twice(_view(lvl, wiz, bb, 7, 3))
    assert rw == ""


def test_blessed_blood_passes_dark_units():
    # Blessed Blood penetrates Dark/Demon/Undead (Spells.py:15558-15560):
    # blessed, the Dark Ghost at (5,7) no longer obstructs — the Wolf
    # behind it becomes the true stopper.
    lvl, wiz = _world()
    ghost = _unit("Ghost")
    ghost.tags = [Level.Tags.Dark]
    lvl.add_obj(ghost, 5, 7)
    bb = _give(wiz, Spells.BloodBullet())
    rw, _info, _suffix = _twice(_view(lvl, wiz, bb, 3, 7))
    assert rw == "Obstructed by Ghost (5,7). "
    bb.blessed = 1
    rw, _info, _suffix = _twice(_view(lvl, wiz, bb, 3, 7))
    assert rw == "Obstructed by Ally Wolf (4,7). "


def test_other_line_spells_never_obstructed():
    # Disjointness pin: hit-everything beams must not borrow the rule —
    # the table keys by name, never by "footprint is a line".
    lvl, wiz = _world()
    fb = _give(wiz, Spells.FireballSpell())
    rw, _info, _suffix = _twice(_view(lvl, wiz, fb, 3, 7))
    assert "Obstructed" not in rw
