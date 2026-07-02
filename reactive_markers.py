# Root-2 cause-markers, leg 4 (Unit 3): the reactive-proc marker.
#
# The ratified Theme D lean shape: WHICH buff/gear/passive reacted is captured
# by classifying at SUBSCRIPTION (wrap buff-owned handlers at the EventHandler
# register functions, owner baked in), dropping a BREADCRUMB at dispatch (one
# reference push/pop around the original handler, ~100ns), and LAZILY
# MATERIALIZING a real `reactive_proc` marker record only when the reaction
# demonstrably does something (five taps — journal record gate, charge writes,
# stat-bonus adjusts, do_ui_flash, cast requests; installed in later steps).
# No-op handler runs evaporate; journal growth ∝ real effects.
#
# Wrapper discipline (first Root-2 leg IN the dispatch call path): the
# original handler ALWAYS runs — breadcrumb bookkeeping is try/except-guarded
# on both sides and never swallows the handler's own exceptions (the
# try/finally balance is what keeps the module-level stack correct across the
# engine's real nested dispatches, e.g. apply_buff raising
# EventOnBuffAttemptApply mid-handler, Level.py:2425).
#
# The translation map (original -> wrapper, for the unregister equality lookup
# at Level.py:218/234) lives ON each EventHandler instance: it dies with the
# manager on load-rebuild (Game.rebuild_event_managers builds a fresh manager
# and re-runs every buff.subscribe() through these wraps — the wrapping
# self-heals per load) and is pickle-invisible because Level.__getstate__
# nulls event_manager (Level.py:2934). Map values are LIFO lists ⟨GATE⟩: the
# engine's one-handler-per-event-type-per-buff dict shape makes duplicate
# keys impossible today, but nothing structurally prevents a future
# re-apply() of the same buff instance — a single-valued map would ghost-leak
# on the second register. RW3's ValueError-tolerant Buff.unsubscribe
# (Level.py:1282-1287) would MASK a translation miss (a leaked wrapper is a
# ghost reaction firing after buff removal), which is why translation
# correctness is pinned as game behavior, not hygiene.
#
# Install is self-gating and field-killable (`reactive_markers_enabled`):
# seam shapes verified before wrapping, declines cleanly otherwise (RW2
# backport inert). Records-only; all voicing is composer-phase.

from journal import journal, _snapshot_unit
import journal as _journal_module

_installed = False
_log_fn = None
_failed_sites = set()       # once-per-site failure-note dedupe

# The attribute name for the per-manager translation map.
_MAP_ATTR = '_wop_reactive_map'

# The breadcrumb stack. Entries are small mutable lists [buff, marker_record]
# (marker_record is None until a tap materializes it in later steps). Module
# level is safe: dispatch is single-threaded and synchronous, and nested
# raises unwind as ordinary call-stack nesting (gate-verified: no synchronous
# mid-dispatch level swap exists; deferred work goes through queue_spell).
_crumbs = []


def _note_failure(site, exc):
    if site in _failed_sites:
        return
    _failed_sites.add(site)
    if _log_fn:
        try:
            _log_fn("[ReactiveMarkers] capture failure at %s: %r" % (site, exc))
        except Exception:
            pass


# ----------------------------------------------------------------------
# Lazy materialization (plan D3-D5).
#
# A breadcrumb becomes a real `reactive_proc` marker record + cause window
# only when a tap fires. The record gate (journal.record wrap below) is tap
# 1: ANY record created while unmaterialized breadcrumbs are live promotes
# the whole chain outermost-first, so marker nesting mirrors dispatch
# nesting and the incoming record parents to the innermost marker. The
# marker's own parent = the cause-stack top at materialization (the
# triggering event's record — journal's raise_event wrap pushed it).
# journal.push routes through container_diff's sweep wrap: `reactive_proc`
# is a _MARKER_KINDS member, so the entry sweep flushes pending deltas
# OUTWARD (to the event record — the graceful direction) and the close's
# pop sweep attributes the window's deltas to the marker.
# ----------------------------------------------------------------------

_materializing = False


