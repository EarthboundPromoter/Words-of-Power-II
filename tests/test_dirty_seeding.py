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
