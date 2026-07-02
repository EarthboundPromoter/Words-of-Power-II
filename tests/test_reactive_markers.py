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


# ---- Install shape ----

def test_register_functions_are_patched():
    _fresh_level()
    # The four class methods are no longer the originals (drift tripwire:
    # if a game update renames/moves them, install declines and this fails).
    for name in ('register_global_trigger', 'unregister_global_trigger',
                 'register_entity_trigger', 'unregister_entity_trigger'):
        fn = vars(Level.EventHandler)[name]
        assert fn.__name__.startswith('patched_'), name
