# Live-Level tests for the no-event capture GATE (the spawn-vs-runtime line the
# __setattr__ interceptor uses to decide whether a watched write — shields /
# team — is a runtime change worth voicing or spawn-init/off-field noise to
# drop). Unlike test_journal.py (pure record builders on SimpleNamespace), these
# drive real add_obj / remove_obj / add_shields through the patched hooks on a
# real Level, because the gate (_is_live_unit) only means anything against the
# live `level.units` list.
#
# Run from the game root: python -m pytest "<mod>/tests/test_journal_capture_gate.py"
#
# Coverage:
#   - runtime shield write on a live unit -> captured           (baseline)
#   - construction write on a not-yet-added unit -> dropped     (spawn-init)
#   - on-summon grant during the EventOnUnitAdded raise -> captured   (R7)
#   - off-field write to a killed/removed unit -> dropped       (R22)
#   - reincarnation respawn shield restore -> not voiced        (R22 end-to-end)

import sys
import os

import pytest

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

import Level
from journal import journal, install_hooks


# ---- Harness ----

def _fresh_level():
    install_hooks()  # idempotent
    lvl = Level.Level(15, 15)
    journal.reset(id(lvl))
    journal._level = lvl
    return lvl


def _unit(name, hp=20, player=False):
    u = Level.Unit()
    u.name = name
    u.max_hp = hp
    u.is_player_controlled = player
    return u


def _place(lvl, unit, x, y):
    lvl.add_obj(unit, x, y)
    return unit


def _shield_records(target_id=None):
    out = [r for r in journal.records
           if r["event_type"] in ("shield_gained", "shield_stripped")]
    if target_id is not None:
        out = [r for r in out
               if (r["payload"].get("target") or {}).get("id") == target_id]
    return out


# ---- Baseline: runtime write on a live unit captures ----

def test_runtime_shield_write_on_live_unit_captured():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ogre.shields = 3
    recs = _shield_records(id(ogre))
    assert len(recs) == 1
    assert recs[0]["event_type"] == "shield_gained"
    assert recs[0]["payload"]["amount"] == 3


# ---- Spawn-init: write on a not-yet-placed unit is dropped ----

def test_construction_write_before_add_is_dropped():
    lvl = _fresh_level()
    ogre = _unit("Ogre")            # constructed, NOT added -> level is None
    ogre.shields = 5                # a factory/baseline grant
    assert _shield_records(id(ogre)) == []


# ---- R7: on-summon grant during the EventOnUnitAdded raise is captured ----

def test_on_summon_grant_during_unit_added_captured():
    # Mirrors MagicMinionShield (Equipment.py:517): a global EventOnUnitAdded
    # handler that grants shields to the just-added unit. The grant fires at
    # Level.py:3908, AFTER units.append (3907) but BEFORE ever_spawned=True
    # (3910) — the straddle the old gate dropped. _is_live_unit reads the unit
    # in `units`, so it now captures.
    lvl = _fresh_level()

    def on_added(evt):
        if not evt.unit.is_player_controlled:
            evt.unit.add_shields(3)

    lvl.event_manager.register_global_trigger(Level.EventOnUnitAdded, on_added)

    wolf = _place(lvl, _unit("Wolf"), 6, 6)
    recs = _shield_records(id(wolf))
    assert len(recs) == 1, "on-summon shield grant must be captured (R7)"
    assert recs[0]["event_type"] == "shield_gained"
    assert recs[0]["payload"]["amount"] == 3


# ---- R22: off-field write to a killed/removed unit is dropped ----

def test_off_field_write_to_removed_unit_dropped():
    # After kill(), remove_obj pops the unit from level.units (Level.py:3947).
    # A shield write then (as ReincarnationBuff.respawn does at 1251, before the
    # re-add) is off-field noise, not a runtime gain -> dropped.
    lvl = _fresh_level()
    ghost = _place(lvl, _unit("Ghost"), 4, 4)
    ghost.shields = 2                       # live -> captured
    assert len(_shield_records(id(ghost))) == 1

    lvl.remove_obj(ghost)                   # off-field now
    ghost.shields = 9                       # respawn-style restore
    assert len(_shield_records(id(ghost))) == 1, \
        "off-field shield write must not be voiced (R22)"


# ---- R22 end-to-end: reincarnation respawn does not phantom-voice shields ----

def test_reincarnation_respawn_shields_not_voiced():
    from CommonContent import ReincarnationBuff

    lvl = _fresh_level()
    martyr = _place(lvl, _unit("Martyr", hp=20), 7, 7)
    buff = ReincarnationBuff(lives=1)
    martyr.apply_buff(buff)
    # a placed unit so the respawn has somewhere to land
    _place(lvl, _unit("Bystander"), 1, 1)

    seq_before = journal.sequence
    martyr.kill()                            # queues respawn
    while lvl.can_advance_spells():
        lvl.advance_spells()

    phantom = [r for r in journal.records
               if r["sequence"] > seq_before
               and r["event_type"] in ("shield_gained", "shield_stripped")
               and (r["payload"].get("target") or {}).get("id") == id(martyr)]
    assert phantom == [], \
        "reincarnation respawn must not phantom-voice a shield change (R22)"
