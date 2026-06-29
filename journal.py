"""
Journal — capture stage for the new data-model pipeline (phase 2).

Captures every game event flowing through Level.act_cast and EventHandler.raise_event
into structured records with monotonic sequence + causation parent links. The journal
is module-level (never pickled), bounded by level transitions (reset on each new level),
and feeds phase 3+ consumers (direct-action digest, death summary, level-end summary).

See mods/screen_reader/memory_pointers in MEMORY.md:
- design_rw2_data_model.md — phase 0+1 design spec
- plan_data_model_overhaul.md — multi-phase strangler-fig plan

Per-event payload builders live below. Each builder snapshots the fields the
digest spec needs at event-fire time (positions, hp_after, stack_count_after,
identity flags, derived tier). Unit and buff references are not stored —
only their snapshotted state — so payloads stay pickle-clean and survive
unit death between event fire and consumer read.

Schema is NOT backwards compatible with the auto-_to_payload era; pre-0.4.0
journal_debug.log files cannot be replayed against the new shape.
"""

import json
import os
import time

import Level


class _Journal:
    def __init__(self):
        self.records = []
        self.cause_stack = []
        self.sequence = 0
        self.action_chain_id = 0
        self.level_id = None
        self._fp = None
        self._hooks_installed = False

    def reset(self, level_id):
        self.records = []
        self.cause_stack = []
        self.level_id = level_id
        if self._fp:
            self._emit({"__meta__": "level_reset", "level_id": level_id, "seq": self.sequence})

    def open_log(self, path):
        try:
            self._fp = open(path, "w", encoding="utf-8")
            self._emit({"__meta__": "journal_log_opened", "ts": time.time()})
        except Exception:
            self._fp = None

    def close_log(self):
        if self._fp:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None

    def push(self, record):
        self.cause_stack.append(record)

    def pop(self):
        if self.cause_stack:
            self.cause_stack.pop()

    def record(self, event_type, payload):
        self.sequence += 1
        parent = self.cause_stack[-1]["sequence"] if self.cause_stack else None
        rec = {
            "sequence": self.sequence,
            "action_chain_id": self.action_chain_id,
            "level_id": self.level_id,
            "event_type": event_type,
            "parent": parent,
            "timestamp": time.time(),
            "payload": payload,
            "marks": [],
        }
        self.records.append(rec)
        if self._fp:
            self._emit(rec)
        return rec

    def begin_chain(self, payload):
        self.action_chain_id += 1
        return self.record("cast_begin", payload)

    def _emit(self, obj):
        try:
            self._fp.write(json.dumps(obj, default=str, separators=(",", ":")) + "\n")
            self._fp.flush()
        except Exception:
            pass


journal = _Journal()


# ----------------------------------------------------------------------
# Snapshot helpers
#
# Each helper captures the fields a downstream consumer is known to need.
# Live object references are NEVER stored — units may die between event
# fire and consumer read. Snapshot dicts are plain primitives only,
# pickle-clean and safe across save/load.
#
# Instance identity is Python id(obj). Stable for the lifetime of the
# object, sufficient for within-level equivalence-class composition
# (the journal resets on level transition, so cross-level id collision
# is irrelevant). Not human-readable in logs but cheap and unambiguous.
# ----------------------------------------------------------------------


def _classify_tier(unit):
    """Return the player-facing tier label for a unit.

    Vocabulary: "wizard" / "spawner" / "boss" / "minion". The phrasing
    spec mentions "elite" but no game flag distinguishes elites from
    other units today — collapsed into "boss" or "minion" by their
    underlying flags. If real play surfaces a useful elite signal we
    re-derive in the composer; raw flags are also captured so the
    split is cheap to add.
    """
    if getattr(unit, 'is_player_controlled', False):
        return 'wizard'
    if getattr(unit, 'is_lair', False):
        return 'spawner'
    if getattr(unit, 'is_boss', False):
        return 'boss'
    return 'minion'


def _can_see_wizard(unit):
    """Whether the wizard can see this unit, captured at event-fire time for
    the orphan composer's in-LoS / out-of-LoS ordering. Capture-time (not
    render-time) so the value reflects the moment the unit acted — a unit that
    fired while visible then slipped away still reads as in-sight for that
    action. Returns None when undeterminable (no level/player yet); the
    composer treats None as out-of-sight for ordering."""
    level = getattr(unit, 'level', None)
    if level is None:
        return None
    player = getattr(level, 'player_unit', None)
    if player is None:
        return None
    try:
        if unit is player:
            return True
        return bool(level.can_see(player.x, player.y, unit.x, unit.y))
    except Exception:
        return None


