# Pure-logic tests for the Root-1 container-diff core (Unit 1, plan step 1).
# SimpleNamespace fakes only — no game import, no journal (the test_digest
# lightweight pattern). The boundary wraps and journal coupling are covered
# by the heavy install-once file (test_journal_capture_gate.py) in later
# build steps.
#
# Run from the game root: python -m pytest "<mod>/tests/test_container_diff.py"

import sys
import os
from collections import defaultdict
from types import SimpleNamespace

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

import container_diff as cd


class FakeTag:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, FakeTag) and other.name == self.name


FIRE = FakeTag('Fire')
DARK = FakeTag('Dark')
HOLY = FakeTag('Holy')
UNDEAD = FakeTag('Undead')


class FakeSpell:
    """Hashable (dict-key) stand-in for a Spell instance — cool_downs is
    keyed by spell OBJECTS (Level.py:913)."""
    def __init__(self, name, cur_charges=None):
        self.name = name
        if cur_charges is not None:
            self.cur_charges = cur_charges


def _unit(player=False, **over):
    u = SimpleNamespace(
        resists=defaultdict(int),
        tags=[],
        cool_downs={},
        turns_to_death=None,
        is_player_controlled=player,
    )
    if player:
        u.global_bonuses = defaultdict(int)
        u.global_bonuses_pct = defaultdict(int)
        u.spell_bonuses = defaultdict(lambda: defaultdict(int))
        u.spell_bonuses_pct = defaultdict(lambda: defaultdict(int))
        u.tag_bonuses = defaultdict(lambda: defaultdict(int))
        u.tag_bonuses_pct = defaultdict(lambda: defaultdict(int))
    for k, v in over.items():
        setattr(u, k, v)
    return u


def _store():
    return cd.ContainerStore()


# ---- Baseline-not-change ----

def test_first_observation_is_baseline_not_change():
    s = _store()
    u = _unit()
    u.resists[FIRE] = 100
    u.tags.append(UNDEAD)
    assert s.diff_unit(u) == []
    # And the baseline actually took: an unchanged second pass is silent.
    assert s.diff_unit(u) == []


def test_reseed_rebaselines_silently():
    s = _store()
    u = _unit()
    assert s.diff_unit(u) == []
    u.resists[FIRE] = 100
    assert len(s.diff_unit(u)) == 1
    s.reseed()
    u.resists[DARK] = 50  # changed while unobserved
    assert s.diff_unit(u) == []  # baseline again, no flood


def test_id_reuse_after_drop_baselines_as_new():
    s = _store()
    u = _unit()
    u.resists[FIRE] = 100
    assert s.diff_unit(u) == []
    # Simulate id reuse: a different object under the same store key.
    stale_snap = s._units[id(u)][1]
    impostor = _unit()
    s._units[id(impostor)] = (u, stale_snap)  # entry ref mismatch
    assert s.diff_unit(impostor) == []  # baseline, not a false diff


# ---- Zero == absent ----

def test_explicit_zero_equals_absent():
    s = _store()
    u = _unit()
    u.resists[FIRE] = 0  # explicit zero at baseline
    assert s.diff_unit(u) == []
    u.resists[FIRE] = 100
    deltas = s.diff_unit(u)
    assert deltas == [('resists', {'Fire': (0, 100)})]
    u.resists[FIRE] = 0  # +=100 then -=100 leaves an explicit 0
    deltas = s.diff_unit(u)
    assert deltas == [('resists', {'Fire': (100, 0)})]
    assert s.diff_unit(u) == []  # explicit 0 now equals its absence


# ---- Read-only observation ----

def test_diff_never_inserts_into_live_defaultdicts():
    s = _store()
    u = _unit(player=True)
    u.resists[FIRE] = 100
    u.spell_bonuses['FireballSpell']['damage'] = 5
    key_counts = (len(u.resists), len(u.spell_bonuses),
                  len(u.global_bonuses), len(u.tag_bonuses))
    s.diff_unit(u)   # baseline
    s.diff_unit(u)   # compare pass
    assert (len(u.resists), len(u.spell_bonuses),
            len(u.global_bonuses), len(u.tag_bonuses)) == key_counts


def test_snapshot_is_value_copy_not_alias():
    s = _store()
    u = _unit()
    u.resists[FIRE] = 100
    s.diff_unit(u)  # baseline snapshots by value
    u.resists[FIRE] = 250  # in-place mutation of the live dict
    deltas = s.diff_unit(u)
    assert deltas == [('resists', {'Fire': (100, 250)})]


# ---- Tags ----

def test_tags_added_and_removed():
    s = _store()
    u = _unit(tags=[FIRE])
    s.diff_unit(u)
    u.tags.append(UNDEAD)
    u.tags.append(HOLY)
    u.tags.remove(FIRE)
    deltas = s.diff_unit(u)
    assert deltas == [('tags', {'added': ['Holy', 'Undead'],
                                'removed': ['Fire']})]
    assert s.diff_unit(u) == []


# ---- Nested bonus domains (wizard only) ----