def _materialize_crumbs(via):
    """Promote every unmaterialized breadcrumb, outermost first."""
    global _materializing
    if _materializing or not _crumbs:
        return
    _materializing = True
    try:
        for entry in list(_crumbs):
            if entry[1] is not None:
                continue
            buff = entry[0]
            owner_unit = getattr(buff, 'owner', None)
            # Shop-path staleness ⟨GATE⟩: reactions can fire outside any
            # combat action root — refresh journal._level like _open_marker.
            level = getattr(owner_unit, 'level', None)
            if level is not None:
                journal._level = level
            rec = journal.record('reactive_proc', {
                'buff': getattr(buff, 'name', None) or type(buff).__name__,
                'buff_class': type(buff).__name__,
                'recipient': (_snapshot_unit(owner_unit)
                              if owner_unit is not None else None),
                'via': via,
            })
            journal.push(rec)
            entry[1] = rec
    except Exception as e:
        _note_failure('materialize', e)
    finally:
        _materializing = False


def _make_wrapper(handler, buff):
    """The dispatch wrapper for one (handler, owning buff) registration.
    Always calls through; breadcrumb push/pop guarded; pops exactly its own
    entry (a mismatch is noted, never propagated); never swallows the
    handler's own exceptions."""
    def _reactive_dispatch(event):
        entry = None
        try:
            entry = [buff, None]
            _crumbs.append(entry)
        except Exception as e:
            _note_failure('dispatch:push', e)
            entry = None
        try:
            return handler(event)
        finally:
            if entry is not None:
                try:
                    if entry[1] is not None:
                        # Close the materialized window — the pop routes
                        # through container_diff's exit sweep (bracket =
                        # 'reactive_proc').
                        journal.pop()
                except Exception as e:
                    _note_failure('dispatch:close', e)
                try:
                    if _crumbs and _crumbs[-1] is entry:
                        _crumbs.pop()
                    else:
                        _note_failure('dispatch:pop',
                                      RuntimeError('breadcrumb stack unbalanced'))
                except Exception as e:
                    _note_failure('dispatch:pop', e)
    return _reactive_dispatch


def _install_write_taps(Level):
    """Taps 2-3: the engine's write chokepoints for the SILENT container
    classes (a pure charge refund or bonus adjust raises nothing and stays
    invisible until a sweep — the census-proven gap the record gate alone
    cannot see). Materialize BEFORE calling the original, so the write lands
    inside the marker window and the pop sweep attributes it. Empty-stack
    no-ops on the non-reactive 99% of calls."""
    def _tap(cls, name, via):
        orig = vars(cls).get(name)

        def tapped(self, *args, **kwargs):
            if _crumbs and not _materializing:
                _materialize_crumbs(via)
            return orig(self, *args, **kwargs)
        tapped.__name__ = 'tapped_' + name
        setattr(cls, name, tapped)

    _tap(Level.Spell, 'refund_charges', 'charges')
    _tap(Level.Spell, 'drain_charges', 'charges')
    _tap(Level.Unit, 'adjust_spell_bonus', 'adjust')
    _tap(Level.Unit, 'adjust_global_bonus', 'adjust')
    _tap(Level.Unit, 'adjust_tag_bonus', 'adjust')


def _install_record_gate():
    """Tap 1: wrap the journal's single record-creation gate. A record
    created during a live breadcrumb materializes the chain FIRST, so the
    record parents to the innermost marker. Recursion-guarded — the marker
    records themselves pass straight through."""
    orig_record = journal.record

    def gated_record(event_type, payload, parent_seq=_journal_module._UNSET):
        if _crumbs and not _materializing:
            _materialize_crumbs('record_gate')
        return orig_record(event_type, payload, parent_seq)

    journal.record = gated_record


# ----------------------------------------------------------------------
# The four register-function wraps (classify at subscription).
#
# Predicate: handler is a bound method whose __self__ is a Buff (Equipment
# and ChannelBuff are Buff subclasses — one mechanism). Everything else —
# UnitSprite (view layer), mutators, the mod's own module-function triggers —
# passes through untouched, register AND unregister.
# ----------------------------------------------------------------------

