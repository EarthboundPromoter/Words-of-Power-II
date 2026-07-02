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
import log_capture
import container_diff


# ---- Harness ----

def _fresh_level():
    install_hooks()  # idempotent
    # The canonical (sole) install site for the oracle wrap in the suite —
    # log_capture.install() joins install_hooks() in the shared, unrestored,
    # whole-process monkeypatch category, so every test here runs with the
    # production Level.log shape (game_log records interleaved; all helpers
    # filter by kind).
    log_capture.install()
    # Same category for the Root-1 container-diff wraps (Unit 1); the store
    # reseeds per fresh level like the journal resets — first sweep after a
    # reseed baselines silently.
    container_diff.install()
    container_diff.reseed()
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


def _src(name="Test"):
    # Minimal damage source: deal_damage reads source.name / source.owner.
    import types
    return types.SimpleNamespace(name=name, owner=None)


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


def _hp_losses(target_id=None):
    out = [r for r in journal.records if r["event_type"] == "hp_loss"]
    if target_id is not None:
        out = [r for r in out
               if (r["payload"].get("target") or {}).get("id") == target_id]
    return out


def _max_hp_changes(target_id=None):
    out = [r for r in journal.records if r["event_type"] == "max_hp_change"]
    if target_id is not None:
        out = [r for r in out
               if (r["payload"].get("target") or {}).get("id") == target_id]
    return out


def test_silent_cur_hp_gain_captured_universally():
    # A raw cur_hp increase on a live unit (no deal_damage, no event) is captured
    # by the interceptor — the universal chokepoint, no per-site hook.
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 20
    wiz.cur_hp = 45                    # raw silent heal
    recs = _silent_heals(id(wiz))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["heal_amount"] == 25
    assert p["cur_hp_before"] == 20 and p["cur_hp_after"] == 45
    assert p["source_name"] is None   # attribution is a Track-B cause-walk


def test_raw_cur_hp_loss_is_hp_loss_not_silent_heal():
    # G-G (Unit 4): a raw cur_hp DECREASE is captured as hp_loss — and is
    # never mis-shaped as a heal. (Pre-Unit-4 this test asserted decreases
    # produced NOTHING; the gains/losses boundary now lives here.)
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 20                    # from 50 -> 20
    assert _silent_heals(id(wiz)) == []
    recs = _hp_losses(id(wiz))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["loss_amount"] == 30
    assert p["cur_hp_before"] == 50 and p["cur_hp_after"] == 20
    assert p["source_name"] is None   # attribution is the cause-walk's job


def test_deal_damage_heal_not_double_captured():
    # A heal THROUGH deal_damage raises EventOnHealed AND writes cur_hp; the
    # deal_damage bracket suppresses the interceptor's cur_hp capture -> no
    # double (exactly one EventOnHealed, zero silent_heal).
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 20
    import Level as L
    wiz.deal_damage(-15, L.Tags.Heal, _src("Heal"))     # heal 15 via deal_damage
    assert _silent_heals(id(wiz)) == []
    healed = [r for r in journal.records if r["event_type"] == "EventOnHealed"
              and (r["payload"].get("target") or {}).get("id") == id(wiz)]
    assert len(healed) == 1
    assert wiz.cur_hp == 35


def test_crisis_charm_save_captured_through_deal_damage():
    # THE real path: Crisis Charm fires inside deal_damage's EventOnDamaged raise
    # (Level.py:4119). The deal_damage bracket suppresses cur_hp, but raise_event
    # un-brackets it — so the charm's silent restore IS captured, not swallowed.
    from Equipment import CrisisCharm
    import Level as L
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    wiz.equip(CrisisCharm())
    wiz.cur_hp = 5
    lvl.deal_damage(7, 7, 40, L.Tags.Physical, _src("Blow"))  # lethal -> charm restores
    recs = _silent_heals(id(wiz))
    assert len(recs) == 1, "Crisis Charm restore must survive the deal_damage bracket"
    p = recs[0]["payload"]
    assert p["cur_hp_before"] <= 0 and p["cur_hp_after"] == wiz.max_hp
    assert wiz.cur_hp == 50


def test_soulbound_clamp_captured():
    # Soulbound clamps cur_hp to 1 on a would-be-lethal hit (CommonContent.py:1392)
    # — captured universally with zero Soulbound-specific code. Works on an ENEMY.
    from CommonContent import Soulbound
    import Level as L
    lvl = _fresh_level()
    guardian = _place(lvl, _unit("Guardian", hp=10, player=False), 3, 3)
    enemy = _place(lvl, _unit("Cultist", hp=20, player=False), 7, 7)
    enemy.apply_buff(Soulbound(guardian))
    enemy.cur_hp = 5
    lvl.deal_damage(7, 7, 40, L.Tags.Physical, _src("Blow"))  # lethal -> clamp to 1
    recs = _silent_heals(id(enemy))
    assert len(recs) == 1, "Soulbound clamp-to-1 must be captured"
    assert recs[0]["payload"]["cur_hp_after"] == 1
    assert enemy.cur_hp == 1


def test_ruby_heart_captures_heal_and_max_change():
    # HeartDot.on_player_enter: max_hp += 25 (max_hp_change) then cur_hp = max_hp
    # (silent_heal) — captured via the interceptor, two records, no wrapper.
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 20
    heart = Level.HeartDot()
    lvl.add_prop(heart, 8, 8)
    heart.on_player_enter(wiz)
    heals = _silent_heals(id(wiz))
    maxes = _max_hp_changes(id(wiz))
    assert len(heals) == 1 and heals[0]["payload"]["heal_amount"] == 55  # 20->75
    assert len(maxes) == 1 and maxes[0]["payload"]["delta"] == 25
    assert maxes[0]["payload"]["direction"] == "gained"
    assert wiz.cur_hp == 75


