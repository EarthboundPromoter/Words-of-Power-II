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


# ---- R21: add_shields / remove_shields cap-clamp double-write -> one net record ----

def _new_records_after(seq, target_id):
    return [r for r in _shield_records(target_id) if r["sequence"] > seq]


def test_add_shields_cap_crossing_single_net_record():
    # 19 + 5 -> `+=` to 24, then min(24,20)=20: two interceptor fires unbracketed
    # (+5 gain, then -4 strip). Bracketed, it must voice one net +1.
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ogre.shields = 19
    seq = journal.sequence
    ogre.add_shields(5)
    recs = _new_records_after(seq, id(ogre))
    assert len(recs) == 1, "cap-crossing add_shields must voice one net record"
    assert recs[0]["event_type"] == "shield_gained"
    assert recs[0]["payload"]["amount"] == 1
    assert ogre.shields == 20


def test_remove_shields_underflow_single_net_record():
    # 2 - 5 -> `-=` to -3, then clamp to 0: two fires unbracketed (-5, then +3).
    # Bracketed, one net -2.
    lvl = _fresh_level()
    ghoul = _place(lvl, _unit("Ghoul"), 5, 5)
    ghoul.shields = 2
    seq = journal.sequence
    ghoul.remove_shields(5)
    recs = _new_records_after(seq, id(ghoul))
    assert len(recs) == 1, "underflow remove_shields must voice one net record"
    assert recs[0]["event_type"] == "shield_stripped"
    assert recs[0]["payload"]["amount_removed"] == 2
    assert ghoul.shields == 0


def test_add_shields_below_cap_single_record():
    # Normal (no clamp) grant still voices exactly one record with the true gain.
    lvl = _fresh_level()
    imp = _place(lvl, _unit("Imp"), 5, 5)
    imp.shields = 5
    seq = journal.sequence
    imp.add_shields(3)
    recs = _new_records_after(seq, id(imp))
    assert len(recs) == 1
    assert recs[0]["event_type"] == "shield_gained"
    assert recs[0]["payload"]["amount"] == 3
    assert imp.shields == 8


def test_add_shields_uncapped_can_exceed_20():
    # cap=False skips the clamp (FinalBosses.py:1546), so no second write and the
    # net record reflects the full uncapped total.
    lvl = _fresh_level()
    titan = _place(lvl, _unit("Titan"), 5, 5)
    titan.shields = 19
    seq = journal.sequence
    titan.add_shields(6, cap=False)
    recs = _new_records_after(seq, id(titan))
    assert len(recs) == 1
    assert recs[0]["payload"]["amount"] == 6
    assert titan.shields == 25


def test_add_shields_respects_outer_suppress():
    # A setter called inside an outer suppress bracket (e.g. within refresh) must
    # not emit, and the store must still happen. Guards the save/restore of the
    # net-emit path.
    lvl = _fresh_level()
    u = _place(lvl, _unit("Unit"), 5, 5)
    prev = journal._suppress_watched_capture
    journal._suppress_watched_capture = True
    try:
        seq = journal.sequence
        u.add_shields(3)
        recs = _new_records_after(seq, id(u))
    finally:
        journal._suppress_watched_capture = prev
    assert recs == []
    assert u.shields == 3          # the store still ran despite suppression


# ---- R5: Crisis Charm silent-heal capture ----

def _silent_heals(target_id=None):
    out = [r for r in journal.records if r["event_type"] == "silent_heal"]
    if target_id is not None:
        out = [r for r in out
               if (r["payload"].get("target") or {}).get("id") == target_id]
    return out


def test_crisis_charm_save_captured():
    # Crisis Charm restores the owner to full on a would-be-lethal hit via a raw
    # cur_hp write (no EventOnHealed). The targeted wrapper snapshots cur_hp and
    # emits a silent_heal with the real restored magnitude.
    from Equipment import CrisisCharm
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    charm = CrisisCharm()
    wiz.equip(charm)                 # registers the trigger; makes unequip valid
    wiz.cur_hp = 0                   # a would-be-lethal state (charm fires ≤ 0)
    charm.on_damage(None)            # on_damage ignores evt; wrapper captures
    recs = _silent_heals(id(wiz))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["source_name"] == "Crisis Charm"
    assert p["heal_amount"] == 50
    assert p["target"]["is_player_controlled"] is True
    assert wiz.cur_hp == 50


def test_crisis_charm_no_fire_no_record():
    # Above 0 HP the charm early-returns (no restore) -> no silent_heal.
    from Equipment import CrisisCharm
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    charm = CrisisCharm()
    wiz.equip(charm)
    wiz.cur_hp = 30                  # not lethal
    charm.on_damage(None)
    assert _silent_heals(id(wiz)) == []
    assert wiz.cur_hp == 30


# ---- R5: capture-only silent heals (staged for Track B, not voiced interim) ----

def test_ruby_heart_captures_silent_heal():
    # HeartDot.on_player_enter: max_hp += 25 then cur_hp = max_hp (raw, no event).
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 20
    heart = Level.HeartDot()
    lvl.add_prop(heart, 8, 8)
    heart.on_player_enter(wiz)
    recs = _silent_heals(id(wiz))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["source_name"] == "Ruby Heart"
    assert p["max_hp_gained"] == 25
    assert p["heal_amount"] == 55       # cur 20 -> 75 (max 50->75)
    assert wiz.cur_hp == 75


def test_component_pickup_captures_silent_heal():
    # Heart Fragment on_pickup: max_hp += 10, cur_hp += 10 — via the floor
    # ComponentPickup choke point, source = the component's name.
    from Components import HeartFragment
    import collections
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    wiz.component_tags = collections.defaultdict(int)   # real player init sets this
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 40
    pickup = Level.ComponentPickup(HeartFragment())
    lvl.add_prop(pickup, 8, 8)
    pickup.on_player_enter(wiz)
    recs = _silent_heals(id(wiz))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["source_name"] == "Heart Fragment"
    assert p["heal_amount"] == 10       # cur 40 -> 50
    assert p["max_hp_gained"] == 10


def test_non_healing_component_pickup_no_record():
    # A component that changes no HP produces no silent_heal (net delta 0).
    from Components import BurningEmber
    import collections
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    wiz.component_tags = collections.defaultdict(int)
    _place(lvl, wiz, 7, 7)
    pickup = Level.ComponentPickup(BurningEmber())
    lvl.add_prop(pickup, 8, 8)
    pickup.on_player_enter(wiz)
    assert _silent_heals(id(wiz)) == []


def test_reincarnation_respawn_emits_silent_heal():
    # The respawn full-restore is now captured as ground truth (capture-only;
    # inert in the interim — owned by the reincarnate announcement).
    from CommonContent import ReincarnationBuff
    lvl = _fresh_level()
    martyr = _place(lvl, _unit("Martyr", hp=20), 7, 7)
    martyr.apply_buff(ReincarnationBuff(lives=1))
    _place(lvl, _unit("Bystander"), 1, 1)
    seq_before = journal.sequence
    martyr.kill()
    while lvl.can_advance_spells():
        lvl.advance_spells()
    heals = [r for r in journal.records if r["sequence"] > seq_before
             and r["event_type"] == "silent_heal"
             and (r["payload"].get("target") or {}).get("id") == id(martyr)]
    assert len(heals) == 1
    assert heals[0]["payload"]["source_name"] == "Reincarnation"
    assert heals[0]["payload"]["heal_amount"] == martyr.max_hp   # 0 -> full