def _install_register_wraps(Level):
    eh_cls = Level.EventHandler
    buff_cls = Level.Buff

    orig_reg_global = eh_cls.register_global_trigger
    orig_unreg_global = eh_cls.unregister_global_trigger
    orig_reg_entity = eh_cls.register_entity_trigger
    orig_unreg_entity = eh_cls.unregister_entity_trigger

    def _owner_of(handler):
        # Bound-method check: both __self__ and __func__ must exist (the
        # census found zero non-bound-method registrations, but a future
        # lambda/partial must fall through unwrapped, not crash).
        owner = getattr(handler, '__self__', None)
        if owner is not None and isinstance(owner, buff_cls) \
                and getattr(handler, '__func__', None) is not None:
            return owner
        return None

    def _map_of(mgr, create):
        m = getattr(mgr, _MAP_ATTR, None)
        if m is None and create:
            m = {}
            setattr(mgr, _MAP_ATTR, m)
        return m

    def _wrap_register(mgr, event_type, entity, handler, owner):
        wrapper = _make_wrapper(handler, owner)
        key = (event_type, entity, handler.__func__, handler.__self__)
        _map_of(mgr, True).setdefault(key, []).append(wrapper)
        return wrapper

    def _pop_wrapper(mgr, event_type, entity, handler):
        m = _map_of(mgr, False)
        if m is None:
            return None
        key = (event_type, entity, getattr(handler, '__func__', None),
               getattr(handler, '__self__', None))
        stack = m.get(key)
        if not stack:
            return None
        wrapper = stack.pop()
        if not stack:
            del m[key]
        return wrapper

    def patched_register_global(self, event_type, handler):
        try:
            owner = _owner_of(handler)
            if owner is not None:
                handler = _wrap_register(self, event_type, None, handler, owner)
        except Exception as e:
            _note_failure('register_global', e)
        return orig_reg_global(self, event_type, handler)

    def patched_unregister_global(self, event_type, handler):
        try:
            if _owner_of(handler) is not None:
                wrapper = _pop_wrapper(self, event_type, None, handler)
                if wrapper is not None:
                    handler = wrapper
        except Exception as e:
            _note_failure('unregister_global', e)
        return orig_unreg_global(self, event_type, handler)

    def patched_register_entity(self, event_type, entity, handler):
        try:
            owner = _owner_of(handler)
            if owner is not None:
                handler = _wrap_register(self, event_type, entity, handler, owner)
        except Exception as e:
            _note_failure('register_entity', e)
        return orig_reg_entity(self, event_type, entity, handler)

    def patched_unregister_entity(self, event_type, entity, handler):
        try:
            if _owner_of(handler) is not None:
                wrapper = _pop_wrapper(self, event_type, entity, handler)
                if wrapper is not None:
                    handler = wrapper
        except Exception as e:
            _note_failure('unregister_entity', e)
        return orig_unreg_entity(self, event_type, entity, handler)

    eh_cls.register_global_trigger = patched_register_global
    eh_cls.unregister_global_trigger = patched_unregister_global
    eh_cls.register_entity_trigger = patched_register_entity
    eh_cls.unregister_entity_trigger = patched_unregister_entity


def install(log_fn=None):
    """Install the reactive-proc wraps. Returns True if installed (now or
    previously), False if any seam is missing/mis-shaped (RW2 backport, or a
    future RW3 restructure) — in which case nothing is wrapped and the mod
    runs reactive-marker-less."""
    global _installed, _log_fn
    if _installed:
        return True
    if log_fn is not None:
        _log_fn = log_fn

    try:
        import Level
    except Exception as e:
        _note_failure('install:import', e)
        return False

    eh_cls = getattr(Level, 'EventHandler', None)
    buff_cls = getattr(Level, 'Buff', None)
    seams_ok = (
        isinstance(eh_cls, type)
        and isinstance(buff_cls, type)
        # Strong form: each wrapped method defined ON EventHandler itself
        # (vars(), not getattr — the cause_markers false-pass lesson).
        and 'register_global_trigger' in vars(eh_cls)
        and 'unregister_global_trigger' in vars(eh_cls)
        and 'register_entity_trigger' in vars(eh_cls)
        and 'unregister_entity_trigger' in vars(eh_cls)
        # The subscription funnel this design leans on.
        and 'subscribe' in vars(buff_cls)
        and 'unsubscribe' in vars(buff_cls)
        # The write-tap chokepoints (taps 2-3).
        and 'refund_charges' in vars(Level.Spell)
        and 'drain_charges' in vars(Level.Spell)
        and 'adjust_spell_bonus' in vars(Level.Unit)
        and 'adjust_global_bonus' in vars(Level.Unit)
        and 'adjust_tag_bonus' in vars(Level.Unit)
    )
    if not seams_ok:
        if _log_fn:
            try:
                _log_fn("[ReactiveMarkers] install declined: register-function "
                        "seams missing/mis-shaped (RW2 or API drift)")
            except Exception:
                pass
        return False

    _install_register_wraps(Level)
    _install_record_gate()
    _install_write_taps(Level)

    _installed = True
    if _log_fn:
        try:
            _log_fn("[ReactiveMarkers] subscription wraps installed "
                    "(classify-at-subscription, breadcrumb dispatch)")
        except Exception:
            pass
    return True
