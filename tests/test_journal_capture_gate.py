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


def test_raw_cur_hp_loss_not_a_silent_heal():
    # A raw cur_hp DECREASE (e.g. an HP spend) is not a heal — gains only.
    lvl = _fresh_level()
    wiz = _unit("Wizard", hp=50, player=True)
    _place(lvl, wiz, 7, 7)
    wiz.cur_hp = 20                    # from 50 -> 20
    assert _silent_heals(id(wiz)) == []


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