def _snapshot_unit(unit):
    """Snapshot the fields downstream consumers need from a Unit.

    Returns a dict (never None) — if the input is missing or weirdly
    shaped, returns a minimal shell so consumer code doesn't have to
    null-check every field access.
    """
    if unit is None:
        return {'id': None, 'name': None}
    parent = getattr(unit, 'parent', None)
    return {
        'id': id(unit),
        'name': getattr(unit, 'name', None),
        'x': getattr(unit, 'x', None),
        'y': getattr(unit, 'y', None),
        'cur_hp': getattr(unit, 'cur_hp', None),
        'max_hp': getattr(unit, 'max_hp', None),
        'shields': getattr(unit, 'shields', None),
        'team': getattr(unit, 'team', None),
        'tier': _classify_tier(unit),
        'is_player_controlled': bool(getattr(unit, 'is_player_controlled', False)),
        'is_boss': bool(getattr(unit, 'is_boss', False)),
        'is_lair': bool(getattr(unit, 'is_lair', False)),
        'parent_id': id(parent) if parent is not None else None,
        'can_see_wizard': _can_see_wizard(unit),
    }


def _buff_agency(buff):
    """Classify a buff's agency impact for the crisis cadence (Model A).

    'control' = stops or limits the owner's turn — any `Stun` subclass,
    which covers Stun, Petrify, Glassed, Frozen, Sleep, Fear (the
    is_stunned family, Level.py:2165). 'silence' = blocks casting only.
    None = no agency impact. Read at capture time via isinstance off the
    live buff, since the snapshot stores only primitives downstream."""
    stun_cls = getattr(Level, 'Stun', None)
    if stun_cls is not None and isinstance(buff, stun_cls):
        return 'control'
    silence_cls = getattr(Level, 'Silence', None)
    if silence_cls is not None and isinstance(buff, silence_cls):
        return 'silence'
    return None


def _buff_resist_penalties(buff):
    """For a buff that lowers resistances, capture the OWNER's *effective*
    resist total for each damage type the buff reduces.

    Buff.apply folds a buff's resists into the owner additively
    (owner.resists[dtype] += resist, Level.py:1147), so by the time
    EventOnBuffApply fires the owner's resists reflect this buff's
    contribution. Reading owner.resists here yields the same value the game
    shows on the character sheet — which is what the crisis class-4 read
    speaks when a stacking penalty (e.g. Melted Armor, -10 Physical/stack)
    deepens the wizard's vulnerability with no damage tick to reveal it.

    Returns {dtype_name: effective_resist} for types the buff modifies
    NEGATIVELY, or {} for buffs that don't lower resistances (the common
    case — cheap empty-defaultdict short-circuit)."""
    own = getattr(buff, 'resists', None)
    owner = getattr(buff, 'owner', None)
    if not own or owner is None:
        return {}
    owner_res = getattr(owner, 'resists', None)
    if owner_res is None:
        return {}
    out = {}
    try:
        for dtype, mod in own.items():
            if mod < 0:
                name = getattr(dtype, 'name', None) or str(dtype)
                out[name] = owner_res.get(dtype, 0)
    except Exception:
        return {}
    return out


def _buff_source_caster(buff):
    """Name the unit that applied this buff, IF the effect happened to set a
    `source` back-reference on it.

    NOTE: there is no general `Buff.source` field in RW3 — the base Buff does
    not define one, and a source scan shows `.source` is set almost entirely
    on units/clouds (summon origin) rather than on debuffs applied to a
    victim. So for the vast majority of enemy debuffs this returns None and
    the crisis producer relies on its chain-walk fallback (the actual live
    path), then anonymous. This hook is kept for the rare effect that DOES
    set `buff.source` (e.g. a player equipment buff) — there it yields the
    applier directly and is immune to the deferred-cast chain gap. Resolves
    `.caster` (a Spell) then `.owner` (a Buff/Item); never names the bearer
    as its own applier (self-buffs)."""
    src = getattr(buff, 'source', None)
    if src is None:
        return None
    caster = getattr(src, 'caster', None)
    if caster is None:
        caster = getattr(src, 'owner', None)
    name = _name_or(caster)
    # Don't attribute a buff to the unit it sits on (self-applied).
    owner = getattr(buff, 'owner', None)
    if name is not None and owner is not None and name == _name_or(owner):
        return None
    return name


