# Live-Level tests for the Unit-3 reactive-proc machinery (reactive_markers).
#
# Step 1 coverage — the classify-at-subscription wrap:
#   - buff-owned handlers are wrapped at registration and still fire
#   - non-buff registrants (view-layer / module-function shapes) untouched
#   - unregister translates original -> wrapper: NO ghost handler, and the
#     translation map self-cleans to baseline over a MULTI-handler lifecycle
#   - the LIVE self-unregister-during-dispatch shape (Equipment.py:1123-1126)
#   - load-lifecycle self-heal (fresh manager + re-subscribe re-wraps)
#   - pickle transparency (no wrapper reaches a save; buff trigger dicts
#     never mutated)
#
# Run from the game root: python -m pytest "<mod>/tests/test_reactive_markers.py"

import pickle
import sys
import os

import pytest

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

import Level

import types as _types
sys.modules.setdefault('steamworks', _types.ModuleType('steamworks'))
import Game  # noqa: F401  -- resolves the LevelRewards/Spells/Monsters cycle

from journal import journal, install_hooks
import log_capture
import container_diff
import cause_markers
import reactive_markers


# ---- Test buffs (module top level so pickled levels resolve them) ----

class _ReactiveTB(Level.Buff):
    """Two trigger types: one owner-scoped, one global — the multi-handler
    lifecycle shape the map-baseline pin needs."""
    def on_init(self):
        self.name = "Reactive Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.owner_triggers[Level.EventOnPass] = self.on_pass
        self.global_triggers[Level.EventOnUnitAdded] = self.on_added
        self.pass_count = 0
        self.added_count = 0

    def on_pass(self, evt):
        self.pass_count += 1

    def on_added(self, evt):
        self.added_count += 1


class _SelfRemovingTB(Level.Buff):
    """The Equipment.py:1123-1126 shape: a handler that removes its OWN buff
    mid-dispatch (synchronous unregister inside raise_event)."""
    def on_init(self):
        self.name = "Self Removing Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.owner_triggers[Level.EventOnPass] = self.on_pass
        self.fired = 0

    def on_pass(self, evt):
        self.fired += 1
        self.owner.remove_buff(self)


# ---- Harness (test_journal_capture_gate conventions) ----

def _fresh_level():
    install_hooks()
    log_capture.install()
    container_diff.install()
    container_diff.reseed()
    cause_markers.install()
    reactive_markers.install()
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


def _map_of(mgr):
    return getattr(mgr, reactive_markers._MAP_ATTR, None) or {}


def _registered(mgr, event_type, entity=None):
    evt_map = mgr._handlers.get(event_type) or {}
    return evt_map.get(entity) or ()


# ---- Classification: buff handlers wrapped, behavior preserved ----

def test_buff_handlers_wrapped_and_still_fire():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    tb = _ReactiveTB()
    ogre.apply_buff(tb)

    # The registered entity handler is our wrapper, not the bound method —
    # equality-compare (bound methods compare equal by (__self__, __func__)).
    entity_handlers = _registered(lvl.event_manager, Level.EventOnPass, ogre)
    assert len(entity_handlers) == 1
    assert entity_handlers[0] != tb.on_pass

    # And it still calls through.
    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert tb.pass_count == 1

    # Global trigger likewise.
    global_handlers = _registered(lvl.event_manager, Level.EventOnUnitAdded)
    assert any(h != tb.on_added and callable(h) for h in global_handlers)

    # The buff's own trigger dicts are NEVER mutated (pickle transparency's
    # load-bearing half): they still hold the original bound methods.
    assert tb.owner_triggers[Level.EventOnPass] == tb.on_pass
    assert tb.global_triggers[Level.EventOnUnitAdded] == tb.on_added


