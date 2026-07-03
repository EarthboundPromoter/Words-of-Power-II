# Unit 5 (world chokepoints) — live-Level tests for terrain / flavor / cloud /
# prop capture and the LoS dirty-flag contract. Real Level.Level harness (the
# test_journal_causegraph pattern — gate Finding 2: the SimpleNamespace style
# cannot run the real primitives). Run from the game root.
#
# Plan: docs/UNIT5_WORLD_CHOKEPOINTS_BUILD_PLAN.md (gated 2026-07-03).

import sys
import os

import pytest

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

import Level
from Level import Point
from journal import journal, install_hooks


# ---- Harness ----

def _fresh_level(w=15, h=15):
    """Install hooks, build a real level, and make it the journal's LIVE level
    via the reset(level=...) set-point (the gate-Finding-1 fix) — NOT via a
    manual journal._level assignment, which papers over the None-window."""
    install_hooks()
    lvl = Level.Level(w, h)
    journal.reset(id(lvl), level=lvl)
    return lvl


def _wizard():
    wiz = Level.Unit()
    wiz.name = "Wizard"
    wiz.is_player_controlled = True
    wiz.max_hp = 50
    return wiz


def _offline(lvl, fn):
    """Run setup mutations with the journal pointed elsewhere (records off),
    then restore the live level."""
    prev = journal._level
    journal._level = None
    try:
        fn()
    finally:
        journal._level = prev


def _records(kind):
    return [r for r in journal.records if r["event_type"] == kind]


def _drain(lvl):
    while lvl.can_advance_spells():
        lvl.advance_spells()


def _make_spell(name, cast_body=None):
    class _S(Level.Spell):
        def on_init(self):
            self.name = name
            self.max_charges = 9
            self.range = 9
        def get_description(self):
            return ""
        def cast(self, x, y, **kw):
            if cast_body is not None:
                yield from cast_body(self, x, y, **kw)
            else:
                return
                yield
    return _S()


# ---- Step 1: terrain primitives + live-level gate ----

def test_make_floor_on_wall_records_terrain_change():
    lvl = _fresh_level()
    _offline(lvl, lambda: lvl.make_wall(3, 3))
    lvl.make_floor(3, 3)
    recs = _records('terrain_change')
    assert len(recs) == 1
    p = recs[0]['payload']
    assert (p['x'], p['y']) == (3, 3)
    assert p['method'] == 'make_floor'
    assert p['before']['kind'] == 'wall'
    assert p['after']['kind'] == 'floor'
    assert p['before']['can_see'] is False
    assert p['after']['can_see'] is True


def test_make_chasm_records_and_flags():
    lvl = _fresh_level()
    _offline(lvl, lambda: lvl.make_floor(4, 4))
    lvl.make_chasm(4, 4)
    recs = _records('terrain_change')
    assert len(recs) == 1
    p = recs[0]['payload']
    assert p['before']['kind'] == 'floor'
    assert p['after']['kind'] == 'chasm'
    assert p['after']['can_walk'] is False
    assert p['after']['can_fly'] is True


def test_noop_make_floor_no_record():
    # The requires_los-reveal idiom re-floors existing floors — no delta.
    lvl = _fresh_level()
    _offline(lvl, lambda: lvl.make_floor(5, 5))
    lvl.make_floor(5, 5)
    assert _records('terrain_change') == []


def test_non_live_level_gated_out():
    # Gen-time carving mutates a level that is not journal._level.
    lvl = _fresh_level()
    other = Level.Level(15, 15)
    other.make_wall(2, 2)
    other.make_floor(2, 2)
    assert _records('terrain_change') == []


def test_none_window_regression():
    # Gate Finding 1: a bare reset leaves _level None (documented old
    # behavior); reset(level=...) — the level-entry set-point — captures a
    # mutation that fires before ANY cast/tick has run (the menu-time
    # portal_mercy shape). No manual journal._level assignment anywhere.
    install_hooks()
    lvl = Level.Level(15, 15)
    lvl.make_wall(6, 6)

    journal.reset(id(lvl))            # bare: the old None-window
    lvl.make_floor(6, 6)
    assert _records('terrain_change') == []

    lvl.make_wall(6, 6)               # still offline (bare reset)
    journal.reset(id(lvl), level=lvl)  # the fix: entry sets the live level
    lvl.make_floor(6, 6)
    recs = _records('terrain_change')
    assert len(recs) == 1
    assert recs[0]['payload']['method'] == 'make_floor'