def _snapshot_buff(buff):
    """Snapshot the fields downstream consumers need from a Buff.

    `buff_type` distinguishes bless (1) from curse / debuff (2) from
    passive (0) and item (3). The digest's debuff/buff split sub-sections
    read this to filter and route applies appropriately. `agency` feeds
    the crisis Model-A cadence (per-turn countdown for control debuffs).
    `resist_penalty` feeds the crisis class-4 read (effective resist total
    when a debuff lowers the owner's resistances). `source_caster` feeds the
    crisis B4 non-damage attribution (who applied this debuff), when the
    game set buff.source."""
    if buff is None:
        return {'id': None, 'name': None, 'agency': None, 'source_caster': None}
    return {
        'id': id(buff),
        'name': getattr(buff, 'name', None),
        'turns_left': getattr(buff, 'turns_left', None),
        'stack_type': getattr(buff, 'stack_type', None),
        'buff_type': getattr(buff, 'buff_type', None),
        'agency': _buff_agency(buff),
        'resist_penalty': _buff_resist_penalties(buff),
        'source_caster': _buff_source_caster(buff),
    }


def _snapshot_spell(spell):
    """Snapshot the fields downstream consumers need from a Spell.

    `melee` distinguishes melee attacks (spell.melee=True) from real spell
    casts. The orphan composer's render branch reads it: melee gets the
    "Aelf hit Wizard, 4 Physical" form (no spell name in the line) while
    casts get the "Aelf cast Lightning Bolt at Wizard, 6 Lightning" form.
    """
    if spell is None:
        return {'name': None, 'cur_charges': None, 'max_charges': None,
                'melee': False}
    return {
        'name': getattr(spell, 'name', None),
        'cur_charges': getattr(spell, 'cur_charges', None),
        'max_charges': getattr(spell, 'max_charges', None),
        'melee': bool(getattr(spell, 'melee', False)),
    }


def _snapshot_item(item):
    """Snapshot the fields downstream consumers need from an Item."""
    if item is None:
        return {'name': None}
    return {'name': getattr(item, 'name', None)}


def _name_or(value, fallback=None):
    """Pull .name off a referenced game object, falling back to a literal."""
    if value is None:
        return fallback
    name = getattr(value, 'name', None)
    return name if isinstance(name, str) else fallback


def _source_attribution(source):
    """Capture the fields a renderer needs to reproduce the game's own
    attribution branch (Level.deal_damage, Level.py:4044/4064): the
    acting unit's name and whether the source is a temp buff (bless/curse)
    vs equipment vs a spell.

    The game renders an effect ACTIVELY — "{owner} deals N to {target}
    with {source}" — when `source.owner` is set AND the source is not a
    BLESS/CURSE buff; otherwise PASSIVELY — "{target} takes N from
    {source}". Equipment (BUFF_TYPE_ITEM) is NOT a temp buff, so a gear
    hit reads actively with the wizard as owner and the item as source.

    Read off the live source object at event-fire time; only primitives
    are stored. `source_name` itself is captured separately by each
    builder and remains the grouping/dedup key — these fields are
    display-layer only (never used as collapse keys)."""
    if source is None:
        return {
            'source_owner_name': None,
            'source_is_buff': False,
            'source_buff_type': None,
        }
    return {
        'source_owner_name': _name_or(getattr(source, 'owner', None)),
        'source_is_buff': isinstance(source, Level.Buff),
        'source_buff_type': getattr(source, 'buff_type', None),
    }


def _stack_count_for(unit, buff):
    """Count buffs on `unit` matching the same name as `buff` — used as
    'stack count after' on EventOnBuffApply / EventOnBuffRemove. The game
    raises BuffApply after appending to unit.buffs and BuffRemove after
    removing, so reading at event-fire time yields the post-state count.
    """
    if unit is None or buff is None:
        return None
    target_name = getattr(buff, 'name', None)
    if target_name is None:
        return None
    try:
        return sum(1 for b in unit.buffs if getattr(b, 'name', None) == target_name)
    except Exception:
        return None


# ----------------------------------------------------------------------
# Per-event payload builders
#
# Each function takes a game Event namedtuple and returns a dict.
# Field choices justified by the digest phrasing spec
# (memory/design_digest_phrasing.md) — the digest is the first consumer
# and drives capture.
# ----------------------------------------------------------------------


def _payload_damaged(event):
    """EventOnDamaged(unit, damage, damage_type, source).
    `damage` is post-resist post-cap (the actually-dealt amount; capped
    at remaining HP for killing blows). For survivor lines the digest
    can use this directly; for kill lines the killing-blow value is
    truncated and the full hit appears on the preceding PreDamaged.

    `source_turns_left` is captured when the source is a Buff (DOTs like
    Poisoned, Burning, Bleeding tick through buff.on_advance). The orphan
    composer's status-tick section uses this to render
    "Goblin (3,4) Poisoned: 1 Poison, 3 turns left." None for non-buff
    sources (spells, equipment proximate damage, etc.)."""
    source = event.source
    return {
        'target': _snapshot_unit(event.unit),
        'damage': event.damage,
        'damage_type': _name_or(event.damage_type),
        'source_name': _name_or(source),
        'source_unit_id': id(source.owner) if getattr(source, 'owner', None) is not None else None,
        'source_turns_left': getattr(source, 'turns_left', None) if source is not None else None,
        **_source_attribution(source),
    }