def test_component_pickup_captured_universally():
    # Heart Fragment on_pickup (max += 10, cur += 10) via the floor pickup path —
    # captured by the interceptor with no ComponentPickup-specific hook.
    from Components import HeartFragment
    import collections
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    wiz.component_tags = collections.defaultdict(int)
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 40
    pickup = Level.ComponentPickup(HeartFragment())
    lvl.add_prop(pickup, 8, 8)
    pickup.on_player_enter(wiz)
    assert len(_silent_heals(id(wiz))) == 1
    assert _silent_heals(id(wiz))[0]["payload"]["heal_amount"] == 10   # 40 -> 50
    assert len(_max_hp_changes(id(wiz))) == 1


def test_max_hp_drain_captured():
    # A raw max_hp decrease is captured as a 'drained' max_hp_change.
    lvl = _fresh_level()
    u = _place(lvl, _unit("Cursed", hp=30, player=False), 5, 5)
    u.max_hp = 25                      # drain 5
    recs = _max_hp_changes(id(u))
    assert len(recs) == 1
    assert recs[0]["payload"]["delta"] == -5
    assert recs[0]["payload"]["direction"] == "drained"


def test_reincarnation_respawn_off_field_dropped():
    # The respawn restore happens while the unit is off-field (removed from
    # level.units between kill and re-add) -> dropped by the in-units gate. It is
    # owned by the reincarnate event, not a standalone heal. (R22 shield safety
    # is preserved; see test_reincarnation_respawn_shields_not_voiced above.)
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
    assert heals == []


# ---- G-F (Unit 4): xp (SP) watch — bidirectional, uniform, name-explicit ----

def _xp_changes(target_id=None):
    out = [r for r in journal.records if r["event_type"] == "xp_change"]
    if target_id is not None:
        out = [r for r in out
               if (r["payload"].get("target") or {}).get("id") == target_id]
    return out


def test_xp_gain_captured_with_none_coercion():
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.xp = 5                          # first live write: before is None -> 0
    recs = _xp_changes(id(wiz))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["xp_before"] == 0 and p["xp_after"] == 5 and p["delta"] == 5
    wiz.xp += 3                         # Memory-Orb-shaped gain (Level.py:2796)
    recs = _xp_changes(id(wiz))
    assert len(recs) == 2
    assert recs[1]["payload"]["delta"] == 3


def test_xp_spend_captured():
    # Upgrade-buy shape (Game.py:628). Capture is uniform; voice-ignores-spends
    # is a composer ruling, not a capture filter.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.xp = 10
    wiz.xp -= 4
    recs = _xp_changes(id(wiz))
    assert len(recs) == 2
    p = recs[1]["payload"]
    assert p["delta"] == -4 and p["xp_after"] == 6


def test_xp_prelive_write_dropped():
    # Game.py:498 (`player.xp = 1` at game creation) fires before the player is
    # in any level's units list -> gated, no phantom record.
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)   # constructed, NOT placed
    wiz.xp = 1
    assert _xp_changes(id(wiz)) == []


def test_xp_write_yields_no_shield_records():
    # Gate finding: _classify_watched's old fallback-else WAS the shields branch;
    # a mis-wired xp branch would voice Memory Orb pickups as shield gains. Pin
    # the negative alongside the positive.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.xp = 7
    assert len(_xp_changes(id(wiz))) == 1
    assert _shield_records(id(wiz)) == []


# ---- G-M (Unit 4): EventOnAwakened typed capture, parity with unfrozen ----

def _awakened_records(target_id=None):
    out = [r for r in journal.records if r["event_type"] == "EventOnAwakened"]
    if target_id is not None:
        out = [r for r in out
               if (r["payload"].get("target") or {}).get("id") == target_id]
    return out


def test_awakened_damage_wake_typed_record():
    # Non-Arcane/Dark damage wakes the sleeper (SleepBuff.on_damage,
    # CommonContent.py:850-852) -> remove_buff -> on_unapplied raises Awakened.
    from CommonContent import SleepBuff
    import Level as L
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre", hp=20), 5, 5)
    ogre.apply_buff(SleepBuff(), 5)
    lvl.deal_damage(5, 5, 3, L.Tags.Physical, _src("Jab"))   # non-lethal wake
    recs = _awakened_records(id(ogre))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["target"]["name"] == "Ogre"      # typed snapshot, not the generic
    assert p["buff_name"] == "Sleep"          # fallback's field-iteration shape
    assert ogre.is_alive()


def test_awakened_expiry_wake_typed_record():
    # Natural expiry: the buff's duration runs out -> unapply -> raise.
    from CommonContent import SleepBuff
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre", hp=20), 5, 5)
    ogre.apply_buff(SleepBuff(), 1)
    for _ in range(3):                        # enough ticks to expire a 1-turn buff
        ogre.advance_buffs()
    recs = _awakened_records(id(ogre))
    assert len(recs) == 1
    assert recs[0]["payload"]["buff_name"] == "Sleep"


def test_awakened_fires_when_sleeper_dies_without_waking():
    # The third wake leg (gate finding): Arcane damage does NOT wake, so a
    # lethal Arcane hit kills the sleeper; remove_obj then unapplies SleepBuff
    # and EventOnAwakened fires AFTER the death record. Capture must record it
    # (the composer-phase wake-line death-dedup decides not to SPEAK it).
    from CommonContent import SleepBuff
    import Level as L
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre", hp=5), 5, 5)
    ogre.apply_buff(SleepBuff(), 5)
    lvl.deal_damage(5, 5, 10, L.Tags.Arcane, _src("Void"))   # lethal, no wake
    recs = _awakened_records(id(ogre))
    assert len(recs) == 1
    deaths = [r for r in journal.records if r["event_type"] == "EventOnDeath"
              and (r["payload"].get("target") or {}).get("id") == id(ogre)]
    assert len(deaths) == 1
    assert recs[0]["sequence"] > deaths[0]["sequence"], \
        "death-leg wake must land after the death record (composer dedup order)"


def test_awakened_not_raised_on_arcane_nonlethal_hit():
    # Negative case: Arcane/Dark damage does not end sleep (CommonContent.py:851).
    from CommonContent import SleepBuff
    import Level as L
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre", hp=20), 5, 5)
    ogre.apply_buff(SleepBuff(), 5)
    lvl.deal_damage(5, 5, 3, L.Tags.Arcane, _src("Void"))    # non-lethal, no wake
    assert _awakened_records(id(ogre)) == []
    assert ogre.is_alive()