def test_non_buff_registrants_untouched():
    lvl = _fresh_level()
    fired = []

    def module_fn_handler(evt):
        fired.append(evt)

    class _ViewShape:  # UnitSprite shape: bound method, __self__ not a Buff
        def on_evt(self, evt):
            fired.append(evt)

    view = _ViewShape()
    em = lvl.event_manager
    em.register_global_trigger(Level.EventOnPass, module_fn_handler)
    em.register_entity_trigger(Level.EventOnPass, view, view.on_evt)

    # Identity preserved — the exact objects sit in the tuples, unwrapped.
    assert module_fn_handler in _registered(em, Level.EventOnPass)
    assert view.on_evt in _registered(em, Level.EventOnPass, view)
    assert _map_of(em) == {}

    # And unregister round-trips through the engine untouched.
    em.unregister_global_trigger(Level.EventOnPass, module_fn_handler)
    em.unregister_entity_trigger(Level.EventOnPass, view, view.on_evt)
    assert _registered(em, Level.EventOnPass) == ()


# ---- Unregister translation: no ghost, map self-cleans to baseline ----

def test_buff_removal_leaves_no_ghost_and_map_returns_to_baseline():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    em = lvl.event_manager
    baseline = dict(_map_of(em))

    tb = _ReactiveTB()
    ogre.apply_buff(tb)
    # Multi-handler lifecycle: two trigger types -> two map entries.
    assert len(_map_of(em)) == len(baseline) + 2

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert tb.pass_count == 1

    ogre.remove_buff(tb)
    # Map size returns exactly to its pre-apply baseline (a single-key
    # removal check would miss a leak on handler 2..N).
    assert len(_map_of(em)) == len(baseline)

    # NO ghost: the wrapper is gone from the engine tuples, and the handler
    # does not fire on the next raise.
    assert _registered(em, Level.EventOnPass, ogre) == ()
    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert tb.pass_count == 1


def test_self_unregister_during_dispatch_no_ghost():
    # Equipment.py:1123-1126 shape: the handler removes its own buff while
    # its event is being dispatched. Engine-side safe (tuple snapshot); the
    # translation must resolve mid-dispatch and self-clean.
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    em = lvl.event_manager
    baseline_size = len(_map_of(em))

    tb = _SelfRemovingTB()
    ogre.apply_buff(tb)
    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert tb.fired == 1
    assert tb not in ogre.buffs

    # Ghost check: a second raise must NOT re-fire the removed buff.
    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert tb.fired == 1
    assert len(_map_of(em)) == baseline_size


# ---- Load lifecycle: fresh manager + re-subscribe re-wraps ----

def test_rebuild_self_heals_on_fresh_manager():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    tb = _ReactiveTB()
    ogre.apply_buff(tb)

    # The rebuild_event_managers shape (Game.py:219-226): fresh manager,
    # every buff re-subscribed through the (patched) register functions.
    lvl.event_manager = Level.EventHandler()
    tb.subscribe()

    handlers = _registered(lvl.event_manager, Level.EventOnPass, ogre)
    assert len(handlers) == 1 and handlers[0] != tb.on_pass
    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert tb.pass_count == 1
    # The old manager (and its map) is garbage with the old level state; the
    # new manager's map holds exactly this buff's two entries.
    assert len(_map_of(lvl.event_manager)) == 2


# ---- Pickle transparency ----

def test_pickle_contains_no_wrapper():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    ogre.apply_buff(_ReactiveTB())

    # The game serializes with dill (Buff.__init__ holds a lambda plain
    # pickle rejects). dill CAN serialize closures, so success alone proves
    # nothing — assert directly that no trace of the wrapper machinery
    # (module reference, per-manager map attribute) reaches the save bytes
    # (event_manager nulled at __getstate__, trigger dicts never mutated).
    # (The test buff class's own qualname 'tests.test_reactive_markers'
    # legitimately appears — buffs pickle by reference. The MOD's artifacts
    # are the map attribute and the wrapper closure.)
    import dill
    blob = dill.dumps(lvl)
    assert b'_wop_reactive_map' not in blob
    assert b'_reactive_dispatch' not in blob