def _payload_pre_damaged(event):
    """EventOnPreDamaged(unit, damage, unresisted_damage, damage_type, source).

    Despite the field names, in the actual code (Level.py:4179) the
    constructor is called as EventOnPreDamaged(unit, orig_amount, amount, ...)
    where orig_amount is PRE-resist and amount is POST-resist. So:
        event.damage           = pre-resist amount (the spec damage)
        event.unresisted_damage = post-resist amount (what will hit HP)
    Resisted is derivable: damage > unresisted_damage.

    `target_resist_pct` is the unit's resistance to this damage type,
    post-cap-at-100 to match the game's effective-resist clamp at
    Level.py:4173. The digest and orphan/equipment composers use this to
    render "immune" instead of "resisted" when resist_pct >= 100 — the
    distinction matters because the game hard-caps resistance at 100
    (no heal-from-overresist behavior), so anything >= 100 is true
    immunity to that type. Negative resists pass through unclamped so
    callers can derive vulnerability by comparing pre vs post."""
    target_resist_pct = None
    try:
        if event.unit is not None and event.damage_type is not None:
            raw_resist = event.unit.resists.get(event.damage_type, 0)
            target_resist_pct = min(raw_resist, 100)
    except Exception:
        target_resist_pct = None
    return {
        'target': _snapshot_unit(event.unit),
        'damage_pre_resist': event.damage,
        'damage_post_resist': event.unresisted_damage,
        'resisted': event.damage > event.unresisted_damage,
        'damage_type': _name_or(event.damage_type),
        'source_name': _name_or(event.source),
        'source_unit_id': id(event.source.owner) if getattr(event.source, 'owner', None) is not None else None,
        'target_resist_pct': target_resist_pct,
        **_source_attribution(event.source),
    }


def _payload_death(event):
    """EventOnDeath(unit, damage_event). Inline the killing damage details
    rather than capturing the live damage_event reference (which would
    keep a Unit object alive past death and not pickle-clean)."""
    dmg = event.damage_event
    return {
        'target': _snapshot_unit(event.unit),
        'killing_damage': getattr(dmg, 'damage', None),
        'killing_dtype': _name_or(getattr(dmg, 'damage_type', None)),
        'killing_source': _name_or(getattr(dmg, 'source', None)),
    }


def _payload_healed(event):
    """EventOnHealed(unit, heal, source). Game stores heal as negative
    amount (healing flows through deal_damage with amount<0, see
    Level.py:4239). Normalize to positive magnitude here so composers
    don't need to know the sign convention."""
    raw = event.heal
    return {
        'target': _snapshot_unit(event.unit),
        'heal_amount': abs(raw) if raw is not None else None,
        'source_name': _name_or(event.source),
        **_source_attribution(event.source),
    }


def _payload_buff_apply(event):
    """EventOnBuffApply(buff, unit). Note: STACK_NONE and STACK_DURATION
    refreshes (re-applying an active buff) do NOT raise this event —
    Level.apply_buff returns early. The digest's "Buff refreshed" line
    requires a follow-up Level patch; defer."""
    return {
        'target': _snapshot_unit(event.unit),
        'buff': _snapshot_buff(event.buff),
        'stack_count_after': _stack_count_for(event.unit, event.buff),
    }


def _payload_buff_remove(event):
    return {
        'target': _snapshot_unit(event.unit),
        'buff': _snapshot_buff(event.buff),
        'stack_count_after': _stack_count_for(event.unit, event.buff),
    }


def _payload_shield_removed(event):
    """EventOnShieldRemoved(unit). Counted by the digest to compose
    'absorbed by N shields' on shielded targets."""
    return {'target': _snapshot_unit(event.unit)}


def _payload_unfrozen(event):
    """EventOnUnfrozen(unit, dtype). Status-state-exit, surfaces the
    'Frozen broken' inline clause per phrasing spec."""
    return {
        'target': _snapshot_unit(event.unit),
        'damage_type': _name_or(event.dtype),
    }


def _payload_spell_cast(event):
    """EventOnSpellCast(spell, caster, x, y, pay_costs). Fires AFTER
    act_cast queues the spell and AFTER charges are decremented, so
    spell.cur_charges here is post-cost."""
    return {
        'caster': _snapshot_unit(event.caster),
        'spell': _snapshot_spell(event.spell),
        'target_x': event.x,
        'target_y': event.y,
        'pay_costs': bool(event.pay_costs),
    }


