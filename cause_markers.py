# Root-2 cause-markers (Unit 2, legs 1-3): pickup / equipment-trigger / craft.
#
# The ratified Theme D shape: a journal record pushed as the live cause around
# each dispatch site the engine runs WITHOUT a cast/tick cause of its own —
# component pickups, equipment-triggered component replays, craft-time
# component effects. Everything recorded inside the window parents to the
# marker (ownership is a fact of the bracket, never inference); the marker
# itself parents to whatever cause is live at that moment (journal cause-stack
# semantics) and stands as a root only when nothing is. Self-covering across
# arrival paths: interact-key pickup -> root; Relocator Beacon's teleport ->
# child of its buff tick; Gold Drake treasure_seekers -> child of the cast;
# Blink-onto-a-pickup -> child of the Blink cast.
#
# Records-only (capture phase): nothing here voices. The marker kinds join
# both producers' known-sets so no unmodeled-channel telemetry trips.
#
# ORDERING INVARIANT (oracle precedent): the game's method ALWAYS runs, and a
# marker bookkeeping failure can only lose attribution, never touch game
# behavior — mod work is try/except-guarded on both sides of the original.
#
# Install is self-gating and field-killable: it verifies each RW3 seam shape
# before wrapping (declines cleanly otherwise) and screen_reader only calls it
# when `cause_markers_enabled` (settings.ini) is true. RW2 backport: the seam
# shapes differ -> install declines -> inert, no code surgery.

from journal import journal, _snapshot_unit


_installed = False
_log_fn = None
_failed_sites = set()       # once-per-site failure-note dedupe


def _note_failure(site, exc):
    if site in _failed_sites:
        return
    _failed_sites.add(site)
    if _log_fn:
        try:
            _log_fn("[CauseMarkers] capture failure at %s: %r" % (site, exc))
        except Exception:
            pass


# ----------------------------------------------------------------------
# The marker bracket
#
# _open_marker returns the pushed record (or None if bookkeeping failed —
# the caller then skips the pop). journal._level is refreshed from the
# in-scope level BEFORE the record is made: markers can fire outside any
# combat action root (shop confirm, interact key), where _level may be
# stale, and both record-parenting and the container-diff span sweeps
# read it.
# ----------------------------------------------------------------------

def _open_marker(kind, payload, level, site):
    try:
        if level is not None:
            journal._level = level
        rec = journal.record(kind, payload)
        journal.push(rec)
        return rec
    except Exception as e:
        _note_failure(site, e)
        return None


def _close_marker(rec, site):
    if rec is None:
        return
    try:
        journal.pop()
    except Exception as e:
        _note_failure(site + ':pop', e)


# ----------------------------------------------------------------------
# Leg 1 — item_pickup markers
#
# Five dispatch shapes game-wide (S29 census, plan doc): the three prop
# on_player_enter methods (ComponentPickup / MemoryOrb / HeartDot,
# Level.py:2795-2824) and DissolutionShop.collect_component
# (LevelRewards.py:270-279 — forgotten-spell components that found no
# floor space). The fifth on_pickup dispatch site (Gold Drake's minion
# replay, Spells.py:7114) carries no item_pickup moment — it is covered
# at the component grain (build step 3) under its cast.
# ----------------------------------------------------------------------

def _pickup_payload(item_kind, name, component, recipient):
    payload = {
        'item': name,
        'item_kind': item_kind,
        'recipient': _snapshot_unit(recipient),
    }
    if component is not None:
        payload['component'] = type(component).__name__
    return payload


def _wrap_prop_enter(cls, item_kind, get_component):
    original = cls.on_player_enter
    site = 'item_pickup:%s' % cls.__name__

    def patched_on_player_enter(self, player):
        payload = None
        try:
            payload = _pickup_payload(
                item_kind, getattr(self, 'name', item_kind),
                get_component(self), player)
        except Exception as e:
            _note_failure(site, e)
        rec = (_open_marker('item_pickup', payload,
                            getattr(self, 'level', None), site)
               if payload is not None else None)
        try:
            return original(self, player)
        finally:
            _close_marker(rec, site)

    cls.on_player_enter = patched_on_player_enter


def _wrap_collect_component(cls):
    original = cls.collect_component
    site = 'item_pickup:collect_component'

    def patched_collect_component(self, player, component):
        payload = None
        try:
            payload = _pickup_payload(
                'shrine_grant', getattr(component, 'name', 'component'),
                component, player)
        except Exception as e:
            _note_failure(site, e)
        rec = (_open_marker('item_pickup', payload,
                            getattr(player, 'level', None), site)
               if payload is not None else None)
        try:
            return original(self, player, component)
        finally:
            _close_marker(rec, site)

    cls.collect_component = patched_collect_component


# ----------------------------------------------------------------------
# Install — self-gating, idempotent, separable
# ----------------------------------------------------------------------

def install(log_fn=None):
    """Install the Root-2 cause-marker wraps. Returns True if installed (now
    or previously), False if any seam is missing/mis-shaped (RW2 backport, or
    a future RW3 restructure) — in which case nothing is wrapped and the mod
    runs marker-less.
    """
    global _installed, _log_fn
    if _installed:
        return True
    if log_fn is not None:
        _log_fn = log_fn

    try:
        import Level
        import LevelRewards
    except Exception as e:
        _note_failure('install:import', e)
        return False

    # Seam checks — every wrapped method must be defined ON its class (the
    # Prop base carries a no-op on_player_enter at Level.py:2669, so a
    # plain getattr would false-pass after API drift removed the specific
    # override), and the Component base must carry both no-op hooks (the
    # own-__dict__ wrap predicate in later steps depends on the base
    # identities).
    pickup_seams = (
        'on_player_enter' in vars(Level.ComponentPickup)
        and 'on_player_enter' in vars(Level.MemoryOrb)
        and 'on_player_enter' in vars(Level.HeartDot)
        and 'collect_component' in vars(LevelRewards.DissolutionShop)
        and callable(getattr(Level.Component, 'on_pickup', None))
        and callable(getattr(Level.Component, 'on_craft', None))
    )
    if not pickup_seams:
        if _log_fn:
            try:
                _log_fn("[CauseMarkers] install declined: pickup seams "
                        "missing/mis-shaped (RW2 or API drift)")
            except Exception:
                pass
        return False

    _wrap_prop_enter(Level.ComponentPickup, 'component',
                     lambda prop: getattr(prop, 'component', None))
    _wrap_prop_enter(Level.MemoryOrb, 'memory_orb', lambda prop: None)
    _wrap_prop_enter(Level.HeartDot, 'ruby_heart', lambda prop: None)
    _wrap_collect_component(LevelRewards.DissolutionShop)

    _installed = True
    if _log_fn:
        try:
            _log_fn("[CauseMarkers] pickup markers installed "
                    "(4 dispatch sites)")
        except Exception:
            pass
    return True