def test_mass_melt_records_share_cast_parent():
    lvl = _fresh_level()
    wiz = _wizard()

    def melt_gen(spell, x, y, **kw):
        spell.caster.level.make_floor(9, 7)
        spell.caster.level.make_floor(10, 7)
        yield

    _offline(lvl, lambda: (lvl.make_wall(9, 7), lvl.make_wall(10, 7)))
    s = _make_spell("Melt", melt_gen)
    wiz.add_spell(s)
    lvl.add_obj(wiz, 7, 7)
    lvl.act_cast(wiz, s, 8, 7, pay_costs=True)  # open tile; body melts 9/10
    _drain(lvl)

    recs = _records('terrain_change')
    assert len(recs) == 2
    begins = [r for r in journal.records if r["event_type"] == "cast_begin"
              and r["payload"]["spell"]["name"] == "Melt"]
    assert len(begins) == 1
    root_seq = begins[0]["sequence"]
    for r in recs:
        assert r["parent"] == root_seq


def test_terrain_dirty_flag_and_consumer_contract():
    # D5 contract: a can_see transition arms the flag; the consumer fires
    # ONCE at the closed root window (pop-to-empty), not per tile; a
    # chasm->floor change (can_see True->True) never arms it.
    lvl = _fresh_level()
    wiz = _wizard()
    calls = []
    journal._terrain_dirty_consumer = lambda: calls.append(1)
    try:
        def melt_gen(spell, x, y, **kw):
            spell.caster.level.make_floor(9, 7)
            spell.caster.level.make_floor(10, 7)
            yield

        _offline(lvl, lambda: (lvl.make_wall(9, 7), lvl.make_wall(10, 7)))
        s = _make_spell("Melt", melt_gen)
        wiz.add_spell(s)
        lvl.add_obj(wiz, 7, 7)
        lvl.act_cast(wiz, s, 8, 7, pay_costs=True)  # open tile
        _drain(lvl)

        # The cast's OWN gen runs unwrapped (R3) — no pop follows it, so the
        # flag stays ARMED for the turn-boundary floor; the boundary then
        # fires the consumer exactly once for the whole two-tile melt.
        assert journal.terrain_los_dirty is True
        assert calls == []
        journal.consume_terrain_dirty()
        assert calls == [1]
        assert journal.terrain_los_dirty is False
        journal.consume_terrain_dirty()   # idempotent once consumed
        assert calls == [1]

        # A terrain change inside a CLOSED window (event/buff bracket) fires
        # the consumer at pop-to-empty — the mid-turn immediacy path.
        calls.clear()
        _offline(lvl, lambda: lvl.make_wall(12, 7))
        marker = journal.record('cast_begin', {'note': 'window'})
        journal.push(marker)
        lvl.make_floor(12, 7)
        assert calls == []            # window still open
        journal.pop()
        assert calls == [1]
        assert journal.terrain_los_dirty is False

        # chasm->floor keeps can_see True: record yes, flag no.
        calls.clear()
        _offline(lvl, lambda: lvl.make_chasm(11, 7))
        lvl.make_floor(11, 7)
        assert len([r for r in _records('terrain_change')
                    if r['payload']['x'] == 11]) == 1
        assert journal.terrain_los_dirty is False
        assert calls == []
    finally:
        journal._terrain_dirty_consumer = None
        journal.terrain_los_dirty = False


# ---- Step 2: flavor — method hooks + the snapshot sweep ----

import container_diff as cd


def _flavor_records():
    return _records(cd.KIND_TILE_FLAVOR)


def _fresh_flavor_level():
    """Fresh live level with container_diff installed and the flavor
    snapshot BASELINED (first sweep seeds silently)."""
    lvl = _fresh_level()
    cd.install()
    cd.reseed()
    cd.turn_boundary(lvl)             # baseline sweep: seeds, no records
    assert _flavor_records() == []
    return lvl


def test_direct_chasm_write_swept_and_attributed_to_cast():
    # The water->swamp direct-write shape (Spells.py:8275): no chokepoint —
    # only the sweep sees it, attributed via the suspended cast window.
    lvl = _fresh_flavor_level()
    wiz = _wizard()

    def swampify(spell, x, y, **kw):
        spell.caster.level.tiles[9][7].chasm_type = 'swamp'
        yield

    s = _make_spell("Swampify", swampify)
    wiz.add_spell(s)
    lvl.add_obj(wiz, 7, 7)
    lvl.act_cast(wiz, s, 8, 7, pay_costs=True)
    _drain(lvl)
    cd.turn_boundary(lvl)             # closes the suspended cast window

    recs = _flavor_records()
    assert len(recs) == 1
    p = recs[0]['payload']
    assert (p['x'], p['y']) == (9, 7)
    assert p['chasm_type_before'] == 'water'
    assert p['chasm_type_after'] == 'swamp'
    assert p['via'] == 'sweep'
    begins = [r for r in journal.records if r['event_type'] == 'cast_begin'
              and r['payload'].get('spell', {}).get('name') == 'Swampify']
    assert len(begins) == 1
    assert recs[0]['parent'] == begins[0]['sequence']