def _payload_moved(event):
    """EventOnMoved(unit, x, y, teleport). x/y is destination."""
    snap = _snapshot_unit(event.unit)
    # Override with destination — snapshot was taken post-move so x/y
    # already reflect destination, but make the intent explicit.
    snap['x'] = event.x
    snap['y'] = event.y
    return {
        'unit': snap,
        'teleport': bool(event.teleport),
    }


def _payload_unit_added(event):
    return {'unit': _snapshot_unit(event.unit)}


def _payload_pass(event):
    return {'unit': _snapshot_unit(event.unit)}


def _payload_item_pickup(event):
    return {'item': _snapshot_item(event.item)}


def _payload_item_used(event):
    return {
        'user': _snapshot_unit(event.unit),
        'item': _snapshot_item(event.item),
    }


def _payload_prop_enter(event):
    return {
        'unit': _snapshot_unit(event.unit),
        'prop_name': _name_or(event.prop),
    }


def _payload_spend_hp(event):
    return {
        'unit': _snapshot_unit(event.unit),
        'hp': event.hp,
    }


def _payload_level_complete(event):
    return {'level_id': id(event.level)}


def _payload_reroll(event):
    return {'level_id': id(event.level)}


EVENT_PAYLOAD_BUILDERS = {
    'EventOnDamaged': _payload_damaged,
    'EventOnPreDamaged': _payload_pre_damaged,
    'EventOnDeath': _payload_death,
    'EventOnHealed': _payload_healed,
    'EventOnBuffApply': _payload_buff_apply,
    'EventOnBuffRemove': _payload_buff_remove,
    'EventOnShieldRemoved': _payload_shield_removed,
    'EventOnUnfrozen': _payload_unfrozen,
    'EventOnSpellCast': _payload_spell_cast,
    'EventOnMoved': _payload_moved,
    'EventOnUnitAdded': _payload_unit_added,
    'EventOnUnitPreAdded': _payload_unit_added,
    'EventOnPass': _payload_pass,
    'EventOnItemPickup': _payload_item_pickup,
    'EventOnItemUsed': _payload_item_used,
    'EventOnPropEnter': _payload_prop_enter,
    'EventOnSpendHP': _payload_spend_hp,
    'EventOnLevelComplete': _payload_level_complete,
    'EventOnReroll': _payload_reroll,
}


def _to_payload(event):
    """Dispatch on event class name; fall back to generic field-iteration
    capture for event types we haven't yet enumerated.

    The fallback keeps the journal forward-compatible with new event
    types added by the game or mods — they're captured (with whatever
    field names fall out of _serialize) and become a flag for "should
    we add a builder for this?" when they appear in the journal log.
    """
    builder = EVENT_PAYLOAD_BUILDERS.get(type(event).__name__)
    if builder:
        try:
            return builder(event)
        except Exception as e:
            return {'_capture_error': repr(e)[:200], '_event_type': type(event).__name__}
    if not hasattr(event, "_fields"):
        return {"_raw": repr(event)[:200]}
    return {f: _serialize(getattr(event, f)) for f in event._fields}


