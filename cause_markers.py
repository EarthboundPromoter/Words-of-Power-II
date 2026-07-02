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
# Leg 2 — equipment_trigger markers
#
# The stored-component replay, two dispatch shapes (Equipment.py
# 6563-6591): the base trigger_component_on_pickups loop (Rod fires it
# from an EventOnSpellCast when its tag requirements fill — its own
# override calls super(), so wrapping the BASE covers it) and Chalice's
# on_sp_pickup (ONE random stored component, chosen inside the method —
# per-component identity arrives at the component grain, step 3). The
# game's "{equipment} triggered {component}" log line stays what the
# ledger demoted it to: validation + phrasing, never the detector.
# ----------------------------------------------------------------------

def _wrap_trigger_replay(cls):
    original = cls.trigger_component_on_pickups
    site = 'equipment_trigger:replay_all'

    def patched_trigger_component_on_pickups(self, player):
        payload = None
        try:
            payload = {'equipment': getattr(self, 'name', 'equipment'),
                       'mode': 'all'}
        except Exception as e:
            _note_failure(site, e)
        rec = (_open_marker('equipment_trigger', payload,
                            getattr(player, 'level', None), site)
               if payload is not None else None)
        try:
            return original(self, player)
        finally:
            _close_marker(rec, site)

    cls.trigger_component_on_pickups = patched_trigger_component_on_pickups


def _wrap_chalice_pickup(cls):
    original = cls.on_sp_pickup
    site = 'equipment_trigger:chalice'

    def patched_on_sp_pickup(self, evt):
        payload = None
        try:
            payload = {'equipment': getattr(self, 'name', 'equipment'),
                       'mode': 'random'}
        except Exception as e:
            _note_failure(site, e)
        owner = getattr(self, 'owner', None)
        rec = (_open_marker('equipment_trigger', payload,
                            getattr(owner, 'level', None), site)
               if payload is not None else None)
        try:
            return original(self, evt)
        finally:
            _close_marker(rec, site)

    cls.on_sp_pickup = patched_on_sp_pickup


# ----------------------------------------------------------------------
# Leg 3 — craft markers (G-Q, escapees-only size)
#
# The craft node is ALWAYS recorded — "crafted X from A, B, C" — at both
# sites: Game.try_shop's Equipment branch (Game.py:563-572; only
# reachable via confirm_buy, which asserts success, so a marker on a
# declined craft is unreachable in-game) and the minion-copy re-craft
# (Level.py:2063-2069, re-runs every on_craft mid-combat inside a summon
# cast -> the marker nests under that cast). Equipment internals
# (stamps) are write-once query-layer state — never captured (G-Q).
# Ingredient names: display names at the shop site (instances in hand,
# game.shop_craft_component_ingredients set by confirm_buy just before);
# class names at the minion site (crafting_input_fns holds constructors
# — instantiating them just for labels would run content code).
# ----------------------------------------------------------------------

def _wrap_try_shop(game_cls, equipment_cls):
    original = game_cls.try_shop
    site = 'craft:shop'

    def patched_try_shop(self, item):
        rec = None
        if isinstance(item, equipment_cls):
            payload = None
            try:
                ingredients = [
                    getattr(c, 'name', type(c).__name__)
                    for c in getattr(self, 'shop_craft_component_ingredients',
                                     ())]
                payload = {'equipment': getattr(item, 'name', 'equipment'),
                           'ingredients': ingredients,
                           'recipient': _snapshot_unit(self.p1),
                           'site': 'shop'}
            except Exception as e:
                _note_failure(site, e)
            if payload is not None:
                rec = _open_marker('craft', payload,
                                   getattr(self, 'cur_level', None), site)
        try:
            return original(self, item)
        finally:
            _close_marker(rec, site)

    game_cls.try_shop = patched_try_shop


def _wrap_minion_copy(unit_cls):
    original = unit_cls.grant_equipment_copy_to_minion
    site = 'craft:minion_copy'

    def patched_grant_equipment_copy_to_minion(self, recipient, equipment):
        payload = None
        try:
            ingredients = []
            fns = getattr(equipment, 'crafting_input_fns', None) or {}
            for c_type, count in fns.items():
                ingredients.extend([c_type.__name__] * count)
            payload = {'equipment': getattr(equipment, 'name', 'equipment'),
                       'ingredients': ingredients,
                       'recipient': _snapshot_unit(recipient),
                       'site': 'minion_copy'}
        except Exception as e:
            _note_failure(site, e)
        rec = (_open_marker('craft', payload,
                            getattr(self, 'level', None), site)
               if payload is not None else None)
        try:
            return original(self, recipient, equipment)
        finally:
            _close_marker(rec, site)

    unit_cls.grant_equipment_copy_to_minion = patched_grant_equipment_copy_to_minion


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
        import Equipment
        import Game
    except Exception as e:
        _note_failure('install:import', e)
        return False

    # Seam checks — every wrapped method must be defined ON its class (the
    # Prop base carries a no-op on_player_enter at Level.py:2669, so a
    # plain getattr would false-pass after API drift removed the specific
    # override), and the Component base must carry both no-op hooks (the
    # own-__dict__ wrap predicate in later steps depends on the base
    # identities).
    seams_ok = (
        'on_player_enter' in vars(Level.ComponentPickup)
        and 'on_player_enter' in vars(Level.MemoryOrb)
        and 'on_player_enter' in vars(Level.HeartDot)
        and 'collect_component' in vars(LevelRewards.DissolutionShop)
        and callable(getattr(Level.Component, 'on_pickup', None))
        and callable(getattr(Level.Component, 'on_craft', None))
        # Leg 2/3 seams
        and 'trigger_component_on_pickups' in vars(
            Equipment.OnPickupTriggerEquipment)
        and 'on_sp_pickup' in vars(Equipment.ArtificersChalice)
        and 'try_shop' in vars(Game.Game)
        and 'grant_equipment_copy_to_minion' in vars(Level.Unit)
        and isinstance(getattr(Level, 'Equipment', None), type)
    )
    if not seams_ok:
        if _log_fn:
            try:
                _log_fn("[CauseMarkers] install declined: marker seams "
                        "missing/mis-shaped (RW2 or API drift)")
            except Exception:
                pass
        return False

    _wrap_prop_enter(Level.ComponentPickup, 'component',
                     lambda prop: getattr(prop, 'component', None))
    _wrap_prop_enter(Level.MemoryOrb, 'memory_orb', lambda prop: None)
    _wrap_prop_enter(Level.HeartDot, 'ruby_heart', lambda prop: None)
    _wrap_collect_component(LevelRewards.DissolutionShop)
    _wrap_trigger_replay(Equipment.OnPickupTriggerEquipment)
    _wrap_chalice_pickup(Equipment.ArtificersChalice)
    _wrap_try_shop(Game.Game, Level.Equipment)
    _wrap_minion_copy(Level.Unit)

    _installed = True
    if _log_fn:
        try:
            _log_fn("[CauseMarkers] markers installed: pickup (4 sites), "
                    "equipment-trigger (2), craft (2)")
        except Exception:
            pass
    return True