# ---- Step 2: lazy materialization via the record gate ----

class _NestedRaiseTB(Level.Buff):
    """A reaction whose only effect is raising another event — the
    record-gate materialization shape (any record created during the
    handler promotes the breadcrumb)."""
    def on_init(self):
        self.name = "Nested Raise Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.owner_triggers[Level.EventOnPass] = self.on_pass
        self.target = None

    def on_pass(self, evt):
        self.owner.level.event_manager.raise_event(
            Level.EventOnPass(self.target), self.target)


def _markers():
    return [r for r in journal.records if r['event_type'] == 'reactive_proc']


def _events_of(kind):
    return [r for r in journal.records if r['event_type'] == kind]


def test_record_gate_materializes_marker_parented_to_event():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    gob = _place(lvl, _unit("Goblin"), 6, 5)
    tb = _NestedRaiseTB()
    ogre.apply_buff(tb)
    tb.target = gob

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)

    ms = _markers()
    assert len(ms) == 1
    m = ms[0]
    assert m['payload']['via'] == 'record_gate'
    assert m['payload']['buff'] == "Nested Raise Test Buff"
    assert (m['payload']['recipient'] or {}).get('name') == "Ogre"

    passes = _events_of('EventOnPass')
    assert len(passes) == 2
    outer, inner = passes
    # Marker parents to the triggering event; the reaction's own record
    # (the nested raise) parents to the marker.
    assert m['parent'] == outer['sequence']
    assert inner['parent'] == m['sequence']


def test_noop_reaction_evaporates():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    tb = _ReactiveTB()          # handler only bumps a python counter
    ogre.apply_buff(tb)

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert tb.pass_count == 1
    assert _markers() == []


def test_nested_reaction_chain_outermost_first():
    # A reacts to E1 by raising E2; B (another unit's buff) reacts to E2 by
    # raising E3. Marker nesting must mirror dispatch nesting:
    # E1 -> markerA -> E2 -> markerB -> E3.
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    gob = _place(lvl, _unit("Goblin"), 6, 5)
    troll = _place(lvl, _unit("Troll"), 7, 5)

    a = _NestedRaiseTB()
    ogre.apply_buff(a)
    a.target = gob
    b = _NestedRaiseTB()
    gob.apply_buff(b)
    b.target = troll

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)

    ms = _markers()
    assert len(ms) == 2
    m_a, m_b = ms
    e1, e2, e3 = _events_of('EventOnPass')
    assert m_a['parent'] == e1['sequence']
    assert e2['parent'] == m_a['sequence']
    assert m_b['parent'] == e2['sequence']
    assert e3['parent'] == m_b['sequence']


def test_marker_never_clears_suspended_cast_window():
    # ⟨GATE⟩ pin, the second parent-resolution branch: a marker fires while
    # a cast window is SUSPENDED (_pending_ctx set, cause_stack empty). The
    # marker must not clear the window, and a post-marker delta still
    # attributes to the suspended cast.
    import types as T
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    gob = _place(lvl, _unit("Goblin"), 6, 5)
    tb = _NestedRaiseTB()
    ogre.apply_buff(tb)
    tb.target = gob
    container_diff.sweep(lvl, site='test:baseline')

    cb = journal.record('cast_begin', {'detail': 'suspended-test'})
    ctx = T.SimpleNamespace(_cast_begin=cb)
    container_diff._pending_ctx = ctx

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)
    assert len(_markers()) == 1
    assert container_diff._pending_ctx is ctx

    ogre.turns_to_death = 7
    container_diff.sweep(lvl, site='test:delta')
    lifespans = [r for r in journal.records
                 if r['event_type'] == 'lifespan_change']
    assert lifespans
    assert lifespans[-1]['parent'] == cb['sequence']
    container_diff._pending_ctx = None


# ---- Step 3: write-taps (charges / adjust) ----