# ---- G-G (Unit 4): silent cur_hp DECREASES — the raw non-evented drops ----

def test_floor_wide_halving_one_hp_loss_per_unit():
    # Word-of-Undeath shape (Spells.py:5182-5183): `//= 2` then max(1,...) per
    # unit. Above 1 HP the clamp write is a no-op (equal value -> no record),
    # so each unit yields exactly one hp_loss.
    lvl = _fresh_level()
    a = _place(lvl, _unit("Ogre", hp=20), 3, 3)
    b = _place(lvl, _unit("Ghoul", hp=20), 5, 5)
    a.cur_hp = 20
    b.cur_hp = 9
    seq = journal.sequence
    for u in (a, b):
        u.cur_hp //= 2
        u.cur_hp = max(1, u.cur_hp)
    la = [r for r in _hp_losses(id(a)) if r["sequence"] > seq]
    lb = [r for r in _hp_losses(id(b)) if r["sequence"] > seq]
    assert len(la) == 1 and la[0]["payload"]["loss_amount"] == 10
    assert len(lb) == 1 and lb[0]["payload"]["loss_amount"] == 5   # 9 -> 4
    heals = [r for r in journal.records if r["sequence"] > seq
             and r["event_type"] == "silent_heal"]
    assert heals == []


def test_write_then_clamp_pair_records_both_same_parent():
    # D5: WoU on a 1-HP unit — 1->0 (hp_loss) then max(1,0) 0->1 (silent_heal).
    # BOTH record, same parent: per-write truth of an engine idiom; pairing/
    # collapse is the composer phase's job. This pins that capture never
    # net-cancels the pair.
    lvl = _fresh_level()
    runt = _place(lvl, _unit("Runt", hp=20), 5, 5)
    runt.cur_hp = 1
    seq = journal.sequence
    runt.cur_hp //= 2                  # 1 -> 0
    runt.cur_hp = max(1, runt.cur_hp)  # 0 -> 1
    losses = [r for r in _hp_losses(id(runt)) if r["sequence"] > seq]
    heals = [r for r in _silent_heals(id(runt)) if r["sequence"] > seq]
    assert len(losses) == 1 and losses[0]["payload"]["cur_hp_after"] == 0
    assert len(heals) == 1 and heals[0]["payload"]["cur_hp_after"] == 1
    assert losses[0]["parent"] == heals[0]["parent"]
    assert runt.is_alive()             # no death at this site (gate correction)


def test_write_to_zero_then_kill_ordering():
    # Orb-of-Anima shape (Spells.py:16012-16014): a raw write to EXACTLY 0,
    # then an explicit kill(). The hp_loss must precede the death record —
    # composer mass-aggregation will assume this order.
    lvl = _fresh_level()
    orb = _place(lvl, _unit("Orb", hp=20), 5, 5)
    orb.cur_hp = 3
    seq = journal.sequence
    orb.cur_hp -= 3                    # exactly 0, still live at the write
    orb.kill()
    losses = [r for r in _hp_losses(id(orb)) if r["sequence"] > seq]
    assert len(losses) == 1
    assert losses[0]["payload"]["cur_hp_after"] == 0
    deaths = [r for r in journal.records if r["sequence"] > seq
              and r["event_type"] == "EventOnDeath"
              and (r["payload"].get("target") or {}).get("id") == id(orb)]
    assert len(deaths) == 1
    assert losses[0]["sequence"] < deaths[0]["sequence"]


def test_boss_steal_shape_wizard_target():
    # FinalBosses.py:604-606 targets the WIZARD (the D2 overlap case's silent
    # half): stolen = cur_hp*3//10.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.cur_hp = 5
    seq = journal.sequence
    wiz.cur_hp -= (wiz.cur_hp * 3) // 10          # steal 1
    losses = [r for r in _hp_losses(id(wiz)) if r["sequence"] > seq]
    assert len(losses) == 1
    assert losses[0]["payload"]["loss_amount"] == 1


def test_clamp_after_max_hp_drain_pairs_with_max_hp_change():
    # The gate-found clamp family (CommonContent.py:1149 drain_max_hp shape):
    # max_hp drops, then cur_hp = min(cur_hp, max_hp). The overflow above the
    # new cap is the hp_loss; it arrives beside the max_hp_change same tick.
    lvl = _fresh_level()
    u = _place(lvl, _unit("Husk", hp=30), 5, 5)
    u.cur_hp = 30
    seq = journal.sequence
    u.max_hp -= 10                                # 30 -> 20 (drained)
    u.cur_hp = min(u.cur_hp, u.max_hp)            # 30 -> 20 (the overflow loss)
    maxes = [r for r in _max_hp_changes(id(u)) if r["sequence"] > seq]
    losses = [r for r in _hp_losses(id(u)) if r["sequence"] > seq]
    assert len(maxes) == 1 and maxes[0]["payload"]["delta"] == -10
    assert len(losses) == 1 and losses[0]["payload"]["loss_amount"] == 10
    assert losses[0]["parent"] == maxes[0]["parent"]


def test_necrosis_shape_clamp_parented_under_buff_tick():
    # Necrosis on_advance shape (Level.py:1681-1682): the per-turn clamp fires
    # inside buff.advance, so the hp_loss parents under the buff_tick cause.
    lvl = _fresh_level()
    u = _place(lvl, _unit("Rotting", hp=30), 5, 5)
    u.cur_hp = 30

    class _ClampDrain(Level.Buff):
        def on_advance(self):
            self.owner.max_hp -= 5
            self.owner.cur_hp = min(self.owner.cur_hp, self.owner.max_hp)

    u.apply_buff(_ClampDrain())
    seq = journal.sequence
    u.advance_buffs()
    losses = [r for r in _hp_losses(id(u)) if r["sequence"] > seq]
    assert len(losses) == 1 and losses[0]["payload"]["loss_amount"] == 5
    ticks = [r for r in journal.records if r["sequence"] > seq
             and r["event_type"] == "buff_tick"]
    assert len(ticks) == 1
    assert losses[0]["parent"] == ticks[0]["sequence"]


