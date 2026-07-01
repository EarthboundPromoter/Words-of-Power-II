# Live-Level tests for the R3 cause-graph (execute_cast anchor + _pending_cause
# FIFO + CastContext piggyback). Unlike test_journal.py (pure functions on
# SimpleNamespace fixtures), these drive REAL casts through the patched hooks on a
# real Level, because the cause-graph only exists once the hooks are wired.
#
# Run from the game root: python -m pytest "<mod>/tests/test_journal_causegraph.py"
# (journal imports Level; the game root must be on sys.path.)
#
# Coverage maps to the R3 build plan's census timing modes:
#   - manual keypress -> root
#   - in-keypress proc via defer_cast -> own cast_begin, nested under the keypress
#   - out-of-chain defer (no live cause) -> own root
#   - inline sub-cast (queue=False) -> nested under parent
#   - buff-tick-that-casts -> parents to the buff_tick root
#   - direct queue_spell effect -> parents to the running cast (wrap retained)
#   - events raised during an (unwrapped) execute_cast gen -> parent the cast
#   - is_manual_cast restored (the shipped-bug regression guard)
#   - _pending_cause lockstep / cap-clear reconcile

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

def _fresh_level():
    install_hooks()  # idempotent
    lvl = Level.Level(15, 15)
    journal.reset(id(lvl))
    journal._level = lvl
    wiz = Level.Unit()
    wiz.name = "Wizard"
    wiz.is_player_controlled = True
    wiz.max_hp = 50
    return lvl, wiz


def _place(lvl, unit, x, y, player=True):
    unit.is_player_controlled = player
    lvl.add_obj(unit, x, y)
    return unit


def _drain(lvl):
    while lvl.can_advance_spells():
        lvl.advance_spells()


def _begins(spell_name=None):
    out = [r for r in journal.records if r["event_type"] == "cast_begin"]
    if spell_name is not None:
        out = [r for r in out if r["payload"]["spell"]["name"] == spell_name]
    return out


def _by_seq():
    return {r["sequence"]: r for r in journal.records}


def _root_of(rec):
    by = _by_seq()
    cur = rec
    while cur["parent"] is not None:
        cur = by[cur["parent"]]
    return cur


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
    s = _S()
    return s


# ---- Tests ----

def test_manual_cast_is_root():
    lvl, wiz = _fresh_level()
    a = _make_spell("A")
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)
    begins = _begins("A")
    assert len(begins) == 1
    assert begins[0]["parent"] is None          # keypress root
    assert begins[0]["payload"]["is_player"] is True
    assert begins[0]["payload"]["pay_costs"] is True


def test_deferred_proc_nests_under_keypress():
    # In-keypress proc: A's effects defer_cast B. B must get its OWN cast_begin
    # (individuated) AND root back to A's keypress.
    lvl, wiz = _fresh_level()

    def a_body(self, x, y, **kw):
        b = _make_spell("B")
        b.caster = self.caster
        b.owner = self.caster
        self.caster.level.defer_cast(self.caster, b, x, y, pay_costs=False)
        yield

    a = _make_spell("A", a_body)
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)

    a_begin = _begins("A")[0]
    b_begin = _begins("B")[0]
    assert a_begin["parent"] is None
    # B has its own chain id and nests under A
    assert b_begin["action_chain_id"] != a_begin["action_chain_id"]
    assert b_begin["parent"] == a_begin["sequence"]
    assert _root_of(b_begin)["sequence"] == a_begin["sequence"]


def test_out_of_chain_defer_is_own_root():
    # An enemy-turn defer has no live cause (empty cause_stack, no cast context).
    # It should become its own root, not orphan-attach to some unrelated cast.
    lvl, wiz = _fresh_level()
    enemy = Level.Unit()
    enemy.name = "Goblin"
    _place(lvl, enemy, 3, 3, player=False)
    spell = _make_spell("Reaction")
    spell.caster = enemy
    spell.owner = enemy
    assert not journal.cause_stack
    assert getattr(lvl, "current_cast_context", None) is None
    lvl.defer_cast(enemy, spell, 3, 3, pay_costs=False)
    _drain(lvl)
    r = _begins("Reaction")[0]
    assert r["parent"] is None                    # own root (ambient)
    assert r["payload"]["is_player"] is False


