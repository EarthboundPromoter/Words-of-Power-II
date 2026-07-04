"""Step-2 tests: wrap-at-seed on real game units (plan REV B, D2).

Exercises the two seed points built in step 2 — the store's baseline branch
and the add_obj wrap — plus the owner-aware re-own guard and the bootstrap
gap the gate found (a write between add and the first sweep must produce a
record, not fold into a late baseline).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import Level

import types as _types
sys.modules.setdefault('steamworks', _types.ModuleType('steamworks'))
import Game  # noqa: F401

import pytest

from journal import journal, install_hooks
import container_diff
import dirty_containers
from dirty_containers import DirtyDict, DirtyDefaultDict, DirtyList


@pytest.fixture(autouse=True)
def _clean():
    dirty_containers.dirty_ids.clear()
    yield
    dirty_containers.dirty_ids.clear()


def _fresh_level():
    install_hooks()
    container_diff.install()
    container_diff.reseed()
    lvl = Level.Level(15, 15)
    journal.reset(id(lvl))
    journal._level = lvl
    return lvl


def _unit(name, hp=20):
    u = Level.Unit()
    u.name = name
    u.max_hp = hp
    return u


def test_add_obj_seeds_and_wraps():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    assert isinstance(u.resists, DirtyDefaultDict)
    assert isinstance(u.cool_downs, DirtyDict)
    assert isinstance(u.tags, DirtyList)
    assert u.resists._owner_id == id(u)
    assert u.cool_downs._owner_id == id(u)
    assert u.tags._owner_id == id(u)


def test_wrapped_containers_preserve_contents_and_factory():
    lvl = _fresh_level()
    u = _unit("Wolf")
    u.resists[Level.Tags.Fire] = 50
    lvl.add_obj(u, 3, 3)
    assert u.resists[Level.Tags.Fire] == 50
    # defaultdict read-miss parity survives wrapping
    assert u.resists[Level.Tags.Ice] == 0


def test_mutation_after_add_marks_dirty():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    dirty_containers.dirty_ids.clear()
    u.resists[Level.Tags.Fire] += 25
    assert id(u) in dirty_containers.dirty_ids
    dirty_containers.dirty_ids.clear()
    u.tags.append(Level.Tags.Undead)
    assert id(u) in dirty_containers.dirty_ids


def test_seed_is_idempotent():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    wrapped = u.resists
    container_diff.store.diff_unit(u)
    container_diff.store.diff_unit(u)
    assert u.resists is wrapped  # same object, not re-wrapped


def test_foreign_wrapper_reowned_in_place():
    # The Grey Goo donation shape (Equipment.py:3066-3079, generalized):
    # a container already owned by ANOTHER unit lands on this one. The
    # guard re-owns the same object — identity preserved (the game may
    # intend the alias), marks now credit the new owner.
    lvl = _fresh_level()
    donor = _unit("Donor")
    lvl.add_obj(donor, 2, 2)
    receiver = _unit("Receiver")
    lvl.add_obj(receiver, 4, 4)
    donated = donor.resists
    object.__setattr__(receiver, 'resists', donated)
    container_diff.store.drop_unit(receiver)  # force re-baseline
    container_diff.store.diff_unit(receiver)
    assert receiver.resists is donated            # identity kept
    assert donated._owner_id == id(receiver)      # ownership moved
    dirty_containers.dirty_ids.clear()
    receiver.resists[Level.Tags.Fire] = 100
    assert id(receiver) in dirty_containers.dirty_ids
    assert id(donor) not in dirty_containers.dirty_ids


def test_write_between_add_and_first_sweep_records():
    # The bootstrap gap (gate: semantics lane #2, adversary F6). Add-time
    # baselining means an in-place write BEFORE the first boundary sweep
    # is a real delta at that sweep — today's full sweep within one
    # boundary gave the same outcome; the drained sweep (step 4) will rely
    # on this seed point entirely.
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    u.resists[Level.Tags.Fire] = 75
    before = len(journal.records)
    container_diff.sweep(lvl, site='test:first-boundary')
    new = [r for r in journal.records[before:]
           if r['event_type'] == container_diff.KIND_RESISTS]
    assert len(new) == 1
    payload = new[0]['payload']
    assert payload['unit']['name'] == "Wolf"


# ---- step 3: the rebind/scalar watch (through the REAL setattr path) ----

def test_cooldown_comprehension_rebind_rewraps_and_marks():
    # The engine's per-turn shape (Level.py:2108): pre_advance rebinds
    # cool_downs to a fresh PLAIN dict via comprehension. The watch must
    # mark the unit and re-wrap the incoming dict, or coverage silently
    # lapses from that turn on.
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    dirty_containers.dirty_ids.clear()
    u.cool_downs = {k: v - 1 for k, v in u.cool_downs.items() if v > 1}
    assert id(u) in dirty_containers.dirty_ids
    assert isinstance(u.cool_downs, DirtyDict)
    assert u.cool_downs._owner_id == id(u)


def test_resists_rebind_grey_goo_shape():
    # Equipment.py:3076 generalized: a plain template dict assigned onto a
    # live unit's resists. Watch marks + wraps; contents preserved.
    lvl = _fresh_level()
    u = _unit("Slime")
    lvl.add_obj(u, 3, 3)
    dirty_containers.dirty_ids.clear()
    from collections import defaultdict
    donor = defaultdict(lambda: 0)
    donor[Level.Tags.Poison] = 100
    u.resists = donor
    assert id(u) in dirty_containers.dirty_ids
    assert isinstance(u.resists, DirtyDefaultDict)
    assert u.resists._owner_id == id(u)
    assert u.resists[Level.Tags.Poison] == 100


def test_turns_to_death_scalar_marks():
    lvl = _fresh_level()
    u = _unit("Wisp")
    lvl.add_obj(u, 3, 3)
    dirty_containers.dirty_ids.clear()
    u.turns_to_death = 5
    assert id(u) in dirty_containers.dirty_ids
    dirty_containers.dirty_ids.clear()
    u.turns_to_death -= 1
    assert id(u) in dirty_containers.dirty_ids


# ---- step 4: the drained sweep ----

def test_dirty_sweep_records_and_drains():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    u.resists[Level.Tags.Fire] = 50
    before = len(journal.records)
    container_diff.sweep(lvl, site='test:drain')
    new = [r for r in journal.records[before:]
           if r['event_type'] == container_diff.KIND_RESISTS]
    assert len(new) == 1
    assert not dirty_containers.dirty_ids     # drained


def test_silent_write_missed_by_drain_caught_by_full():
    # The escaped-write shape the D6 backstop exists for: a mutation that
    # bypasses the wrapper (unbound vanilla setitem) marks nothing. The
    # drained sweep must miss it (that's the design's honest trade); the
    # full-mode turn-boundary sweep must catch it.
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    dict.__setitem__(u.resists, Level.Tags.Fire, 50)   # silent, no mark
    before = len(journal.records)
    container_diff.sweep(lvl, site='test:drain')
    assert not [r for r in journal.records[before:]
                if r['event_type'] == container_diff.KIND_RESISTS]
    container_diff.sweep(lvl, site='test:backstop', full=True)
    caught = [r for r in journal.records[before:]
              if r['event_type'] == container_diff.KIND_RESISTS]
    assert len(caught) == 1


def test_wizard_nested_bonus_recorded_with_empty_dirty_set():
    # The always-wizard rule (D5, gate amendment): nested bonus writes go
    # through inner dicts no wrapper sees — the dirty set stays EMPTY —
    # yet the sweep must still record them, because the wizard is compared
    # unconditionally on every sweep.
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=100)
    wiz.is_player_controlled = True
    lvl.add_obj(wiz, 5, 5)
    lvl.player_unit = wiz
    container_diff.sweep(lvl, site='test:baseline')
    wiz.tag_bonuses[Level.Tags.Fire]['damage'] += 5
    assert not dirty_containers.dirty_ids     # nothing marked — the point
    before = len(journal.records)
    container_diff.sweep(lvl, site='test:nested')
    new = [r for r in journal.records[before:]
           if r['event_type'] == container_diff.KIND_STAT_BONUS]
    assert len(new) == 1


def test_reseed_clears_dirty_set():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    u.resists[Level.Tags.Fire] = 50
    assert dirty_containers.dirty_ids
    container_diff.reseed()
    assert not dirty_containers.dirty_ids


# ---- step 5: the turn-boundary backstop alarm ----

class _FakeTelemetry:
    def __init__(self):
        self.emitted = []

    def is_enabled(self):
        return True

    def emit(self, kind, **fields):
        self.emitted.append((kind, fields))


def test_escaped_write_raises_backstop_alarm():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    dict.__setitem__(u.resists, Level.Tags.Fire, 50)   # bypasses wrapper
    tel = _FakeTelemetry()
    container_diff.turn_boundary(lvl, telemetry_mod=tel)
    escaped = [(k, f) for k, f in tel.emitted
               if k == 'container_escaped_write']
    assert len(escaped) == 1
    assert escaped[0][1]['domain'] == 'resists'
    assert escaped[0][1]['unit'] == "Wolf"


def test_marked_write_does_not_alarm():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    u.resists[Level.Tags.Fire] = 50                    # normal, marks
    tel = _FakeTelemetry()
    container_diff.turn_boundary(lvl, telemetry_mod=tel)
    assert not [k for k, _ in tel.emitted if k == 'container_escaped_write']


def test_wizard_nested_write_exempt_from_alarm():
    # The wizard's nested bonus writes never mark BY DESIGN (always-compare
    # covers them) — the backstop must not cry wolf about the one unit
    # whose unmarked deltas are expected.
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=100)
    wiz.is_player_controlled = True
    lvl.add_obj(wiz, 5, 5)
    lvl.player_unit = wiz
    container_diff.sweep(lvl, site='test:baseline')
    wiz.tag_bonuses[Level.Tags.Fire]['damage'] += 5
    tel = _FakeTelemetry()
    container_diff.turn_boundary(lvl, telemetry_mod=tel)
    assert not [k for k, _ in tel.emitted if k == 'container_escaped_write']


def test_escaped_alarm_dedupes_per_domain_and_unit():
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    dict.__setitem__(u.resists, Level.Tags.Fire, 50)
    tel = _FakeTelemetry()
    container_diff.turn_boundary(lvl, telemetry_mod=tel)
    dict.__setitem__(u.resists, Level.Tags.Fire, 75)   # same shape again
    container_diff.turn_boundary(lvl, telemetry_mod=tel)
    escaped = [k for k, _ in tel.emitted if k == 'container_escaped_write']
    assert len(escaped) == 1


# ---- step 6: save/load round-trip (the mechanism patched_on_loaded runs) ----

def test_save_load_roundtrip_vanilla_payload_and_reseed():
    # The game saves with dill (Game.py:12); same machinery here. Three
    # claims in one flow: (1) the save payload references NO mod classes;
    # (2) loaded units come back with PLAIN containers; (3) the post-load
    # reseed + seed-and-wrap pass (what patched_on_loaded now does) makes
    # the first post-load write a real, recorded delta — not a silent
    # baseline fold, the gate's Finding-6 failure mode.
    import dill
    lvl = _fresh_level()
    u = _unit("Wolf")
    lvl.add_obj(u, 3, 3)
    u.resists[Level.Tags.Fire] = 50
    container_diff.sweep(lvl, site='test:steady')      # steady state

    payload = dill.dumps(lvl)
    assert b'DirtyDict' not in payload
    assert b'DirtyDefaultDict' not in payload
    assert b'DirtyList' not in payload
    assert b'dirty_containers' not in payload

    lvl2 = dill.loads(payload)
    u2 = next(un for un in lvl2.units if un.name == "Wolf")
    from collections import defaultdict
    assert type(u2.resists) is defaultdict              # plain after load
    assert u2.resists[Level.Tags.Fire] == 50            # data survived

    # The patched_on_loaded pass:
    container_diff.reseed()
    journal.reset(id(lvl2), lvl2)
    journal._level = lvl2
    for un in list(lvl2.units):
        container_diff.store.diff_unit(un)

    assert isinstance(u2.resists, DirtyDefaultDict)     # re-wrapped
    assert u2.resists._owner_id == id(u2)

    # First post-load write records instead of folding:
    u2.resists[Level.Tags.Ice] = 25
    assert id(u2) in dirty_containers.dirty_ids
    before = len(journal.records)
    container_diff.sweep(lvl2, site='test:first-post-load')
    new = [r for r in journal.records[before:]
           if r['event_type'] == container_diff.KIND_RESISTS]
    assert len(new) == 1


def test_unseeded_template_assignment_untouched():
    # Monsters factories assign unit.tags = [...] etc. at construction,
    # before add_obj — the watch must leave templates alone (no wrap, no
    # mark); they wrap at seed.
    _fresh_level()  # watch installed
    dirty_containers.dirty_ids.clear()
    u = _unit("Template")
    u.tags = [Level.Tags.Fire]
    u.resists = {Level.Tags.Fire: 50}
    assert not isinstance(u.tags, DirtyList)
    assert not isinstance(u.resists, (DirtyDict, DirtyDefaultDict))
    assert id(u) not in dirty_containers.dirty_ids