def test_unbracket_handler_decrease_inside_deal_damage():
    # Death-Tax shape (gate finding): a handler decreasing a BYSTANDER's cur_hp
    # during an event raised inside deal_damage runs under the raise_event
    # un-bracket -> hp_loss emitted, parented under the triggering event. The
    # victim's own evented decrease stays bracketed out.
    import Level as L
    lvl = _fresh_level()
    victim = _place(lvl, _unit("Victim", hp=20), 5, 5)
    bystander = _place(lvl, _unit("Bystander", hp=20), 3, 3)
    victim.cur_hp = 20
    bystander.cur_hp = 20

    def on_damaged(evt):
        if evt.unit is victim:
            bystander.cur_hp -= 4

    lvl.event_manager.register_global_trigger(Level.EventOnDamaged, on_damaged)
    seq = journal.sequence
    lvl.deal_damage(5, 5, 6, L.Tags.Physical, _src("Blow"))
    by_losses = [r for r in _hp_losses(id(bystander)) if r["sequence"] > seq]
    assert len(by_losses) == 1
    assert by_losses[0]["payload"]["loss_amount"] == 4
    dmg = [r for r in journal.records if r["sequence"] > seq
           and r["event_type"] == "EventOnDamaged"
           and (r["payload"].get("target") or {}).get("id") == id(victim)]
    assert len(dmg) == 1
    assert by_losses[0]["parent"] == dmg[0]["sequence"]
    assert [r for r in _hp_losses(id(victim)) if r["sequence"] > seq] == [], \
        "deal_damage's own decrease must stay bracketed out"


def test_direct_kill_writes_no_hp_loss():
    # kill() writes cur_hp = 0 (Level.py:2601) AFTER remove_obj pops the unit
    # (:2600) — load-bearing ordering that keeps ~60 direct-kill sites from
    # emitting a full-remaining-HP hp_loss. Regression-pin it, don't rely on
    # the accident silently.
    lvl = _fresh_level()
    goner = _place(lvl, _unit("Goner", hp=20), 5, 5)
    goner.cur_hp = 20
    seq = journal.sequence
    goner.kill()
    assert [r for r in _hp_losses(id(goner)) if r["sequence"] > seq] == []


# ---- G-G step 4 (Unit 4): EventOnSpendHP supersedes its own hp_loss (D2) ----

def _spend(lvl, unit, amount):
    """A spend site's PRODUCTION shape (Level.py:917->920): raw write, then
    the PAY_HP log line, then the raise — one synchronous body. The log write
    puts a game_log record BETWEEN the hp_loss and the spend record, so every
    test through this helper also exercises the adjacency matcher's
    game_log transparency (the live shape once the oracle wrap installed)."""
    unit.cur_hp -= amount
    lvl.log(("{unit} pays {cost} HP to cast {spell}",
             {"unit": unit.name, "cost": amount, "spell": "Test Spell"}))
    lvl.event_manager.raise_event(Level.EventOnSpendHP(unit, amount), unit)


def test_spend_supersedes_own_hp_loss():
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.cur_hp = 20
    seq = journal.sequence
    _spend(lvl, wiz, 3)
    losses = [r for r in _hp_losses(id(wiz)) if r["sequence"] > seq]
    assert len(losses) == 1, "capture stays uniform — the loss record EXISTS"
    assert "superseded_by_spend" in losses[0]["marks"]
    spends = [r for r in journal.records if r["sequence"] > seq
              and r["event_type"] == "EventOnSpendHP"]
    assert len(spends) == 1


def test_two_identical_spends_each_claim_own_loss():
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.cur_hp = 20
    seq = journal.sequence
    _spend(lvl, wiz, 1)
    _spend(lvl, wiz, 1)
    losses = [r for r in _hp_losses(id(wiz)) if r["sequence"] > seq]
    assert len(losses) == 2
    assert all("superseded_by_spend" in r["marks"] for r in losses)


def test_coincident_silent_loss_not_claimed_by_spend():
    # The D2 overlap case: a boss-steal-shaped SILENT loss (same unit, same
    # amount) earlier in the window, then a real spend. The spend marks its
    # OWN loss (adjacency); the silent one stays unmarked for composition.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.cur_hp = 20
    seq = journal.sequence
    wiz.cur_hp -= 1                       # silent (boss-steal shape)
    _spend(lvl, wiz, 1)                   # write + raise
    losses = [r for r in _hp_losses(id(wiz)) if r["sequence"] > seq]
    assert len(losses) == 2
    silent, owned = losses[0], losses[1]  # sequence order
    assert "superseded_by_spend" not in silent["marks"]
    assert "superseded_by_spend" in owned["marks"]


def test_spend_with_no_coincident_loss_no_false_mark():
    # A spend event whose paired write was dropped (off-field unit) must not
    # crash and must not claim a bystander's loss — pins the unit/target
    # payload-key asymmetry too (spend carries 'unit', hp_loss carries
    # 'target'), since the only candidate record belongs to another unit.
    lvl = _fresh_level()
    ghost = _unit("Ghost", hp=20)               # never placed -> off-field
    other = _place(lvl, _unit("Other", hp=20), 3, 3)
    other.cur_hp = 20
    seq = journal.sequence
    other.cur_hp -= 2                           # bystander's silent loss
    ghost.cur_hp = 18                           # off-field write -> dropped
    lvl.event_manager.raise_event(Level.EventOnSpendHP(ghost, 2), ghost)
    losses = [r for r in _hp_losses(id(other)) if r["sequence"] > seq]
    assert len(losses) == 1
    assert "superseded_by_spend" not in losses[0]["marks"]
    assert [r for r in _hp_losses(id(ghost)) if r["sequence"] > seq] == []