def test_inline_subcast_nests_under_parent():
    # A yields from an inline (queue=False) sub-cast of B. B must nest under A.
    lvl, wiz = _fresh_level()

    def a_body(self, x, y, **kw):
        b = _make_spell("Binline")
        b.caster = self.caster
        b.owner = self.caster
        yield self.caster.level.act_cast(self.caster, b, x, y, pay_costs=False, queue=False)

    a = _make_spell("A", a_body)
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)

    a_begin = _begins("A")[0]
    b_begin = _begins("Binline")[0]
    assert a_begin["parent"] is None
    assert _root_of(b_begin)["sequence"] == a_begin["sequence"]


def test_direct_queue_effect_parents_to_running_cast():
    # A directly queue_spells an inner effect gen (the ~150-site pattern). The
    # inner effect's records must parent into A's tree via the retained wrap.
    lvl, wiz = _fresh_level()

    def inner(self):
        self.caster.level.event_manager.raise_event(Level.EventOnPass(self.caster), self.caster)
        yield

    def a_body(self, x, y, **kw):
        self.caster.level.queue_spell(inner(self))
        yield

    a = _make_spell("A", a_body)
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)

    a_begin = _begins("A")[0]
    passes = [r for r in journal.records if r["event_type"] == "EventOnPass"]
    assert passes, "direct-queued effect's event was not journaled"
    assert _root_of(passes[-1])["sequence"] == a_begin["sequence"]


def test_reaction_queue_during_execute_cast_roots_to_cast():
    # Regression for the adversarial-gate finding: a handler reacting to
    # EventOnSpellCast (raised INSIDE execute_cast, Level.py:3140) that directly
    # queue_spells a free/copy cast must root to the triggering cast, not orphan.
    # This is the window the old _in_execute_cast time-flag wrongly swallowed;
    # mirrors AlchemistMulticastBuff (Equipment.py:1676) / Dragon-mage copy (3608).
    lvl, wiz = _fresh_level()
    _place(lvl, wiz, 7, 7)

    class _MulticastBuff(Level.Buff):
        def on_init(self):
            self.owner_triggers[Level.EventOnSpellCast] = self.on_cast
            self._fired = False
        def on_cast(self, evt):
            if self._fired:
                return
            self._fired = True
            copy = _make_spell("Copy")
            copy.caster = self.owner
            copy.owner = self.owner
            def copy_gen():
                self.owner.level.act_cast(self.owner, copy, evt.x, evt.y,
                                          pay_costs=False, queue=False)
                yield
            self.owner.level.queue_spell(copy_gen())

    buff = _MulticastBuff()
    buff.buff_type = Level.BUFF_TYPE_ITEM
    wiz.apply_buff(buff, 0)

    a = _make_spell("A")
    wiz.add_spell(a)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)

    a_begin = _begins("A")[0]
    copy_begin = _begins("Copy")[0]
    assert copy_begin["parent"] is not None, "copy cast orphaned (parent=None)"
    assert _root_of(copy_begin)["sequence"] == a_begin["sequence"]


def test_event_during_unwrapped_gen_parents_to_cast():
    # An execute_cast gen is NO LONGER wrapped; events it raises must still parent
    # to its cast_begin via the current_cast_context fallback in _current_parent_seq.
    lvl, wiz = _fresh_level()

    def a_body(self, x, y, **kw):
        self.caster.level.event_manager.raise_event(Level.EventOnPass(self.caster), self.caster)
        yield

    a = _make_spell("A", a_body)
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)

    a_begin = _begins("A")[0]
    passes = [r for r in journal.records if r["event_type"] == "EventOnPass"]
    assert passes
    assert _root_of(passes[-1])["sequence"] == a_begin["sequence"]


def test_is_manual_cast_restored():
    # Regression guard for the shipped desync (docs/IS_MANUAL_CAST_DESYNC.md):
    # with the mod loaded, is_manual_cast() must be True for a real manual cast.
    lvl, wiz = _fresh_level()
    seen = {}

    def a_body(self, x, y, **kw):
        seen['manual'] = self.is_manual_cast()
        return
        yield

    a = _make_spell("A", a_body)
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)
    assert seen.get('manual') is True


