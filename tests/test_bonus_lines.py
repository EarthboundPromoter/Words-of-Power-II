# Tests for _format_bonus_lines — the shared bonus-dictionary reader behind
# every tooltip body (_describe_upgrade_body and _describe_spell_segments).
# It lives nested inside the installer closure in screen_reader.py — not
# importable — so, like test_view_targeting.py, this file extracts its source
# by signature markers and execs it. A renamed/moved function breaks
# extraction LOUDLY at collection; it can never pass silently.
#
# Why this exists: the game draws one line per (source, attr) pair, which
# scans fine on screen but stutters aurally — "Blood spells gain. Blood
# spells gain. Blood spells gain" (field report 2026-07-05, ahicks; quote
# predates the 2026-07-10 scope-wording ruling). The grouped grammar keeps
# every bonus under one prefix per source, and the prefix carries the game's
# own scope wording — "spells and equipment" (text.py:204-210). These tests
# pin the grouped shape on REAL gear (Blood Staff, Iron Legion's Banner) and
# fabricated edge cases (multi-tag, gain/lose split, stats filter, resists).
#
# Run from the game root (Level/Equipment import from cwd; paths also derived
# from __file__ so `-m pytest <mod>/tests/test_bonus_lines.py` works).

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
import Level  # noqa: F401
import Equipment as EQ

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    return textwrap.dedent(_src[start:end])


_ns = {
    '_name': lambda o, fb="": (getattr(o, 'name', None) or fb),
    '_main': types.SimpleNamespace(),  # no tt_attrs -> falls back to _tt_attrs
}
# _tt_attrs + _fmt_attr + _format_bonus_lines in one contiguous block
exec(_extract("    _tt_attrs = [", "    # _clean_desc imported from helpers.py"), _ns)

_bonus = _ns['_format_bonus_lines']


def _obj(**kwargs):
    return types.SimpleNamespace(**kwargs)


class _Tag:
    # SimpleNamespace is unhashable; bonus dicts key on the tag object
    def __init__(self, name):
        self.name = name


def _tag(name):
    return _Tag(name)


# ---- real gear ----


def test_blood_staff_groups_all_five_bonuses_under_one_prefix():
    lines = _bonus(EQ.BloodStaff())
    assert lines == [
        "Blood spells and equipment gain 50% Minion Health, 50% Damage, "
        "1 Max Charges, 2 Range, 3 Minion Damage"
    ]
    # extraction-harness convention: second call identical (no consumed state)
    assert _bonus(EQ.BloodStaff()) == lines


def test_single_bonus_item_reads_one_plain_sentence():
    # Iron Legion's Banner: one tag bonus -> one sentence, no grouping artifacts
    assert _bonus(EQ.IronLegionsBanner()) == ["Metallic spells and equipment gain 1 Num Summons"]


# ---- grouping shape ----


def test_multi_tag_item_reads_tag_by_tag_not_draw_order():
    fire, ice = _tag("Fire"), _tag("Ice")
    o = _obj(
        tag_bonuses_pct={fire: {'damage': 25}, ice: {'damage': 10}},
        tag_bonuses={fire: {'radius': 1}, ice: {'duration': 2}},
    )
    assert _bonus(o) == [
        "Fire spells and equipment gain 25% Damage, 1 Radius",
        "Ice spells and equipment gain 10% Damage, 2 Duration",
    ]


def test_global_gain_and_lose_stay_separate_prefixes():
    o = _obj(global_bonuses_pct={'damage': 10}, global_bonuses={'range': 1, 'duration': -2})
    assert _bonus(o) == [
        "All spells and equipment gain 10% Damage, 1 Range",
        "All spells and equipment lose -2 Duration",
    ]


def test_resists_stay_standalone_lines_after_bonus_groups():
    fire = _tag("Fire")
    o = _obj(tag_bonuses={fire: {'damage': 5, 'radius': 1}},
             resists={_tag("Fire"): 50, _tag("Ice"): 25})
    assert _bonus(o) == [
        "Fire spells and equipment gain 5 Damage, 1 Radius",
        "50% Fire resist",
        "25% Ice resist",
    ]


def test_zero_values_still_skipped():
    fire = _tag("Fire")
    o = _obj(tag_bonuses={fire: {'damage': 0, 'radius': 1}})
    assert _bonus(o) == ["Fire spells and equipment gain 1 Radius"]


# ---- mixed scope: global + tag on one object ----


def test_global_bonuses_stay_distinguished_from_tag_bonuses():
    # The one genuinely mixed object in the game is Multicast's spell-weaving
    # buff (MulticastBuff, Spells.py:6258-6260): +1 quick cast globally, -1
    # back from Sorcery. Grouping keys on the full source prefix, so global
    # lines keep their own "All spells" sentence and can never fold under a
    # tag heading (owner regression check 2026-07-10; census of all 1,097
    # shop/equipment objects found no other mixed case).
    sorcery = _tag("Sorcery")
    o = _obj(tag_bonuses={sorcery: {'quick_cast': -1}},
             global_bonuses={'quick_cast': 1})
    assert _bonus(o) == [
        "Sorcery spells and equipment gain -1 Quick Cast",
        "All spells and equipment gain 1 Quick Cast",
    ]


# ---- per-spell bonuses ----


class _FakeSpell:
    name = "Fireball"
    stats = ['damage', 'radius']


def test_spell_bonuses_group_and_keep_stats_filter():
    o = _obj(spell_bonuses={_FakeSpell: {'damage': 4, 'radius': 1, 'num_summons': 2}})
    # num_summons not in the spell's displayed stats -> filtered, as the game
    # filters (RiftWizard3.py:7138); the rest grouped under one prefix
    assert _bonus(o) == ["Fireball gains 4 Damage, 1 Radius"]


def test_upgrade_new_attributes_group_with_spell_bonuses_for_same_spell():
    prereq = types.SimpleNamespace(name="Fireball")
    o = _obj(spell_bonuses={_FakeSpell: {'damage': 4}},
             prereq=prereq,
             new_attributes={'duration': 3, 'not_displayed': 9})
    # 'duration' is in _tt_attrs; 'not_displayed' filtered as the game filters
    # (RiftWizard3.py:7150). Same spell name -> same prefix, one sentence.
    assert _bonus(o) == ["Fireball gains 4 Damage, 3 Duration"]