def test_spend_supersede_survives_interleaved_game_log_record():
    # The Unit-4 pre-declaration made live: with the oracle wrap installed, a
    # game_log record physically sits between the hp_loss and the spend record
    # (write -> log -> raise). Adjacency must look through it.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.cur_hp = 20
    seq = journal.sequence
    _spend(lvl, wiz, 3)
    window = [r for r in journal.records if r["sequence"] > seq]
    kinds = [r["event_type"] for r in window]
    assert kinds == ["hp_loss", "game_log", "EventOnSpendHP"], (
        "the live interleave shape itself is the fixture premise: %r" % kinds)
    assert "superseded_by_spend" in window[0]["marks"]


def test_spend_supersede_through_real_pay_costs():
    # End-to-end through the game's own Spell.pay_costs (Level.py:912-921):
    # charge/cooldown bookkeeping, the real PAY_HP log line, the real raise.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.cur_hp = 20
    spell = Level.Spell()
    spell.name = "Blood Test"
    spell.hp_cost = 5
    spell.caster = wiz
    spell.owner = wiz
    seq = journal.sequence
    spell.pay_costs()
    assert wiz.cur_hp == 15
    losses = [r for r in _hp_losses(id(wiz)) if r["sequence"] > seq]
    assert len(losses) == 1
    assert "superseded_by_spend" in losses[0]["marks"]
    logs = [r for r in journal.records if r["sequence"] > seq
            and r["event_type"] == "game_log"]
    assert len(logs) == 1 and "pays" in logs[0]["payload"]["template"]


def test_spend_amount_mismatch_not_marked():
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    wiz.cur_hp = 20
    seq = journal.sequence
    wiz.cur_hp -= 5
    lvl.event_manager.raise_event(Level.EventOnSpendHP(wiz, 3), wiz)
    losses = [r for r in _hp_losses(id(wiz)) if r["sequence"] > seq]
    assert len(losses) == 1
    assert "superseded_by_spend" not in losses[0]["marks"]


# ---- Invariant tripwire: deal_damage is the SOLE evented HP-change path ----