class _TestSpell(Level.Spell):
    """Minimal shelf spell: refund_charges needs cur_charges +
    get_stat('max_charges') only."""
    def __init__(self, name, charges):
        Level.Spell.__init__(self)
        self.name = name
        self.cur_charges = charges

    def get_stat(self, attr, *a, **k):
        return 99


class _RefundOnDeathTB(Level.Buff):
    """The MARQUEE Soul-Harvester shape (Equipment.py:116-136): an
    EventOnDeath reaction whose only data effect is a silent charge refund."""
    def on_init(self):
        self.name = "Refund On Death Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.global_triggers[Level.EventOnDeath] = self.on_death
        self.spell = None

    def on_death(self, evt):
        self.spell.refund_charges(1)


class _AssimShapeTB(Level.Buff):
    """The AssimilationBuff residual shape (Spells.py:15738-41): a PRE-tap
    dict write (cool_downs.pop), then a tapped refund."""
    def on_init(self):
        self.name = "Assim Shape Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.owner_triggers[Level.EventOnPass] = self.on_pass
        self.cd_spell = None
        self.spell = None

    def on_pass(self, evt):
        self.owner.cool_downs.pop(self.cd_spell, None)   # pre-tap, silent
        self.spell.refund_charges(1)                     # the tap


class _DamageGrowthTB(Level.Buff):
    """The damage-growth quartet shape (Equipment.py:1113): a reactive
    spell.damage += with no tap, no flash, no record — accepted residual."""
    def on_init(self):
        self.name = "Damage Growth Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.owner_triggers[Level.EventOnPass] = self.on_pass
        self.spell = None

    def on_pass(self, evt):
        self.spell.damage = getattr(self.spell, 'damage', 0) + 1


def _wizard(lvl, x=2, y=2, spells=()):
    wiz = _place(lvl, _unit("Wizard", player=True), x, y)
    for s in spells:
        wiz.spells.append(s)
    lvl.player_unit = wiz
    return wiz


def test_marquee_refund_in_death_reaction_attributes_to_marker():
    lvl = _fresh_level()
    fireball = _TestSpell('Fireball', 1)
    wiz = _wizard(lvl, spells=[fireball])
    gob = _place(lvl, _unit("Goblin"), 6, 5)
    tb = _RefundOnDeathTB()
    wiz.apply_buff(tb)
    tb.spell = fireball
    container_diff.sweep(lvl, site='test:baseline')

    lvl.event_manager.raise_event(Level.EventOnDeath(gob, None), gob)

    ms = _markers()
    assert len(ms) == 1
    m = ms[0]
    assert m['payload']['via'] == 'charges'
    death = _events_of('EventOnDeath')[-1]
    assert m['parent'] == death['sequence']

    charges = [r for r in journal.records
               if r['event_type'] == 'charges_change']
    assert len(charges) == 1
    c = charges[0]
    assert (c['payload']['before'], c['payload']['after']) == (1, 2)
    assert c['parent'] == m['sequence']
    assert c['payload']['bracket'] == 'reactive_proc'
    assert not c['payload']['unattributed']


def test_adjust_tag_bonus_reaction_attributes_to_marker():
    # The Crimson-Brooch shape (Equipment.py:7197): adjust_tag_bonus is a
    # tapped Unit method; the stat_bonus_change lands under the marker.
    lvl = _fresh_level()
    wiz = _wizard(lvl)

    class _BroochTB(Level.Buff):
        def on_init(self):
            self.name = "Brooch Shape Test Buff"
            self.buff_type = Level.BUFF_TYPE_BLESS
            self.owner_triggers[Level.EventOnPass] = self.on_pass

        def on_pass(self, evt):
            self.owner.adjust_tag_bonus(Level.Tags.Fire, 'damage', 4, pct=True)

    b = _BroochTB()
    wiz.apply_buff(b)
    container_diff.sweep(lvl, site='test:baseline')

    lvl.event_manager.raise_event(Level.EventOnPass(wiz), wiz)

    ms = _markers()
    assert len(ms) == 1
    assert ms[0]['payload']['via'] == 'adjust'
    bonuses = [r for r in journal.records
               if r['event_type'] == 'stat_bonus_change']
    assert bonuses
    assert bonuses[-1]['parent'] == ms[0]['sequence']
    assert bonuses[-1]['payload']['bracket'] == 'reactive_proc'