def test_nested_bonus_delta_and_wizard_gating():
    s = _store()
    wiz = _unit(player=True)
    s.diff_unit(wiz)
    wiz.spell_bonuses['FireballSpell']['damage'] = 7
    deltas = s.diff_unit(wiz)
    assert deltas == [('spell_bonuses', {'FireballSpell': {'damage': (0, 7)}})]

    # A monster's bonus dicts are outside the render gate: not diffed.
    mon = _unit()
    mon.spell_bonuses = defaultdict(lambda: defaultdict(int))
    s.diff_unit(mon)
    mon.spell_bonuses['Bite']['damage'] = 3
    assert s.diff_unit(mon) == []


def test_nested_bonus_removal_reports_to_zero():
    s = _store()
    wiz = _unit(player=True)
    wiz.tag_bonuses['Fire']['damage'] = 4
    s.diff_unit(wiz)
    wiz.tag_bonuses['Fire']['damage'] = 0
    deltas = s.diff_unit(wiz)
    assert deltas == [('tag_bonuses', {'Fire': {'damage': (4, 0)}})]


# ---- Routine-tick suppression ----

def test_routine_cooldown_tick_absorbed_snapshot_advances():
    s = _store()
    spell_a, spell_b = FakeSpell('Bite'), FakeSpell('Gaze')
    u = _unit(cool_downs={spell_a: 3, spell_b: 1})
    s.diff_unit(u)
    # The engine's per-turn sweep: {spell: cd-1 for ... if cd > 1}
    u.cool_downs = {spell_a: 2}
    assert s.diff_unit(u) == []          # absorbed, no record
    u.cool_downs = {spell_a: 1}
    assert s.diff_unit(u) == []          # absorbed again -> snapshot advanced
    u.cool_downs = {}
    assert s.diff_unit(u) == []          # final 1 -> gone is routine


def test_cooldown_deviation_records_in_full():
    s = _store()
    spell_a = FakeSpell('Gaze')
    u = _unit(cool_downs={spell_a: 5})
    s.diff_unit(u)
    u.cool_downs = {spell_a: 2}          # a halving, not a -1 tick
    deltas = s.diff_unit(u)
    assert deltas == [('cool_downs', {'Gaze': (5, 2)})]


def test_new_cooldown_set_records():
    s = _store()
    spell_a = FakeSpell('Gaze')
    u = _unit(cool_downs={})
    s.diff_unit(u)
    u.cool_downs = {spell_a: 6}          # pay_costs write
    deltas = s.diff_unit(u)
    assert deltas == [('cool_downs', {'Gaze': (0, 6)})]


def test_routine_lifespan_tick_absorbed_but_refresh_records():
    s = _store()
    u = _unit(turns_to_death=5)
    s.diff_unit(u)
    u.turns_to_death = 4
    assert s.diff_unit(u) == []          # routine -1
    u.turns_to_death = 9                 # a refresh/extension
    deltas = s.diff_unit(u)
    assert deltas == [('turns_to_death', {'turns_to_death': (4, 9)})]
    u.turns_to_death = 8
    assert s.diff_unit(u) == []


def test_mixed_cooldown_change_not_split_records_full_delta():
    s = _store()
    spell_a, spell_b = FakeSpell('Bite'), FakeSpell('Gaze')
    u = _unit(cool_downs={spell_a: 3, spell_b: 4})
    s.diff_unit(u)
    # Routine tick AND a real halving land between sweeps: the delta fails
    # the signature and records in full — no attempt to split the pair.
    u.cool_downs = {spell_a: 2, spell_b: 1}
    deltas = s.diff_unit(u)
    assert deltas == [('cool_downs', {'Bite': (3, 2), 'Gaze': (4, 1)})]


# ---- The spell shelf ----

def test_spell_shelf_baseline_change_and_churn():
    s = _store()
    fireball = SimpleNamespace(name='Fireball', cur_charges=5)
    blink = SimpleNamespace(name='Blink', cur_charges=2)
    wiz = _unit(player=True, spells=[fireball, blink])
    assert s.diff_spells(wiz) == []      # baseline
    fireball.cur_charges = 4
    assert s.diff_spells(wiz) == [('Fireball', 5, 4)]
    # Membership churn: a learned spell baselines, a removed one drops.
    learned = SimpleNamespace(name='Volcano', cur_charges=1)
    wiz.spells = [fireball, learned]     # Blink removed at a shrine
    assert s.diff_spells(wiz) == []
    learned.cur_charges = 0
    assert s.diff_spells(wiz) == [('Volcano', 1, 0)]
    assert id(blink) not in s._spells    # dropped, no leak


def test_spell_shelf_refund_records_gain():
    s = _store()
    fireball = SimpleNamespace(name='Fireball', cur_charges=1)
    wiz = _unit(player=True, spells=[fireball])
    s.diff_spells(wiz)
    fireball.cur_charges = 3             # refund_charges
    assert s.diff_spells(wiz) == [('Fireball', 1, 3)]


# ---- Kind mapping ----

def test_domain_kind_mapping_complete():
    for dom in ('resists', 'tags', 'cool_downs', 'turns_to_death',
                'global_bonuses', 'global_bonuses_pct', 'spell_bonuses',
                'spell_bonuses_pct', 'tag_bonuses', 'tag_bonuses_pct'):
        assert cd.domain_kind(dom) in cd.ALL_KINDS