def test_deal_damage_is_sole_evented_hp_path():
    # The universal cur_hp capture is DEFINE-BY-EXCLUSION: "silent" = every cur_hp
    # write except those deal_damage's bracket suppresses. That is sound ONLY
    # while deal_damage remains the sole raiser of EventOnHealed/EventOnDamaged.
    # If a future RW3 update adds a second evented HP path, its cur_hp write would
    # escape the bracket and DOUBLE-voice (evented + silent). This canary fails
    # loudly on that drift so the scheme's assumption is checked, not assumed.
    import Level as L
    src = os.path.join(os.path.dirname(L.__file__), 'Level.py')
    with open(src, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Level.deal_damage — NOT Unit.deal_damage(self, amount, ...); match by its
    # x,y signature so we get the right one of the two same-named methods.
    dd_start = next(i for i, ln in enumerate(lines)
                    if ln.startswith('\tdef deal_damage(self, x, y'))
    dd_end = next((i for i in range(dd_start + 1, len(lines))
                   if lines[i].startswith('\tdef ')), len(lines))
    # Constructions of the events (namedtuple defs use `= namedtuple`, so the
    # `EventName(` pattern matches only construction sites).
    for ev in ('EventOnHealed(', 'EventOnDamaged('):
        sites = [i for i, ln in enumerate(lines) if ev in ln]
        assert len(sites) == 1, (
            "%s constructed at %d site(s); expected exactly 1 (inside deal_damage). "
            "A new evented HP path breaks the universal-capture bracket." % (ev, len(sites)))
        assert dd_start < sites[0] < dd_end, (
            "%s no longer constructed inside deal_damage (%d not in %d..%d)."
            % (ev, sites[0], dd_start, dd_end))


# ---- Oracle step 1: the Level.log wrap and its record shape (O1/O4) ----

def _game_logs():
    return [r for r in journal.records if r["event_type"] == "game_log"]


def test_log_capture_record_shape_tuple_entry():
    lvl = _fresh_level()
    lvl.turn_no = 3
    lvl.log(("{unit} tests {thing}", {"unit": "Ogre", "thing": "capture"}))
    recs = _game_logs()
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["template"] == "{unit} tests {thing}"
    assert p["values"] == {"unit": "Ogre", "thing": "capture"}
    assert p["resolved"] == "Ogre tests capture"
    assert p["turn"] == 3
    # The game's own write landed too, in the same bucket.
    assert lvl.combat_log[3][-1] == "Ogre tests capture"


def test_log_capture_bare_string_entry():
    # Mutators.py:122/155/184 and Level.py:2138/2154 pass bare strings.
    lvl = _fresh_level()
    lvl.log("Object removed from level due to mutator.")
    recs = _game_logs()
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["template"] == "Object removed from level due to mutator."
    assert p["values"] == {}
    assert p["resolved"] == "Object removed from level due to mutator."


def test_log_capture_setup_writes_bucket_to_turn_one():
    # turn_no is 0 during setup/deploy; the game folds those into bucket 1
    # (max(1, turn_no)) and the record mirrors that.
    lvl = _fresh_level()
    assert lvl.turn_no == 0
    lvl.log("setup line")
    assert _game_logs()[0]["payload"]["turn"] == 1


def test_log_capture_coerces_non_primitive_values():
    # Nested markup tuples (color_entity) and arbitrary objects coerce to str;
    # no live references may reach the journal.
    lvl = _fresh_level()
    marker = object()
    lvl.log(("{who} does {what}",
             {"who": ("[{name}:{color}]", {"name": "Ogre", "color": "enemy"}),
              "what": marker}))
    values = _game_logs()[0]["payload"]["values"]
    assert isinstance(values["who"], str)
    assert isinstance(values["what"], str)


def test_log_capture_non_ascii_roundtrips_jsonl(tmp_path):
    # A localized resolved string must survive journal.record + JSONL emission.
    lvl = _fresh_level()
    path = tmp_path / "journal_test.jsonl"
    journal.open_log(str(path))
    try:
        lvl.log(("{unit} frappé", {"unit": "Ogre"}))
    finally:
        journal.close_log()
    recs = _game_logs()
    assert recs and "frappé" in recs[0]["payload"]["resolved"]
    import json
    lines = [json.loads(ln) for ln in
             path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    emitted = [d for d in lines if d.get("event_type") == "game_log"]
    assert emitted and "frappé" in emitted[0]["payload"]["resolved"]


def test_log_capture_ordering_invariant_capture_failure_never_blocks_game():
    # THE safety property: the game's write lands even when the ENTIRE capture
    # path throws. Original-first, zero mod code before it.
    lvl = _fresh_level()
    real_capture = log_capture._capture

    def exploding_capture(level, entry):
        raise RuntimeError("boom")

    log_capture._capture = exploding_capture
    try:
        lvl.log(("{unit} survives", {"unit": "Ogre"}))
    finally:
        log_capture._capture = real_capture
    assert lvl.combat_log[1][-1] == "Ogre survives"   # game write landed
    assert _game_logs() == []                          # record lost, nothing else


def test_log_capture_failure_note_deduped_per_template():
    lvl = _fresh_level()
    real_capture = log_capture._capture
    notes = []
    real_log_fn = log_capture._log_fn
    log_capture._log_fn = notes.append
    log_capture._failed_templates.clear()

    def exploding_capture(level, entry):
        raise RuntimeError("boom")

    log_capture._capture = exploding_capture
    try:
        lvl.log(("dup {n}", {"n": 1}))
        lvl.log(("dup {n}", {"n": 2}))
        lvl.log("other line")
    finally:
        log_capture._capture = real_capture
        log_capture._log_fn = real_log_fn
        log_capture._failed_templates.clear()
    assert len([n for n in notes if "dup {n}" in n]) == 1
    assert len([n for n in notes if "other line" in n]) == 1


def test_log_capture_reentrant_log_call_guarded():
    # No capture path reaches Level.log today (resolve_text verified log-free);
    # pin the guard anyway: a reentrant call must neither recurse nor lose the
    # game's writes.
    lvl = _fresh_level()
    real_capture = log_capture._capture

    def reentrant_capture(level, entry):
        template, _ = log_capture._entry_parts(entry)
        if template == "outer":
            level.log("inner")   # re-enters the wrap; guard skips its capture
        real_capture(level, entry)

    log_capture._capture = reentrant_capture
    try:
        lvl.log("outer")
    finally:
        log_capture._capture = real_capture
    assert "outer" in lvl.combat_log[1]
    assert "inner" in lvl.combat_log[1]                # both game writes landed
    templates = [r["payload"]["template"] for r in _game_logs()]
    assert templates == ["outer"]                      # inner capture skipped


def test_log_capture_double_install_noops():
    _fresh_level()
    wrapped = Level.Level.log
    assert log_capture.install() is True
    assert Level.Level.log is wrapped                  # not re-wrapped


def test_oracle_sweep_clean_on_real_spend_and_damage():
    # Integration: the REAL row table against REAL records from real game
    # paths. A production-shaped spend (PAY_HP line + EventOnSpendHP) and a
    # deal_damage hit (DMG line + EventOnDamaged) must sweep clean — no
    # parity failures, no unknown templates.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", hp=50, player=True), 7, 7)
    _place(lvl, _unit("Ogre", hp=20), 5, 5)
    wiz.cur_hp = 30
    _spend(lvl, wiz, 3)
    lvl.deal_damage(5, 5, 4, Level.Tags.Fire, _src())

    class _Tel:
        def __init__(self):
            self.calls = []

        def is_enabled(self):
            return True

        def emit(self, ev, **fields):
            self.calls.append((ev, fields))

    tel = _Tel()
    c = log_capture.ParityChecker()
    c.sweep(journal.records, tel)
    c.sweep(journal.records, tel)   # flush the carry-over window too
    assert tel.calls == [], tel.calls


def test_expect_row_kinds_are_journal_producible():
    # Drift guard: the row table names record kinds; the journal is what
    # produces them. Event-class kinds must exist as game event classes
    # (recorded via type(event).__name__); synthetic kinds must appear as
    # literals in journal.py. A renamed kind fails here, not silently in
    # the field.
    import CommonContent
    with open(os.path.join(mod_dir, 'journal.py'), encoding='utf-8') as f:
        journal_src = f.read()
    for tpl, row in log_capture.rows().items():
        for kind in row['kinds']:
            if kind.startswith('EventOn'):
                assert hasattr(Level, kind) or hasattr(CommonContent, kind), (
                    "row %r names unknown event class %r" % (tpl, kind))
            else:
                assert ("'%s'" % kind) in journal_src or \
                       ('"%s"' % kind) in journal_src, (
                    "row %r names synthetic kind %r not found in journal.py"
                    % (tpl, kind))


def test_log_capture_install_declines_on_missing_sink():
    # Simulate the RW2 backport / a future sink restructure. Level.Level.log
    # is shared process state — save/restore in try/finally, and restore the
    # module's installed flag so later tests keep the live wrap.
    _fresh_level()
    saved_log = Level.Level.log
    saved_flag = log_capture._installed
    try:
        del Level.Level.log
        log_capture._installed = False
        assert log_capture.install() is False
        assert not hasattr(Level.Level, 'log')         # left unwrapped
    finally:
        Level.Level.log = saved_log
        log_capture._installed = saved_flag


# ---- Unit 1 step 2: Root-1 container-diff — synchronous boundary brackets ----

def _container_records(kind=None, target_id=None):
    kinds = set(container_diff.ALL_KINDS) if kind is None else {kind}
    out = [r for r in journal.records if r["event_type"] in kinds]
    if target_id is not None:
        out = [r for r in out
               if (r["payload"].get("unit") or {}).get("id") == target_id]
    return out


def _fire_ward(amount=50):
    b = Level.Buff()
    b.name = "Fire Ward"
    b.resists[Level.Tags.Fire] = amount
    return b


def test_buff_apply_resist_fold_recorded_with_bracket():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ogre.apply_buff(_fire_ward())
    recs = _container_records('resists_change', id(ogre))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["changes"] == {'Fire': (0, 50)}
    assert p["bracket"] == 'buff_apply'
    assert p["detail"] == {'buff': 'Fire Ward'}


def test_buff_unapply_unfold_recorded_with_bracket():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ward = _fire_ward()
    ogre.apply_buff(ward)
    ogre.remove_buff(ward)
    recs = _container_records('resists_change', id(ogre))
    assert len(recs) == 2
    p = recs[1]["payload"]
    assert p["changes"] == {'Fire': (50, 0)}
    assert p["bracket"] == 'buff_unapply'


class _TagRobe(Level.Equipment):
    # The equip/unequip TAIL shape (SpiderCarapaceRobe/NecromancersLocket,
    # Equipment.py:860-865/924-928): container writes in on_equip/on_unequip,
    # which run OUTSIDE apply_buff/remove_buff (Level.py:2052/2060).
    def on_equip(self, unit):
        unit.tags.append(Level.Tags.Arcane)

    def on_unequip(self, unit):
        if Level.Tags.Arcane in unit.tags:
            unit.tags.remove(Level.Tags.Arcane)


def test_equip_tail_tag_write_attributed_to_equip_not_buff_bracket():
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", player=True), 2, 2)
    robe = _TagRobe()
    robe.name = "Test Robe"
    wiz.equip(robe)
    recs = _container_records('tags_change', id(wiz))
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["changes"] == {'added': ['Arcane'], 'removed': []}
    # The append happens in the on_equip tail, after Buff.apply's exit sweep
    # already ran — so the delta belongs to the equip bracket, not the
    # nested buff_apply bracket.
    assert p["bracket"] == 'equip'
    assert p["detail"] == {'item': 'Test Robe'}


def test_unequip_tail_tag_removal_attributed_to_unequip_bracket():
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", player=True), 2, 2)
    robe = _TagRobe()
    robe.name = "Test Robe"
    wiz.equip(robe)
    wiz.unequip(robe)
    recs = _container_records('tags_change', id(wiz))
    assert len(recs) == 2
    p = recs[1]["payload"]
    assert p["changes"] == {'added': [], 'removed': ['Arcane']}
    assert p["bracket"] == 'unequip'


def test_death_cleanup_unapply_swept_before_snapshot_drop():
    # remove_obj unapplies buffs directly (bypassing remove_buff); the
    # un-fold must be captured before the unit's snapshot is dropped.
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ogre.apply_buff(_fire_ward())
    lvl.remove_obj(ogre)
    recs = _container_records('resists_change', id(ogre))
    assert len(recs) == 2
    assert recs[1]["payload"]["changes"] == {'Fire': (50, 0)}
    assert recs[1]["payload"]["bracket"] == 'buff_unapply'
    assert id(ogre) not in container_diff.store._units


def test_eventless_direct_write_caught_at_next_boundary_unattributed():
    # The D1 counterexample shape: essence-Reincarnation re-cast writes
    # resists+tags on a target with ZERO events. Outside any bracket, the
    # drift surfaces at the next boundary's entry sweep — recorded honestly
    # with no mechanism tag and no claimed cause, never pinned on the
    # adjacent bracket.
    lvl = _fresh_level()
    ally = _place(lvl, _unit("Ghost"), 4, 4)
    other = _place(lvl, _unit("Ogre"), 6, 6)
    ally.apply_buff(_fire_ward())          # boundary: baselines both units
    ally.resists[Level.Tags.Dark] += 100   # eventless direct writes...
    ally.tags.append(Level.Tags.Undead)    # ...outside every bracket
    other.apply_buff(_fire_ward())         # next boundary's ENTRY sweep
    resist_recs = _container_records('resists_change', id(ally))
    drift = [r for r in resist_recs if r["payload"]["changes"] == {'Dark': (0, 100)}]
    assert len(drift) == 1
    assert drift[0]["payload"]["bracket"] is None
    assert drift[0]["payload"]["unattributed"] is True
    tag_recs = _container_records('tags_change', id(ally))
    assert len(tag_recs) == 1
    assert tag_recs[0]["payload"]["changes"] == {'added': ['Undead'], 'removed': []}
    assert tag_recs[0]["payload"]["bracket"] is None


def test_reseed_rebaselines_without_flood():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ogre.apply_buff(_fire_ward())          # baseline + fold record
    container_diff.reseed()                # level boundary
    ogre.resists[Level.Tags.Ice] += 75     # changed while unobserved
    seq_before = journal.sequence
    _place(lvl, _unit("Newt"), 7, 7).apply_buff(_fire_ward())  # boundary
    ogre_recs = [r for r in _container_records('resists_change', id(ogre))
                 if r["sequence"] > seq_before]
    assert ogre_recs == []                 # baseline, not change


def test_container_diff_install_declines_on_missing_shapes():
    # RW2 backport / future restructure: decline cleanly, wrap nothing.
    # Level.Buff.apply is shared process state — save/restore in try/finally.
    _fresh_level()
    saved_apply = Level.Buff.apply
    saved_flag = container_diff._installed
    try:
        del Level.Buff.apply
        container_diff._installed = False
        assert container_diff.install() is False
        assert not hasattr(Level.Buff, 'apply')      # left unwrapped
    finally:
        Level.Buff.apply = saved_apply
        container_diff._installed = saved_flag


def test_container_diff_double_install_noops():
    _fresh_level()
    before = Level.Buff.apply
    assert container_diff.install() is True
    assert Level.Buff.apply is before


# ---- Unit 1 step 3: tick / cast / queued-gen span sweeps + turn boundary ----

class _IceAuraBuff(Level.Buff):
    # A tick that writes its owner's containers directly in on_advance.
    def on_init(self):
        self.name = "Ice Aura"

    def on_advance(self):
        self.owner.resists[Level.Tags.Ice] += 25


def test_tick_container_write_parented_to_buff_tick():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ogre.apply_buff(_IceAuraBuff())
    seq = journal.sequence
    ogre.advance_buffs()
    ticks = [r for r in journal.records
             if r["event_type"] == "buff_tick" and r["sequence"] > seq]
    assert len(ticks) == 1
    recs = [r for r in _container_records('resists_change', id(ogre))
            if r["sequence"] > seq]
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["changes"] == {'Ice': (0, 25)}
    assert p["bracket"] == 'buff_tick'
    assert p["unattributed"] is False
    assert recs[0]["parent"] == ticks[0]["sequence"]


class _AssimBuff(Level.Buff):
    # The DamageAssimilation shape (Spells.py:15744): the VICTIM's resists
    # written from inside a damage-event handler.
    def on_init(self):
        self.name = "Assimilation"
        self.owner_triggers[Level.EventOnDamaged] = self.on_damaged

    def on_damaged(self, evt):
        self.owner.resists[Level.Tags.Fire] += evt.damage


class _StingAuraBuff(Level.Buff):
    def __init__(self, victim):
        self._victim = victim
        Level.Buff.__init__(self)

    def on_init(self):
        self.name = "Sting Aura"

    def on_advance(self):
        self.owner.level.deal_damage(self._victim.x, self._victim.y, 5,
                                     Level.Tags.Fire, _src("Sting"))


def test_cross_unit_reactive_write_during_tick_attributed_to_tick():
    # Full-sweep proof at a real boundary: the changed unit is NOT the
    # bracket owner. Granularity per D2: the write attributes to the
    # enclosing ACTION (the tick), not the event raise.
    lvl = _fresh_level()
    ghost = _place(lvl, _unit("Ghost", hp=30), 4, 4)
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ghost.apply_buff(_AssimBuff())
    ogre.apply_buff(_StingAuraBuff(ghost))
    seq = journal.sequence
    ogre.advance_buffs()
    ticks = [r for r in journal.records
             if r["event_type"] == "buff_tick" and r["sequence"] > seq]
    assert len(ticks) == 1
    recs = [r for r in _container_records('resists_change', id(ghost))
            if r["sequence"] > seq]
    assert len(recs) == 1
    assert recs[0]["payload"]["changes"] == {'Fire': (0, 5)}
    assert recs[0]["parent"] == ticks[0]["sequence"]


class _BareSpell(Level.Spell):
    def on_init(self):
        self.name = "Bare"
        self.range = 20

    def cast(self, x, y):
        yield


def test_reaction_write_during_execute_cast_attributed_to_cast_begin():
    # A container write during the synchronous EventOnSpellCast raise
    # (Level.py:3140, inside execute_cast) lands in the cast_begin span.
    lvl = _fresh_level()
    wiz = _place(lvl, _unit("Wizard", player=True), 2, 2)

    def on_cast(evt):
        evt.caster.resists[Level.Tags.Arcane] += 10

    lvl.event_manager.register_global_trigger(Level.EventOnSpellCast, on_cast)
    spell = _BareSpell()
    spell.caster = wiz
    spell.owner = wiz
    seq = journal.sequence
    lvl.execute_cast(wiz, spell, wiz.x, wiz.y, pay_costs=False, queue=True)
    casts = [r for r in journal.records
             if r["event_type"] == "cast_begin" and r["sequence"] > seq]
    assert len(casts) == 1
    recs = [r for r in _container_records('resists_change', id(wiz))
            if r["sequence"] > seq]
    assert len(recs) == 1
    assert recs[0]["payload"]["changes"] == {'Arcane': (0, 10)}
    assert recs[0]["payload"]["bracket"] == 'cast_begin'
    assert recs[0]["parent"] == casts[0]["sequence"]
    while lvl.can_advance_spells():   # drain the queued no-op gen
        lvl.advance_spells()


def test_direct_queue_gen_write_swept_per_step_with_carried_cause():
    # The ⟨GATE⟩ 4b carrier: a generator queued directly (never through
    # execute_cast) steps with current_cast_context None; its cause rides
    # the mod's _wrap_with_cause proxy, whose span hooks sweep per step.
    lvl = _fresh_level()
    ghost = _place(lvl, _unit("Ghost"), 4, 4)
    cause = journal.record('EventOnSpellCast', {'note': 'reaction root'})
    journal.push(cause)

    def effect():
        ghost.resists[Level.Tags.Dark] += 100
        yield

    lvl.queue_spell(effect())
    journal.pop()
    seq = journal.sequence
    while lvl.can_advance_spells():
        lvl.advance_spells()
    recs = [r for r in _container_records('resists_change', id(ghost))
            if r["sequence"] > seq]
    assert len(recs) == 1
    assert recs[0]["payload"]["changes"] == {'Dark': (0, 100)}
    assert recs[0]["payload"]["bracket"] == 'EventOnSpellCast'
    assert recs[0]["payload"]["unattributed"] is False
    assert recs[0]["parent"] == cause["sequence"]


class _CdKey:
    # Hashable stand-in for a Spell as a cool_downs dict key.
    def __init__(self, name):
        self.name = name


def test_turn_boundary_absorbs_routine_cooldown_tick():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    gaze = _CdKey("Gaze")
    ogre.cool_downs[gaze] = 3
    container_diff.turn_boundary(lvl)          # baseline
    ogre.cool_downs = {gaze: 2}                # the engine's per-turn rebind
    seq = journal.sequence
    container_diff.turn_boundary(lvl)
    assert [r for r in _container_records('cooldown_change', id(ogre))
            if r["sequence"] > seq] == []      # absorbed


def test_turn_boundary_records_cooldown_deviation_unattributed():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    gaze = _CdKey("Gaze")
    ogre.cool_downs[gaze] = 4
    container_diff.turn_boundary(lvl)          # baseline
    ogre.cool_downs = {gaze: 2}                # a halving, not a tick
    seq = journal.sequence
    container_diff.turn_boundary(lvl)
    recs = [r for r in _container_records('cooldown_change', id(ogre))
            if r["sequence"] > seq]
    assert len(recs) == 1
    assert recs[0]["payload"]["changes"] == {'Gaze': (4, 2)}
    assert recs[0]["payload"]["bracket"] is None
    assert recs[0]["payload"]["unattributed"] is True