def test_assim_shape_pre_tap_write_parents_to_event_post_tap_to_marker():
    # ⟨GATE⟩ residual pinned AS DESIGNED: the pre-tap cool_downs.pop flushes
    # to the triggering EVENT at materialization (the graceful direction);
    # the tapped refund lands under the marker.
    lvl = _fresh_level()
    fireball = _TestSpell('Fireball', 1)
    cd_spell = _TestSpell('Bolt', 0)
    wiz = _wizard(lvl, spells=[fireball])
    wiz.cool_downs[cd_spell] = 3
    tb = _AssimShapeTB()
    wiz.apply_buff(tb)
    tb.cd_spell = cd_spell
    tb.spell = fireball
    container_diff.sweep(lvl, site='test:baseline')

    lvl.event_manager.raise_event(Level.EventOnPass(wiz), wiz)

    ms = _markers()
    assert len(ms) == 1
    m = ms[0]
    evt_rec = _events_of('EventOnPass')[-1]

    cooldowns = [r for r in journal.records
                 if r['event_type'] == 'cooldown_change']
    assert cooldowns
    assert cooldowns[-1]['parent'] == evt_rec['sequence']

    charges = [r for r in journal.records
               if r['event_type'] == 'charges_change']
    assert charges
    assert charges[-1]['parent'] == m['sequence']


def test_damage_growth_reaction_stays_recordless():
    # ⟨GATE⟩ residual pinned AS DESIGNED: a reactive spell.damage += is in
    # no watched domain and fires no tap — no marker, no records beyond the
    # event itself. Drift in either direction surfaces here.
    lvl = _fresh_level()
    fireball = _TestSpell('Fireball', 1)
    wiz = _wizard(lvl, spells=[fireball])
    tb = _DamageGrowthTB()
    wiz.apply_buff(tb)
    tb.spell = fireball
    container_diff.sweep(lvl, site='test:baseline')
    count_before = len(journal.records)

    lvl.event_manager.raise_event(Level.EventOnPass(wiz), wiz)

    assert _markers() == []
    new = journal.records[count_before:]
    assert [r['event_type'] for r in new] == ['EventOnPass']
    assert fireball.damage == 1


def test_drain_charges_write_then_flash_still_attributes():
    # drain_charges internally flashes AFTER its write (Level.py:935-937);
    # the charge tap fires BEFORE the original, so ordering is safe.
    lvl = _fresh_level()
    fireball = _TestSpell('Fireball', 3)
    wiz = _wizard(lvl, spells=[fireball])

    class _DrainTB(Level.Buff):
        def on_init(self):
            self.name = "Drain Shape Test Buff"
            self.buff_type = Level.BUFF_TYPE_BLESS
            self.owner_triggers[Level.EventOnPass] = self.on_pass
            self.spell = None

        def on_pass(self, evt):
            self.spell.drain_charges(1)

    b = _DrainTB()
    wiz.apply_buff(b)
    b.spell = fireball
    container_diff.sweep(lvl, site='test:baseline')

    lvl.event_manager.raise_event(Level.EventOnPass(wiz), wiz)

    ms = _markers()
    assert len(ms) == 1
    assert ms[0]['payload']['via'] == 'charges'
    charges = [r for r in journal.records
               if r['event_type'] == 'charges_change']
    assert charges
    assert (charges[-1]['payload']['before'],
            charges[-1]['payload']['after']) == (3, 2)
    assert charges[-1]['parent'] == ms[0]['sequence']


# ---- Step 4: flash + request taps ----