def test_buff_tick_cast_parents_to_tick():
    # Gate 5: a buff whose on_advance casts. The cast must parent to the buff_tick
    # root (via cause_stack), not orphan and not mis-root to a stale cast.
    lvl, wiz = _fresh_level()
    _place(lvl, wiz, 7, 7)

    class _TickBuff(Level.Buff):
        def on_advance(self):
            s = _make_spell("TickCast")
            s.caster = self.owner
            s.owner = self.owner
            self.owner.level.act_cast(self.owner, s, self.owner.x, self.owner.y, pay_costs=False)

    buff = _TickBuff()
    buff.buff_type = Level.BUFF_TYPE_BLESS
    wiz.apply_buff(buff, 5)
    wiz.advance_buffs()
    _drain(lvl)

    tick_cast = _begins("TickCast")[0]
    root = _root_of(tick_cast)
    assert root["event_type"] == "buff_tick"
    assert root["payload"]["owner"]["id"] == id(wiz)


def test_pending_cause_empty_after_drain():
    lvl, wiz = _fresh_level()

    def a_body(self, x, y, **kw):
        b = _make_spell("B")
        b.caster = self.caster
        b.owner = self.caster
        self.caster.level.defer_cast(self.caster, b, x, y, pay_costs=False)
        yield

    a = _make_spell("A", a_body)
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)
    _drain(lvl)
    assert len(journal._pending_cause) == 0


def test_exception_mid_drain_keeps_fifo_aligned():
    # Adversarial-gate finding: if an exception aborts a drain mid-way, the engine
    # leaves un-drained tuples in pending_casts (paired with their _pending_cause
    # entries). The reconcile must NOT clear _pending_cause then, or the leftover
    # steals the next cast's cause. Verify alignment holds and the next live-cause
    # cast (a buff tick) still roots correctly while the leftover keeps its own.
    lvl, wiz = _fresh_level()
    _place(lvl, wiz, 7, 7)

    class _NastyBuff(Level.Buff):
        def on_init(self):
            self.owner_triggers[Level.EventOnSpellCast] = self.on_cast
            self._fired = False
        def on_cast(self, evt):
            if self._fired:
                return
            self._fired = True
            leftover = _make_spell("Leftover")
            leftover.caster = self.owner
            leftover.owner = self.owner
            self.owner.level.defer_cast(self.owner, leftover, evt.x, evt.y, pay_costs=False)
            raise RuntimeError("boom mid-drain")

    nasty = _NastyBuff()
    nasty.buff_type = Level.BUFF_TYPE_ITEM
    wiz.apply_buff(nasty, 0)
    a = _make_spell("A")
    wiz.add_spell(a)

    with pytest.raises(RuntimeError):
        lvl.act_cast(wiz, a, 9, 7, pay_costs=True)

    # FIFO stays paired, not skewed to (1, 0).
    assert len(journal._pending_cause) == len(lvl.pending_casts)

    # Game continues: a buff tick casts Victim with a live cause.
    class _TickBuff(Level.Buff):
        def on_advance(self):
            v = _make_spell("Victim")
            v.caster = self.owner
            v.owner = self.owner
            self.owner.level.act_cast(self.owner, v, self.owner.x, self.owner.y, pay_costs=False)

    tick = _TickBuff()
    tick.buff_type = Level.BUFF_TYPE_BLESS
    wiz.apply_buff(tick, 5)
    wiz.advance_buffs()
    _drain(lvl)

    victim = _begins("Victim")[0]
    assert _root_of(victim)["event_type"] == "buff_tick"   # not stolen, not orphaned


def test_process_pending_casts_clears_stale_pending_cause():
    # Simulate a DEFERRED_CAST_CAP-style leftover: a stale entry sits in
    # _pending_cause; the next outer drain must reconcile it away.
    lvl, wiz = _fresh_level()
    journal._pending_cause.append(None)   # stale, never paired with an execute_cast
    a = _make_spell("A")
    wiz.add_spell(a)
    _place(lvl, wiz, 7, 7)
    lvl.act_cast(wiz, a, 9, 7, pay_costs=True)   # outer drain
    _drain(lvl)
    assert len(journal._pending_cause) == 0