def _serialize(value):
    """Generic fallback serializer for un-builder'd event types."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return repr(value)[:200]


def _wrap_with_cause(inner_gen, cause_record):
    """Wrap a spell-cast generator so the given cause is on the cause stack
    during each iteration step. Spell effects (damage, heals, summons) that
    fire from inside next(inner_gen) inherit cause_record as their parent."""
    try:
        while True:
            journal.push(cause_record)
            try:
                value = next(inner_gen)
            except StopIteration:
                return
            finally:
                journal.pop()
            yield value
    except GeneratorExit:
        return


def install_hooks():
    """Monkeypatch Level.act_cast, Level.queue_spell, EventHandler.raise_event,
    and ChannelBuff.on_advance to populate the journal. Idempotent — safe to
    call multiple times.

    The ChannelBuff hook synthesizes a cast_begin record per channel
    continuation tick. Channels bypass act_cast (ChannelBuff.on_advance
    calls queue_spell directly) so without this hook each turn's channel
    effects would parent to None — invisible to the digest's keypress
    chain. The synthesized record reflects ground truth: every field comes
    from the buff's owner and spell at the moment the continuation fires.
    """
    if journal._hooks_installed:
        return

    original_act_cast = Level.Level.act_cast
    original_queue_spell = Level.Level.queue_spell
    original_raise_event = Level.EventHandler.raise_event
    original_channel_advance = Level.ChannelBuff.on_advance
    original_add_obj = Level.Level.add_obj
    original_remove_obj = Level.Level.remove_obj
    original_unit_kill = Level.Unit.kill
    original_unit_equip = Level.Unit.equip
    original_unit_apply_buff = Level.Unit.apply_buff
    original_buff_advance = Level.Buff.advance
    original_cloud_advance = Level.Cloud.advance

    def patched_act_cast(self, unit, spell, x, y, pay_costs=True, queue=True, **cast_kwargs):
        # RW3: act_cast replaced the explicit is_echo param with **cast_kwargs and
        # forwards them straight into spell.cast() — so we must NOT inject is_echo
        # (spells don't accept it). Read it from cast_kwargs for the journal and
        # forward cast_kwargs untouched.
        is_echo = cast_kwargs.get('is_echo', False)
        # cast_begin fires BEFORE original_act_cast runs, so the spell
        # snapshot here captures pre-cost charges. EventOnSpellCast
        # (raised inside act_cast after queueing) captures post-cost.
        # pay_costs=False indicates a passive / proc / auto-cast — used by
        # consumers (e.g., the digest) to distinguish real player keypresses
        # from passive end-of-turn auto-fires (Explosive Spore Manual,
        # similar amulets) and from internal recursive recasts.
        cast_record = journal.begin_chain({
            'caster': _snapshot_unit(unit),
            'spell': _snapshot_spell(spell),
            'target_x': x,
            'target_y': y,
            'is_echo': bool(is_echo),
            'is_player': bool(getattr(unit, "is_player_controlled", False)),
            'pay_costs': bool(pay_costs),
        })
        journal.push(cast_record)
        try:
            return original_act_cast(self, unit, spell, x, y,
                                     pay_costs=pay_costs, queue=queue, **cast_kwargs)
        finally:
            journal.pop()

    def patched_queue_spell(self, gen):
        cause = journal.cause_stack[-1] if journal.cause_stack else None
        if cause is not None:
            gen = _wrap_with_cause(gen, cause)
        return original_queue_spell(self, gen)

    def patched_raise_event(self, event, entity=None):
        rec = journal.record(type(event).__name__, _to_payload(event))
        journal.push(rec)
        try:
            return original_raise_event(self, event, entity)
        finally:
            journal.pop()

    def patched_channel_advance(self):
        # Predict whether this channel will fire its spell this turn,
        # replicating the conditions in the original on_advance:
        #   - self.passed must be True (player passed turn while channeling)
        #   - AND either: not cast_after_channel (fires every turn), OR
        #                 cast_after_channel and reached max_channel
        # The original on_advance increments self.channel_turns first, then
        # checks ==max_channel — so our pre-check uses channel_turns + 1.
        will_fire = bool(getattr(self, 'passed', False)) and (
            not getattr(self, 'cast_after_channel', False)
            or getattr(self, 'channel_turns', 0) + 1 == getattr(self, 'max_channel', 0)
        )

        if not will_fire:
            return original_channel_advance(self)

        # Synthesize a cast_begin record reflecting the continuation. Every
        # field traces to ground-truth fields on the buff at fire time —
        # this captures a real game event that bypasses our act_cast hook
        # because ChannelBuff uses queue_spell directly. is_channel_continuation
        # is the verb-dispatch flag for compose_cast_section.
        target = getattr(self, 'spell_target', None)
        cast_record = journal.begin_chain({
            'caster': _snapshot_unit(self.owner),
            'spell': _snapshot_spell(self.spell),
            'target_x': getattr(target, 'x', None),
            'target_y': getattr(target, 'y', None),
            'is_echo': False,
            'is_player': bool(getattr(self.owner, 'is_player_controlled', False)),
            'pay_costs': True,
            'is_channel_continuation': True,
        })
        journal.push(cast_record)
        try:
            return original_channel_advance(self)
        finally:
            journal.pop()

    def patched_add_obj(self, obj, x, y):
        # Game's add_obj at Level.py:4023-4030 calls buff.apply(obj) directly
        # (not via unit.apply_buff) for buffs that arrive on a unit at add
        # time — pre-existing buffs on summoned units. That direct path
        # bypasses the EventOnBuffApply raise. Without synthesis, "Wolf
        # with Pack Tactics" and similar can't be surfaced. Snapshot
        # which buffs were already applied before add, then synthesize a
        # record for any that flipped to applied during it.
        is_unit = isinstance(obj, Level.Unit)
        pre_applied = set()
        if is_unit:
            for buff in obj.buffs:
                if getattr(buff, 'applied', False):
                    pre_applied.add(id(buff))
        result = original_add_obj(self, obj, x, y)
        if is_unit:
            for buff in obj.buffs:
                if not getattr(buff, 'applied', False):
                    continue
                if id(buff) in pre_applied:
                    continue
                journal.record('EventOnBuffApply', {
                    'target': _snapshot_unit(obj),
                    'buff': _snapshot_buff(buff),
                    'stack_count_after': _stack_count_for(obj, buff),
                    'is_silent_activate': True,
                })
        return result

    def patched_remove_obj(self, obj):
        # Game's remove_obj at Level.py:4058-4060 calls buff.unapply()
        # directly on every buff of a unit being removed (typically on
        # death), bypassing EventOnBuffRemove. Without synthesis, buff
        # fades on death are invisible — relevant when the buff was
        # observable beforehand (e.g., a player aura sourced by a unit
        # that just died). Synthesize before delegating so the snapshot
        # captures the unit's final on-field state.
        is_unit = isinstance(obj, Level.Unit)
        if is_unit:
            for buff in list(obj.buffs):
                if not getattr(buff, 'applied', False):
                    continue
                journal.record('EventOnBuffRemove', {
                    'target': _snapshot_unit(obj),
                    'buff': _snapshot_buff(buff),
                    'stack_count_after': 0,
                    'is_unit_removed': True,
                })
        return original_remove_obj(self, obj)

    def patched_unit_kill(self, damage_event=None, trigger_death_event=True):
        # Game's Unit.kill at Level.py:2310-2325 only raises EventOnDeath
        # when trigger_death_event is True. Silent kills come from
        # RespawnAs, ChanceToBecome, MatureInto, try_dismiss_ally — i.e.,
        # transformation and dismissal, where the game suppresses the
        # death event because the unit is being replaced rather than
        # truly dying. We still want capture so composers can surface
        # transformations distinctly. Synthesize before the original
        # runs so the record's snapshot reflects pre-removal state.
        if not trigger_death_event and not getattr(self, 'killed', False):
            journal.record('EventOnDeath', {
                'target': _snapshot_unit(self),
                'killing_damage': None,
                'killing_dtype': None,
                'killing_source': None,
                'is_silent_kill': True,
            })
        return original_unit_kill(self, damage_event=damage_event,
                                  trigger_death_event=trigger_death_event)

    def patched_unit_equip(self, item):
        # Game's Unit.equip at Level.py:1714-1716 fires the equipment's
        # EventOnUnitAdded trigger directly without raising the event
        # (and passes None as the event arg — likely also a bug). The
        # item-side initialization runs but is invisible. Synthesize a
        # distinct 'equipment_initialized' record (not EventOnUnitAdded)
        # so the auto-proc is captured without polluting render paths
        # that consume real EventOnUnitAdded (e.g., the digest's Spawned
        # section, which would otherwise render "Wizard spawned").
        has_uadd_trigger = (
            hasattr(item, 'owner_triggers')
            and Level.EventOnUnitAdded in getattr(item, 'owner_triggers', {})
        )
        result = original_unit_equip(self, item)
        if has_uadd_trigger:
            journal.record('equipment_initialized', {
                'unit': _snapshot_unit(self),
                'item_name': getattr(item, 'name', None),
            })
        return result

    def patched_unit_apply_buff(self, buff, duration=0):
        # RW3's Unit.apply_buff (Level.py:2404-2455) resolves a re-application
        # of an already-present same-typed buff several ways; only some raise
        # EventOnBuffApply. We detect the outcome by observing buff-list STATE
        # before/after — NOT by counting raised events. (The old "no events
        # raised" heuristic is dead in RW3: apply_buff now raises an
        # unconditional EventOnBuffAttemptApply at Level.py:2414, so every call
        # advances journal.sequence and seq_before == seq_after never holds.)
        #
        #   - Fresh apply / intensity stack / STACK_REPLACE: the PASSED buff is
        #     appended to self.buffs (Level.py:2439) and a real EventOnBuffApply
        #     fires — already captured (and tagged with agency by _snapshot_buff).
        #     Nothing to synthesize.
        #   - Silent duration refresh (STACK_NONE max() / STACK_DURATION/TRANSFORM
        #     +=, Level.py:2428-2434): the passed buff is discarded and the
        #     EXISTING buff's turns_left is mutated with no EventOnBuffApply.
        #     Synthesize one tagged is_refresh so the cadence can speak the new
        #     remaining duration — but ONLY when the duration actually changed.
        #     A no-op max() refresh, a debuff-immune block, or a clarity re-stun
        #     all leave turns_left untouched (or never reach the existing branch)
        #     and stay silent.
        pre_existing = None
        if hasattr(self, 'buffs'):
            pre_existing = next(
                (b for b in self.buffs
                 if getattr(b, 'name', None) == getattr(buff, 'name', None)
                 and type(b) == type(buff)),
                None,
            )
        pre_turns = (getattr(pre_existing, 'turns_left', None)
                     if pre_existing is not None else None)
        result = original_unit_apply_buff(self, buff, duration)

        buffs = getattr(self, 'buffs', None) or ()
        if buff in buffs:
            # Fresh apply / intensity stack / replace — real EventOnBuffApply
            # already captured. Nothing to do.
            pass
        elif (
            pre_existing is not None
            and pre_existing in buffs
            and getattr(pre_existing, 'turns_left', None) != pre_turns
        ):
            # Silent duration refresh that changed the remaining duration.
            journal.record('EventOnBuffApply', {
                'target': _snapshot_unit(self),
                'buff': _snapshot_buff(pre_existing),
                'stack_count_after': _stack_count_for(self, pre_existing),
                'is_refresh': True,
            })
        return result

    def patched_buff_advance(self):
        # Synthesize a chain root for the buff's per-turn tick. Events
        # raised during on_advance (apply_buff, deal_damage, summon) parent
        # to this synthetic record, giving the orphan-window composer
        # source attribution and chain-walkability for content that today
        # fires as truly orphan records (parent=None).
        #
        # Type discrimination on buff_type: equipment (BUFF_TYPE_ITEM)
        # produces 'equipment_tick'; everything else produces 'buff_tick'.
        #
        # ChannelBuff is skipped: its on_advance is independently wrapped
        # by patched_channel_advance which synthesizes a `cast_begin` record
        # (so channels appear to the digest as chain roots). Wrapping the
        # outer Buff.advance for channels too would put the cast_begin
        # under a buff_tick, breaking the digest's parent=None root
        # detection. Channels stay on the cast_begin path; everything else
        # gets a buff_tick / equipment_tick.
        #
        # Most buffs produce no events during on_advance — the synthetic
        # record is then a no-effect chain root. Composers ignore empty
        # chains, so this is render noise only, not speech noise. Storage
        # cost is bounded (per-level reset; ~30KB per dense turn at peak).
        if isinstance(self, Level.ChannelBuff):
            return original_buff_advance(self)
        is_equipment = (
            getattr(self, 'buff_type', None) == Level.BUFF_TYPE_ITEM
        )
        record_type = 'equipment_tick' if is_equipment else 'buff_tick'
        cause_record = journal.record(record_type, {
            'buff': _snapshot_buff(self),
            'owner': _snapshot_unit(getattr(self, 'owner', None)),
        })
        journal.push(cause_record)
        try:
            return original_buff_advance(self)
        finally:
            journal.pop()

    def patched_cloud_advance(self):
        # Synthesize a chain root for the cloud's per-turn tick so its
        # damage / buff-apply effects parent to it. Cloud at the wizard's
        # tile is critical-tier (per the agency rule); the crisis producer
        # claims cloud_tick records targeting the wizard. Cloud effects on
        # other tiles flow to the orphan composer's status-tick section.
        #
        # The cloud's pre-call duration is captured so the composer can
        # render "X turns left" without separately querying the cloud
        # (which may have been killed by this same advance call). The
        # original advance() decrements duration first then calls
        # on_advance(), so post-call self.duration reflects the
        # already-decremented value; we capture pre-call here.
        pre_duration = getattr(self, 'duration', None)
        cause_record = journal.record('cloud_tick', {
            'cloud_name': getattr(self, 'name', None) or type(self).__name__,
            'x': getattr(self, 'x', None),
            'y': getattr(self, 'y', None),
            'duration_before_tick': pre_duration,
            'duration_after_tick': (
                pre_duration - 1 if pre_duration is not None else None
            ),
        })
        journal.push(cause_record)
        try:
            return original_cloud_advance(self)
        finally:
            journal.pop()

    Level.Level.act_cast = patched_act_cast
    Level.Level.queue_spell = patched_queue_spell
    Level.EventHandler.raise_event = patched_raise_event
    Level.ChannelBuff.on_advance = patched_channel_advance
    Level.Level.add_obj = patched_add_obj
    Level.Level.remove_obj = patched_remove_obj
    Level.Unit.kill = patched_unit_kill
    # NB: RW2/RW3 Unit.steal_hp was dead code (never called; RW2's body had a
    # broken argless raise_event). RW3 life-drain flows through deal_damage with
    # Tags.Heal → EventOnDamaged (victim) + EventOnHealed (drainer), both already
    # captured. No steal_hp hook needed. (Verified 2026-06-27.)
    Level.Unit.equip = patched_unit_equip
    Level.Unit.apply_buff = patched_unit_apply_buff
    Level.Buff.advance = patched_buff_advance
    Level.Cloud.advance = patched_cloud_advance
    journal._hooks_installed = True