class _FlashOnlyEq(Level.Equipment):
    """A flash-only proc: the sighted player sees the icon flash even though
    no data effect follows. The marker (via='flash') IS that render's
    capture (capture ⊇ render)."""
    def on_init(self):
        self.name = "Flash Only Test Trinket"
        self.owner_triggers[Level.EventOnPass] = self.on_pass

    def on_pass(self, evt):
        self.do_ui_flash()


class _MaskShapeEq(Level.Equipment):
    """The MaskOfNihilo residual shape (Equipment.py:7565-68): resists
    writes BEFORE the only tap (flash) — the deltas flush to the event, the
    marker stands adjacent."""
    def on_init(self):
        self.name = "Mask Shape Test Trinket"
        self.owner_triggers[Level.EventOnPass] = self.on_pass

    def on_pass(self, evt):
        self.owner.resists[Level.Tags.Fire] += 10
        self.do_ui_flash()


class _QueueingTB(Level.Buff):
    """The PsychopompStaff shape (Equipment.py:8071-98): the reaction's
    effect lives in a generator it queue_spells — the request tap
    materializes the marker so the deferred work parents to it."""
    def on_init(self):
        self.name = "Queueing Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.owner_triggers[Level.EventOnPass] = self.on_pass
        self.target = None

    def on_pass(self, evt):
        self.owner.level.queue_spell(self.deferred())

    def deferred(self):
        self.owner.level.event_manager.raise_event(
            Level.EventOnPass(self.target), self.target)
        yield


def test_flash_only_proc_materializes_marker_no_children():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    eq = _FlashOnlyEq()
    ogre.apply_buff(eq)
    count_before = len(journal.records)

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)

    ms = _markers()
    assert len(ms) == 1
    assert ms[0]['payload']['via'] == 'flash'
    evt_rec = _events_of('EventOnPass')[-1]
    assert ms[0]['parent'] == evt_rec['sequence']
    # No children: the raise produced exactly the event record + the marker.
    assert len(journal.records) == count_before + 2


def test_mask_shape_pre_flash_resists_parent_to_event_marker_adjacent():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    eq = _MaskShapeEq()
    ogre.apply_buff(eq)
    container_diff.sweep(lvl, site='test:baseline')

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)

    ms = _markers()
    assert len(ms) == 1
    assert ms[0]['payload']['via'] == 'flash'
    evt_rec = _events_of('EventOnPass')[-1]
    resists = [r for r in journal.records
               if r['event_type'] == 'resists_change']
    assert resists
    # AS DESIGNED: the pre-tap write flushed OUTWARD to the triggering
    # event (never the live cast); the marker stands adjacent.
    assert resists[-1]['parent'] == evt_rec['sequence']
    assert ms[0]['parent'] == evt_rec['sequence']


def test_reaction_queued_cast_parents_to_marker():
    lvl = _fresh_level()
    ogre = _place(lvl, _unit("Ogre"), 5, 5)
    gob = _place(lvl, _unit("Goblin"), 6, 5)
    tb = _QueueingTB()
    ogre.apply_buff(tb)
    tb.target = gob

    lvl.event_manager.raise_event(Level.EventOnPass(ogre), ogre)

    # The queue request materialized the marker even though nothing else
    # in the handler recorded.
    ms = _markers()
    assert len(ms) == 1
    assert ms[0]['payload']['via'] == 'request'

    # Pump the queued generator: its deferred effect must parent under the
    # marker (the request-time cause capture saw it).
    lvl.advance_spells()
    inner = _events_of('EventOnPass')[-1]
    assert inner is not _events_of('EventOnPass')[0]
    assert _has_ancestor(inner, ms[0]['sequence'])


def _has_ancestor(rec, ancestor_seq):
    by_seq = {r["sequence"]: r for r in journal.records}
    seq = rec["parent"]
    while seq is not None:
        if seq == ancestor_seq:
            return True
        parent = by_seq.get(seq)
        seq = parent["parent"] if parent else None
    return False


# ---- Step 5: the redirect leg ----