def test_set_group_tileset_hook_records_changed_tiles_only():
    lvl = _fresh_flavor_level()
    pts = [Point(3, 3), Point(4, 3)]
    lvl.set_group_tileset(pts, 'volcano', 'lava')
    recs = _flavor_records()
    assert len(recs) == 2
    for r in recs:
        assert r['payload']['via'] == 'set_group_tileset'
        assert r['payload']['chasm_type_after'] == 'lava'
        assert r['payload']['tileset_after'] == 'volcano'
    # Re-apply: nothing changed -> nothing recorded.
    lvl.set_group_tileset(pts, 'volcano', 'lava')
    assert len(_flavor_records()) == 2
    # Dedup (D3): the hook advanced the snapshot inline — the next sweep
    # must NOT re-report the same change.
    cd.turn_boundary(lvl)
    assert len(_flavor_records()) == 2


def test_set_tileset_non_live_level_passes_through():
    lvl = _fresh_flavor_level()
    other = Level.Level(15, 15)
    other.set_tileset('volcano', 'lava')
    other.set_group_tileset([Point(2, 2)], 'volcano', 'lava')
    assert _flavor_records() == []
    # And the writes really landed (original ran untouched).
    assert other.tiles[2][2].chasm_type == 'lava'


def test_make_wall_no_phantom_flavor_record():
    # Gate-confirmed: the make_* primitives write zero flavor fields — the
    # sweep must stay silent across a structural change.
    lvl = _fresh_flavor_level()
    lvl.make_wall(5, 5)
    cd.turn_boundary(lvl)
    assert _flavor_records() == []
    assert len(_records('terrain_change')) == 1


def test_flavor_reseed_baselines_silently():
    lvl = _fresh_flavor_level()
    lvl.tiles[6][6].chasm_type = 'swamp'   # unswept direct write...
    cd.reseed()                            # ...dropped by the reseed
    cd.turn_boundary(lvl)                  # re-baseline, not a change
    assert _flavor_records() == []


# ---- Step 3: the Unit-3 handoff pin — reaction markers arrive free ----

def test_golem_reversion_shape_materializes_reaction_marker():
    # GolemReversionBuff (Spells.py:17676): Stonewake's "Return from Dust"
    # calls make_wall inside a buff-owned EventOnDeath handler. Unit 3's
    # record gate promotes the dispatch breadcrumb on ANY record — the new
    # terrain_change must land parented under a materialized reactive_proc
    # marker, itself parented under the death event. Zero Unit-5 coupling;
    # this pins the handoff working end-to-end.
    import reactive_markers
    lvl = _fresh_level()
    reactive_markers.install()

    class _ReversionTB(Level.Buff):
        def on_init(self):
            self.name = "Return from Dust"
            self.buff_type = Level.BUFF_TYPE_BLESS
            self.owner_triggers[Level.EventOnDeath] = self.on_death

        def on_death(self, evt):
            owner = self.owner
            u = owner.level.get_unit_at(owner.x, owner.y)
            if u and u is not owner:
                return
            owner.level.make_wall(owner.x, owner.y)

    golem = Level.Unit()
    golem.name = "Golem"
    golem.max_hp = 10
    lvl.add_obj(golem, 5, 5)
    golem.apply_buff(_ReversionTB())
    seq0 = journal.sequence

    golem.kill()

    terr = [r for r in _records('terrain_change') if r['sequence'] > seq0]
    assert len(terr) == 1
    assert terr[0]['payload']['after']['kind'] == 'wall'
    assert terr[0]['payload']['method'] == 'make_wall'

    markers = [r for r in _records('reactive_proc') if r['sequence'] > seq0]
    assert len(markers) == 1
    assert markers[0]['payload']['buff'] == "Return from Dust"
    assert terr[0]['parent'] == markers[0]['sequence']

    deaths = [r for r in journal.records
              if r['event_type'] == 'EventOnDeath' and r['sequence'] > seq0]
    assert deaths
    assert markers[0]['parent'] == deaths[0]['sequence']


def test_menu_time_mutation_flag_survives_for_boundary_floor():
    # A mutation outside any bracket (menu-time portal_mercy shape): no pop
    # runs, so the flag stays armed for the turn-boundary floor consumer.
    lvl = _fresh_level()
    calls = []
    journal._terrain_dirty_consumer = lambda: calls.append(1)
    try:
        _offline(lvl, lambda: lvl.make_wall(8, 8))
        lvl.make_floor(8, 8)          # no cause window live
        assert journal.terrain_los_dirty is True
        assert calls == []            # nothing popped -> consumer not fired
    finally:
        journal._terrain_dirty_consumer = None
        journal.terrain_los_dirty = False