class _RedirectTB(Level.Buff):
    """The LordOfRot shape (Spells.py:16227-33): a buff granting a damage-
    redirect closure that silently refunds charges when it fires."""
    def on_init(self):
        self.name = "Redirect Test Buff"
        self.buff_type = Level.BUFF_TYPE_BLESS
        self.spell = None

    def on_applied(self, owner):
        self.owner.grant_redirect(self, self.redirect)

    def on_unapplied(self):
        self.owner.remove_redirect(self)

    def redirect(self, amount, damage_type, source):
        self.spell.refund_charges(1)
        return None     # leaves the damage untouched


def test_redirect_closure_attributes_to_marker_and_removal_is_clean():
    import types as T
    lvl = _fresh_level()
    fireball = _TestSpell('Fireball', 1)
    wiz = _wizard(lvl, x=5, y=5, spells=[fireball])
    tb = _RedirectTB()
    tb.spell = fireball
    wiz.apply_buff(tb)
    container_diff.sweep(lvl, site='test:baseline')
    hp_before = wiz.cur_hp

    src = T.SimpleNamespace(name="Blow", owner=None)
    lvl.deal_damage(5, 5, 5, Level.Tags.Physical, src)

    ms = _markers()
    assert len(ms) == 1
    m = ms[0]
    assert m['payload']['channel'] == 'redirect'
    assert m['payload']['via'] == 'charges'
    assert m['payload']['buff'] == "Redirect Test Buff"
    charges = [r for r in journal.records
               if r['event_type'] == 'charges_change']
    assert charges and charges[-1]['parent'] == m['sequence']
    # The redirect returned None: damage flowed unchanged.
    assert wiz.cur_hp == hp_before - 5

    # Removal goes through the ENGINE's chained-closure identity check —
    # untouched by the wrap. No refund, no new marker on the next hit.
    wiz.remove_buff(tb)
    lvl.deal_damage(5, 5, 5, Level.Tags.Physical, src)
    assert fireball.cur_charges == 2        # unchanged (one refund only)
    assert len(_markers()) == 1
    assert wiz.cur_hp == hp_before - 10


# ---- Install shape ----

def test_register_functions_are_patched():
    _fresh_level()
    # The four class methods are no longer the originals (drift tripwire:
    # if a game update renames/moves them, install declines and this fails).
    for name in ('register_global_trigger', 'unregister_global_trigger',
                 'register_entity_trigger', 'unregister_entity_trigger'):
        fn = vars(Level.EventHandler)[name]
        assert fn.__name__.startswith('patched_'), name


def test_install_declines_on_missing_seam_and_wraps_nothing():
    # ⟨GATE⟩ the identity form of the kill/decline story: when a seam is
    # missing (RW2 backport / API drift), install returns False and the
    # register functions are the EXACT objects they were before the call —
    # not merely "no markers observed". (The settings kill switch gates the
    # install CALL itself, screen_reader Phase 2.8 — same as Units 1/2.)
    _fresh_level()
    saved = Level.Spell.refund_charges
    saved_flag = reactive_markers._installed
    before = {n: vars(Level.EventHandler)[n]
              for n in ('register_global_trigger', 'unregister_global_trigger',
                        'register_entity_trigger', 'unregister_entity_trigger')}
    try:
        del Level.Spell.refund_charges
        reactive_markers._installed = False
        assert reactive_markers.install() is False
        for n, fn in before.items():
            assert vars(Level.EventHandler)[n] is fn, n
    finally:
        Level.Spell.refund_charges = saved
        reactive_markers._installed = saved_flag


def test_double_install_noops():
    _fresh_level()
    before = vars(Level.EventHandler)['register_global_trigger']
    assert reactive_markers.install() is True
    assert vars(Level.EventHandler)['register_global_trigger'] is before


def test_reactive_proc_staged_in_composer_known_set():
    # The Unit-2 gate lesson: nothing guarded known-set registration.
    import digest
    assert 'reactive_proc' in digest._COMPOSER_KNOWN_EVENT_TYPES
