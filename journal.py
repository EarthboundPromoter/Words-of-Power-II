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
import math
import os
import time
from collections import deque

import Level

_UNSET = object()

# Unit 3 (reactive_markers): the pre-request tap. Called at the top of the
# three cast-request patches (act_cast / defer_cast / queue_spell) BEFORE
# the request-time cause capture, so a reaction that queues work gets its
# marker materialized first and the deferred cast parents to it. None until
# reactive_markers installs; the installed hook is cheap (empty-stack
# no-op) and exception-guarded on its own side.
_pre_request_hook = None


class _Journal:
    def __init__(self):
        self.records = []
        self.cause_stack = []
        self.sequence = 0
        self.action_chain_id = 0
        self.level_id = None
        self._fp = None
        self._hooks_installed = False
        # Set True only while Unit.refresh() runs, so the __setattr__ interceptor
        # ignores refresh's shields=0 reset (a level-transition/respawn reset, not
        # a combat strip). Applies to ALL watched attrs. See patched_unit_refresh.
        self._suppress_watched_capture = False
        # Set True only while Level.deal_damage runs. deal_damage (Level.py:4006)
        # is the SOLE evented HP-change path — it writes cur_hp (4073/4129/4135)
        # and raises EventOnDamaged (4119) / EventOnHealed (4087), nowhere else.
        # So cur_hp writes INSIDE it are already captured via those events; suppress
        # the interceptor's cur_hp capture there and what escapes the bracket is
        # exactly the SILENT cur_hp changes (Crisis Charm, Soulbound, Ruby Heart,
        # component pickups, ...). cur_hp-ONLY: the block's inline `shields -= 1`
        # (Level.py:4061) fires inside deal_damage and MUST still capture (the
        # shield_blocked detail depends on it), so shields are not suppressed here.
        self._suppress_cur_hp_capture = False
        # R3 cause-graph. cast_begin roots are anchored at execute_cast (the true
        # per-cast chokepoint) so every cast — manual, deferred, internal, inline —
        # gets exactly one root. A cast's triggering cause is computed at
        # act_cast/defer_cast APPEND time (while it is still live) and held in
        # _pending_cause, a FIFO mirroring Level.pending_casts, then popped at
        # execute_cast. _pending_cast_begin hands the freshly-made cast_begin to the
        # CastContext subclass, which carries it through resolution via the engine's
        # own current_cast_context. _in_execute_cast marks execute_cast's own
        # queue_spell so it is NOT generator-wrapped (wrapping desyncs cast_contexts
        # and breaks is_manual_cast — docs/IS_MANUAL_CAST_DESYNC.md). _level is the
        # active Level so parenting can read current_cast_context off it.
        self._pending_cause = deque()
        self._pending_cast_begin = None
        # One-shot: armed when a QUEUED cast's CastContext is created (after
        # pay-costs, before the cast queues its own gen at Level.py:3133) and
        # consumed by that gen's queue_spell, so ONLY execute_cast's own gen skips
        # the cause-wrap. Content gens queued by reaction handlers during the SAME
        # execute_cast — e.g. an EventOnSpellCast handler (raised at Level.py:3140,
        # inside the call) that queue_spells a copy/free cast — are still wrapped, or
        # they orphan (the adversarial-gate finding). _pending_queue carries the
        # cast's `queue` flag to the CastContext so the token arms only when an own
        # gen will actually be queued (queue=False casts have none).
        self._skip_next_queue_wrap = False
        self._pending_queue = False
        self._level = None

    def reset(self, level_id):
        self.records = []
        self.cause_stack = []
        self._pending_cause.clear()
        self._pending_cast_begin = None
        self._skip_next_queue_wrap = False
        self._pending_queue = False
        self._suppress_watched_capture = False
        self._suppress_cur_hp_capture = False
        # Drop the level ref on transition: a new floor's records must not read a
        # departed level's current_cast_context. Not live-reachable today (no
        # cross-level records fire during a live cast), but cheap insurance.
        self._level = None
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

    def _current_parent_seq(self):
        # Parent for a new record = the innermost live cause. cause_stack wins when
        # non-empty (nested events, the synthesized buff/cloud/channel roots, and
        # wrapped direct-queue gens). Otherwise fall back to the running cast's
        # cast_begin via the engine's own current_cast_context — this is how an
        # execute_cast gen's effects (which R3 no longer generator-wraps) reach
        # their root during deferred resolution.
        if self.cause_stack:
            return self.cause_stack[-1]["sequence"]
        lvl = self._level
        if lvl is not None:
            ctx = getattr(lvl, "current_cast_context", None)
            if ctx is not None:
                cb = getattr(ctx, "_cast_begin", None)
                if cb is not None:
                    return cb["sequence"]
        return None

    def record(self, event_type, payload, parent_seq=_UNSET):
        self.sequence += 1
        if parent_seq is _UNSET:
            parent_seq = self._current_parent_seq()
        rec = {
            "sequence": self.sequence,
            "action_chain_id": self.action_chain_id,
            "level_id": self.level_id,
            "event_type": event_type,
            "parent": parent_seq,
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

    def begin_chain_with_parent(self, payload, parent_rec):
        # Like begin_chain, but the parent is computed at cast-REQUEST time
        # (act_cast/defer_cast, via the _pending_cause FIFO), not at drain time when
        # the triggering cause is gone. This is the deferred-cast root fix.
        self.action_chain_id += 1
        parent_seq = parent_rec["sequence"] if parent_rec else None
        return self.record("cast_begin", payload, parent_seq)

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
    """EventOnShieldRemoved(unit, source). Raised ONLY on a block
    (Level.py:4066). Kept as a DATA record for the shield ledger /
    validation; the canonical block VOICE is the richer 'shield_blocked'
    record synthesized in the deal_damage wrapper (the event is too thin —
    it carries no blocked amount or damage type, which live only as locals in
    deal_damage). `source` enriched here for ledger completeness; the digest's
    'absorbed by N shields' counting still works off the target."""
    return {
        'target': _snapshot_unit(event.unit),
        'source_name': _name_or(getattr(event, 'source', None)),
        'source_owner_name': _name_or(getattr(getattr(event, 'source', None), 'owner', None)),
    }


# ----------------------------------------------------------------------
# Shield capture (R3). The game mutates `shields` as an IMMUTABLE int by
# silent direct assignment (add_shields Level.py:2499; remove_shields :2504;
# and ~8 raw `.shields =`/`-=` writes in spell/equipment content), raising no
# event. Because the value is immutable, EVERY change is a wholesale
# reassignment that must route through Unit.__setattr__ — so a single
# __setattr__ interceptor (patched_setattr in install_hooks) is the one
# COMPLETE source for shield-change events, catching content-direct writes the
# old add_shields/remove_shields method hooks missed. (Same mechanism will
# capture `team` for team-flip — see _WATCHED_ATTRS.) The block DETAIL
# (blocked amount/type/source the game's DMG_BLOCKED log shows but the
# EventOnShieldRemoved event omits) still needs the deal_damage wrapper, gated
# on the block event actually firing. These helpers are pure (no live-Level
# dependency) so they unit-test in test_journal.py.
# ----------------------------------------------------------------------

# Attributes the __setattr__ interceptor watches: no-event, player-relevant
# state the game shows but never logs (render-gate bin 2, PRODUCER_TAXONOMY §5).
# Each dispatches to its own change-record builder in patched_setattr; all share
# the interceptor chokepoint + the _is_live_unit gating:
#   'shields' — numeric delta (gain/strip)
#   'team'    — categorical allegiance flip
#   'cur_hp'  — silent heals (gains) AND silent hp_loss (decreases, G-G Unit 4).
#               deal_damage's own writes are bracketed out (_suppress_cur_hp_
#               capture) since they are already evented; an HP-spend's hp_loss is
#               retro-marked superseded_by_spend by its EventOnSpendHP (D2). What
#               remains is exactly the silent set: heals (Crisis Charm, Soulbound,
#               Ruby Heart, components, undeath, ...) and losses (Word of Undeath,
#               Death Tax, boss steal, clamp-after-drain, chronomancer timeout).
#   'max_hp'  — any change (no max_hp event exists anywhere, so every max_hp write
#               is silent): boosts (Ruby Heart +25, undeath, components) and drains
#               (drain_max_hp, Necrosis, sacrifice). Capture-only interim.
#   'xp'      — SP total (G-F, Unit 4). Player-only in practice (no non-player .xp
#               write exists); gains AND spends record (voice-ignores-spends is a
#               composer ruling). Init writes (Game.py:498, Mutators.py:1105) fire
#               pre-live -> gated; unpickle bypasses __setattr__ -> no load flood.
# This is the taxonomy's universal-chokepoint capture (§2) — no per-site hooks, so
# future content is caught automatically.
_WATCHED_ATTRS = frozenset({'shields', 'team', 'cur_hp', 'max_hp', 'xp'})


def _is_live_unit(unit):
    """The spawn-vs-runtime discriminator for watched-attr capture: True iff
    `unit` is currently a placed, on-field unit — present in its level's `units`
    list. Level.add_obj appends the unit at Level.py:3907, one line BEFORE it
    raises EventOnUnitAdded (3908) and two before it sets ever_spawned (3910).
    So:
      - construction / factory / summon-init shield & team writes (all BEFORE
        the append) read False -> dropped as arrival, not "gained";
      - the on-summon grant window is the EventOnUnitAdded raise (3908), where
        the unit is ALREADY in `units` -> reads True -> captured. This is the
        R7 straddle the old `ever_spawned` gate missed (grants that fire on
        EventOnUnitAdded landed before ever_spawned=True and were dropped);
      - a killed/removed unit is popped from `units` (remove_obj, Level.py:3947)
        -> reads False, so off-field writes drop naturally. That subsumes R22:
        ReincarnationBuff.respawn restores shields (CommonContent.py:1251) while
        the unit sits removed between kill and re-add, so no phantom "gained".
    O(n) membership. The interceptor's fast-path returns before this for every
    non-watched attribute, and the suppress-checks (incl. _suppress_cur_hp_capture
    inside deal_damage — the hot cur_hp path) short-circuit before it too. So it
    runs only on watched writes that ESCAPE suppression: shield/team changes, and
    silent cur_hp/max_hp writes OUTSIDE deal_damage (rare). Guarded so a
    not-yet-placed unit (level is None) reads False."""
    lvl = getattr(unit, 'level', None)
    if lvl is None:
        return False
    units = getattr(lvl, 'units', None)
    if not units:
        return False
    return unit in units


def _resist_blocked_amount(resists, damage_type, orig_amount):
    """Reproduce the game's post-resist amount (Level.deal_damage,
    Level.py:4034-4039) for a hit a shield then fully blocked — the
    'would have been N' the game's DMG_BLOCKED log shows. The
    EventOnShieldRemoved event can't carry it, so the block record recomputes
    from the same inputs: effective resist capped at 100, ceil of the resisted
    fraction.

    NOT folded in (rare, documented divergence — not reconstructed): a
    damage_limit_buff clamp (Level.py:4042) can lower the game's amount below
    this."""
    if orig_amount is None:
        return None
    resist = 0
    if resists:
        try:
            resist = resists.get(damage_type, 0)
        except Exception:
            resist = 0
    resist = min(resist, 100)
    return int(math.ceil(orig_amount * (100 - resist) / 100.0))


_BLOCK_CLAIMED_MARK = 'shield_blocked_claimed'
_BLOCK_SUPERSEDED_MARK = 'superseded_by_block'
_SPEND_SUPERSEDED_MARK = 'superseded_by_spend'

# Record kinds that may legitimately interleave between a spend's raw write and
# its EventOnSpendHP raise without breaking their adjacency (three spend sites
# call level.log between the two — Level.py:918, Equipment.py:6136, 7336 — so
# once the log-capture oracle lands, its records are adjacency-transparent).
_ADJACENCY_TRANSPARENT_KINDS = frozenset({'game_log'})


def _supersede_spend_loss(records, spend_rec, unit_id, amount):
    """Mark the hp_loss produced by an HP-spend's own raw write as superseded
    by its EventOnSpendHP record (D2, Unit 4) — both records exist (capture
    uniform, the block/strip precedent); the mark is composition-facing so the
    spend is voiced once, never as a duplicate raw loss.

    Bracket-grade matching, no recency correlation: at all six spend sites the
    write and the raise are adjacent in one synchronous body (write, raise 1-3
    lines later; nothing hooked fires between — gate-verified), so the spend's
    own hp_loss is the IMMEDIATELY-preceding record and shares the spend
    record's parent (same live cause window). The matcher therefore examines
    only the first non-transparent record before spend_rec and requires
    parent-equality + unit id + exact amount + unmarked. A coincident silent
    loss (boss steal on the wizard + a same-amount spend, the overlap case) is
    never claimed: the spend's own loss always exists (spend writes escape
    every suppression bracket) and sits between any older loss and the event.
    Claim-once keeps two identical spends each marking their own loss."""
    for rec in reversed(records):
        if rec is spend_rec:
            continue
        if rec.get('event_type') in _ADJACENCY_TRANSPARENT_KINDS:
            continue
        if (rec.get('event_type') == 'hp_loss'
                and rec.get('parent') == spend_rec.get('parent')
                and (rec.get('payload', {}).get('target') or {}).get('id') == unit_id
                and rec.get('payload', {}).get('loss_amount') == amount
                and _SPEND_SUPERSEDED_MARK not in rec.get('marks', ())):
            rec.setdefault('marks', []).append(_SPEND_SUPERSEDED_MARK)
        return  # adjacency: only the first non-transparent record can match


def _supersede_block_strip(records, seq_before, target_id):
    """A block's inline `unit.shields -= 1` (Level.py:4061) trips the
    __setattr__ interceptor and produces a generic `shield_stripped` record in
    addition to the rich `shield_blocked`. Mark that coincident strip superseded
    so renderers (any producer) voice ONLY the block, not a duplicate 'shields
    stripped'. Identified as the in-window `shield_stripped` for this target —
    a block consumes exactly one shield, so there is one to mark."""
    for rec in reversed(records):
        if rec.get('sequence', 0) <= seq_before:
            break
        if (rec.get('event_type') == 'shield_stripped'
                and (rec.get('payload', {}).get('target') or {}).get('id') == target_id
                and _BLOCK_SUPERSEDED_MARK not in rec.get('marks', ())):
            rec.setdefault('marks', []).append(_BLOCK_SUPERSEDED_MARK)
            return


def _claim_block_event(records, seq_before, target_id):
    """Precise block detector + claimer: find the first UNCLAIMED
    EventOnShieldRemoved for `target_id` raised after `seq_before`, mark it
    claimed, and return True. That event is raised ONLY at the real block
    branch (Level.py:4066), so gating on it — not on a shields before/after
    diff — avoids false positives on the other paths deal_damage returns 0
    (no-unit, dead unit, damage-instance cap, redirect).

    The CLAIM (via the journal's existing per-record `marks`) is what stops an
    OUTER deal_damage wrapper from re-counting a block performed by a NESTED
    inner call: a handler that re-damages the same shielded unit from inside
    raise_event(EventOnShieldRemoved)/PreDamaged makes the inner block's event
    fall inside the outer wrapper's scan window too. The inner wrapper runs
    first and claims its event; the outer then finds only its own (or none).
    Walks only records created during the call (breaks at seq_before)."""
    for rec in reversed(records):
        if rec.get('sequence', 0) <= seq_before:
            break
        if rec.get('event_type') == 'EventOnShieldRemoved':
            tgt = rec.get('payload', {}).get('target') or {}
            if tgt.get('id') == target_id and _BLOCK_CLAIMED_MARK not in rec.get('marks', ()):
                rec.setdefault('marks', []).append(_BLOCK_CLAIMED_MARK)
                return True
    return False


def _shield_change_record(unit, before, after):
    """Classify a shields write seen by the __setattr__ interceptor into a
    journal record, or None for a no-op. The interceptor sees only the stored
    before/after (already cap-clamped by the game), so the net delta IS the
    true change — inherently cap-honest, no need to know the requested amount.
    A block's inline `unit.shields -= 1` (Level.py:4061) also arrives here as a
    strip; the deal_damage wrapper adds the richer block detail separately and
    the render layer lets the block voice supersede the coincident strip."""
    before = before or 0
    after = after or 0
    if after == before:
        return None
    if after > before:
        return 'shield_gained', _shield_gained_payload(unit, after - before, before, after)
    return 'shield_stripped', _shield_stripped_payload(unit, before, after)


def _shield_gained_payload(unit, amount, shields_before, shields_after):
    """Synthesized 'shield_gained' record. `amount` is the NET gain
    (after - before) the interceptor observed — already cap-honest, since the
    game stored the clamped value before we diffed."""
    return {
        'target': _snapshot_unit(unit),
        'amount': amount,
        'shields_before': shields_before,
        'shields_after': shields_after,
    }


def _shield_stripped_payload(unit, shields_before, shields_after):
    """Synthesized 'shield_stripped' record — remove_shields strips with no
    event. The block path decrements unit.shields inline (NOT via
    remove_shields), so this never double-fires with shield_blocked."""
    removed = None
    if shields_before is not None and shields_after is not None:
        removed = shields_before - shields_after
    return {
        'target': _snapshot_unit(unit),
        'amount_removed': removed,
        'shields_before': shields_before,
        'shields_after': shields_after,
    }


def _team_change_record(unit, before, after):
    """Classify a `team` write seen by the __setattr__ interceptor into a journal
    record, or None for a no-op. Categorical, not a numeric delta — RW3 has
    exactly two teams (TEAM_PLAYER=0, TEAM_ENEMY=1, Level.py:18-19), so a runtime
    flip is one of two directions, read relative to the wizard's TEAM_PLAYER:
        enemy  -> player : gained an ally -> 'team_joined' ("turned friendly")
        player -> enemy  : lost an ally   -> 'team_turned' ("turned hostile")
    Direction comes from the raw before/after ints, NOT the snapshot (some sites
    hardcode the constant rather than caster.team). Spawn-init writes are gated
    out upstream by _is_live_unit — a summoned/transformed unit's team is set
    before add_obj appends it (Level.py:3998 summon; CommonContent MatureInto/
    raise_skeleton), so it isn't yet in level.units and only flips of already-live
    units reach here. Berserk is a buff, not a team write, so it never appears."""
    if before == after:
        return None
    player = getattr(Level, 'TEAM_PLAYER', 0)
    if after == player:
        return 'team_joined', _team_change_payload(unit, before, after)
    return 'team_turned', _team_change_payload(unit, before, after)


def _team_change_payload(unit, before, after):
    """Synthesized team-flip record. The snapshot is post-write (team == after),
    so a just-joined unit reads as an ally downstream; team_before/after carry the
    raw direction for renderers that need it."""
    return {
        'target': _snapshot_unit(unit),
        'team_before': before,
        'team_after': after,
    }


def _classify_watched(unit, name, before, after):
    """Dispatch a watched-attr before/after to its change-record builder, or
    None for a no-op. Shared by the __setattr__ interceptor (per-write) and the
    shield-setter net-emit (one net diff over a bracketed add_shields/
    remove_shields body), so both classify identically.

    Every branch is NAME-EXPLICIT with no fallback (Unit-4 gate finding: the
    old fallback-else was the shields branch, so a watched attr added without
    its own branch would have classified — and VOICED — as a shield change).
    An unmatched name is a wiring error and records nothing."""
    if name == 'shields':
        return _shield_change_record(unit, before, after)
    if name == 'team':
        return _team_change_record(unit, before, after)
    if name == 'cur_hp':
        return _cur_hp_change_record(unit, before, after)
    if name == 'max_hp':
        return _max_hp_change_record(unit, before, after)
    if name == 'xp':
        return _xp_change_record(unit, before, after)
    return None


def _emit_watched_net(unit, name, before):
    """Emit ONE net-diff record for a watched attr after a bracketed setter body
    (add_shields / remove_shields), honoring the same live-unit + suppress gate
    as the per-write interceptor. `before` is the attr's stored value from just
    before the setter ran; the current stored value is the net result — so a
    cap-crossing grant (Level.py:2501-2502: `+=` then `min(...,20)`) or an
    underflow (2507-2508: `-=` then clamp-to-0) voices its true net change once,
    instead of an inflated intermediate plus a correction."""
    try:
        if not _is_live_unit(unit) or journal._suppress_watched_capture:
            return
        after = unit.__dict__.get(name)
        classified = _classify_watched(unit, name, before, after)
        if classified is not None:
            event_type, payload = classified
            journal.record(event_type, payload)
    except Exception:
        pass


# ----------------------------------------------------------------------
# Silent HP capture (R5) — UNIVERSAL, via the __setattr__ interceptor chokepoint
# (PRODUCER_TAXONOMY §2). cur_hp/max_hp are in _WATCHED_ATTRS; the interceptor
# records their SILENT changes — those the game shows (HP bar) but never logs.
#
# cur_hp: GAINS are heals; DECREASES are hp_loss (G-G, Unit 4). deal_damage's own
# cur_hp writes are bracketed out (_suppress_cur_hp_capture) — already evented —
# and an HP-spend's hp_loss is retro-marked by its EventOnSpendHP (D2 supersede),
# so what the interceptor contributes is exactly the silent set. Heals: Crisis
# Charm (Equipment.py:3195), Soulbound (CommonContent.py:1392), Ruby Heart
# (Level.py:2808), component pickups, undeath. Losses: Word of Undeath
# (Spells.py:5182), Death Tax (:5130), boss steal (FinalBosses.py:606), the
# clamp-after-max_hp-drain family (CommonContent.py:1149, Necrosis Level.py:1682),
# chronomancer timeout (RiftWizard3.py:10475) — and any FUTURE content, no
# per-site hooks. (Reincarnation's restore happens off-field — removed from
# level.units between kill and re-add — so the _is_live_unit gate drops it;
# direct kill()'s cur_hp=0 write, Level.py:2601, is likewise off-field because
# remove_obj at :2600 pops the unit FIRST: load-bearing ordering, see the
# direct-kill regression test.)
#
# max_hp: no max_hp event exists, so EVERY max_hp write is silent. The interceptor
# records any change (boost or drain) as 'max_hp_change'. Capture-only interim.
#
# Attribution (which item healed you) is NOT known at the write — it is the
# cause-walk's job (Track B, §3 "source = attribution"). The interim render keys
# on the DATA instead: a wizard cur_hp rise from <=0 is a lethal-save, voiced by
# crisis (covers Crisis Charm AND Soulbound without naming either). Ordinary
# silent heals (cur_hp_before > 0) stay captured-but-inert, staged for Track B.
# ----------------------------------------------------------------------

def _cur_hp_change_record(unit, before, after):
    """Classify a cur_hp write seen by the interceptor. GAINS are silent heals;
    DECREASES are silent hp_loss (G-G, Unit 4) — the raw non-evented drops
    (Word of Undeath //= 2, Death Tax, boss HP-steal, the clamp-after-max_hp-
    drain family, chronomancer timeout). Evented losses stay owned elsewhere:
    deal_damage's writes are bracketed out, and an HP-spend's record is
    retro-marked superseded_by_spend by its own EventOnSpendHP (D2).

    Direction branches FIRST (gate finding): the gains-side defensive max_hp
    clamp must never rewrite a decrease's magnitude. Decrease payloads record
    STORED TRUTH — no flooring: no shipped site stores negative cur_hp (every
    raw site clamps/guards; deal_damage caps at cur_hp), and a floored
    loss_amount would break the spend-supersede exact-amount match.

    A write-then-clamp pair (WoU on a 1-HP unit: 1->0 then max(1,...) 0->1)
    records BOTH writes — hp_loss then silent_heal, same parent. That is
    per-write truth of an engine idiom (plan D5), correct capture; the
    composer phase pairs/collapses at speech time. Do not "fix" here.

    Returns (kind, payload) or None for a no-op."""
    before = before or 0
    after = after or 0
    max_hp = getattr(unit, 'max_hp', None)
    if after == before:
        return None
    if after < before:
        return 'hp_loss', {
            'target': _snapshot_unit(unit),
            'loss_amount': before - after,
            'cur_hp_before': before,
            'cur_hp_after': after,
            'max_hp_after': max_hp,
            # Attribution deferred to the cause-walk; unknown at the write.
            'source_name': None,
        }
    # Defensive, GAINS ONLY: if a reactive handler raw-overheals above max
    # during an event raise (un-bracketed), the engine's paired clamp
    # (Level.py:4128/4135) runs bracketed, so the interceptor would otherwise
    # record the pre-clamp inflated value. Clamp to max so the recorded
    # magnitude matches the unit's real final HP. Forward insurance.
    if max_hp is not None and after > max_hp:
        after = max_hp
        if after <= before:
            return None
    return 'silent_heal', _silent_heal_payload(unit, before, after, max_hp)


def _silent_heal_payload(unit, cur_before, cur_after, max_hp):
    heal = (cur_after - cur_before) if (cur_before is not None and cur_after is not None) else None
    return {
        'target': _snapshot_unit(unit),
        'heal_amount': heal,
        'cur_hp_before': cur_before,
        'cur_hp_after': cur_after,
        'max_hp_after': max_hp,
        # Attribution deferred to the cause-walk (Track B); unknown at the write.
        'source_name': None,
    }


def _max_hp_change_record(unit, before, after):
    """Classify a max_hp write seen by the interceptor. No max_hp event exists,
    so every change is silent; record boosts and drains alike (capture-only).
    Returns ('max_hp_change', payload) or None for a no-op."""
    before = before or 0
    after = after or 0
    if after == before:
        return None
    return 'max_hp_change', {
        'target': _snapshot_unit(unit),
        'delta': after - before,
        'max_hp_before': before,
        'max_hp_after': after,
        'direction': 'gained' if after > before else 'drained',
    }


def _xp_change_record(unit, before, after):
    """Classify an xp (SP) write seen by the interceptor (G-F, Unit 4).
    Bidirectional and uniform — gains and spends both record; which of them is
    ever SPOKEN is the composer's call, never a capture filter. `before` may be
    None on the first live write (Unit.__init__ sets no xp attr) -> coerce to 0.
    Returns ('xp_change', payload) or None for a no-op."""
    before = before or 0
    after = after or 0
    if after == before:
        return None
    return 'xp_change', {
        'target': _snapshot_unit(unit),
        'delta': after - before,
        'xp_before': before,
        'xp_after': after,
    }


def _shield_blocked_payload(unit, blocked_amount, damage_type, source, shields_remaining):
    """Synthesized 'shield_blocked' record — the CANONICAL block voice. Binds
    the blocked hit (amount the game would have dealt + type + source) with the
    remaining shield count, from the deal_damage wrapper's before/after diff.
    Supersedes EventOnShieldRemoved for rendering."""
    return {
        'target': _snapshot_unit(unit),
        'blocked_amount': blocked_amount,
        'damage_type': _name_or(damage_type),
        'source_name': _name_or(source),
        'source_owner_name': _name_or(getattr(source, 'owner', None)),
        'shields_remaining': shields_remaining,
    }


def _payload_unfrozen(event):
    """EventOnUnfrozen(unit, dtype). Status-state-exit, surfaces the
    'Frozen broken' inline clause per phrasing spec."""
    return {
        'target': _snapshot_unit(event.unit),
        'damage_type': _name_or(event.dtype),
    }


def _payload_awakened(event):
    """EventOnAwakened(unit, buff). Status-state-exit at parity with unfrozen
    (G-M; Sleep is a new RW3 status with no RW2 ancestor). Raised in
    SleepBuff.on_unapplied (CommonContent.py:855) on EVERY unapply path:
    damage-wake, natural expiry, AND removal/death (kill -> remove_obj unapplies
    all buffs). No wake cause in the payload (unlike unfrozen's dtype) — a
    damage-wake inherits its cause from tree position instead."""
    return {
        'target': _snapshot_unit(event.unit),
        'buff_name': _name_or(event.buff),
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
    'EventOnAwakened': _payload_awakened,
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


# Span hooks (Unit 1 container-diff). The wrapped-generator proxy below is
# the ONLY cause carrier for direct-queue_spell cast windows (channels, the
# ~150 content sites, reaction-queued gens — none populate cast_contexts),
# so the container sweep hooks its per-step push/pop: enter fires BEFORE the
# push (closing the outer span under the old stack top), exit fires BEFORE
# the pop (the cause is still live for parenting). No-ops when unset.
span_enter_hook = None
span_exit_hook = None


def _wrap_with_cause(inner_gen, cause_record):
    """Wrap a spell-cast generator so the given cause is on the cause stack
    during each iteration step. Spell effects (damage, heals, summons) that
    fire from inside next(inner_gen) inherit cause_record as their parent."""
    try:
        while True:
            if span_enter_hook is not None:
                span_enter_hook(cause_record)
            journal.push(cause_record)
            try:
                value = next(inner_gen)
            except StopIteration:
                return
            finally:
                if span_exit_hook is not None:
                    span_exit_hook(cause_record)
                journal.pop()
            yield value
    except GeneratorExit:
        return


def _active_cause(level):
    """The record a new cast's cast_begin should hang under, evaluated at
    act_cast/defer_cast APPEND time (while the triggering cause is still live).

    cause_stack wins when non-empty — an in-keypress proc parents to the
    triggering spell/event; a buff- or cloud-tick cast parents to that tick's
    synthesized root. Otherwise the running cast's cast_begin (via the engine's
    current_cast_context) — an internal sub-cast nests under its parent cast.
    Neither → None → a manual-keypress (or truly orphan) root."""
    if journal.cause_stack:
        return journal.cause_stack[-1]
    ctx = getattr(level, "current_cast_context", None)
    if ctx is not None:
        return getattr(ctx, "_cast_begin", None)
    return None


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
    original_defer_cast = Level.Level.defer_cast
    original_execute_cast = Level.Level.execute_cast
    original_process_pending_casts = Level.Level.process_pending_casts
    original_cast_context = Level.CastContext
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
    original_unit_setattr = Level.Unit.__setattr__
    original_unit_refresh = Level.Unit.refresh
    original_add_shields = Level.Unit.add_shields
    original_remove_shields = Level.Unit.remove_shields
    original_deal_damage = Level.Level.deal_damage

    class _TrackedCastContext(original_cast_context):
        # A mutable subclass of the engine's immutable CastContext namedtuple.
        # namedtuple stores its fields via __new__/__slots__; a subclass that does
        # NOT declare __slots__ gains a __dict__, so we can attach the cast's
        # cast_begin record without disturbing field access / unpacking / isinstance.
        # execute_cast (Level.py:3129) builds every context, and the engine threads
        # it through resolution as current_cast_context (advance_spells:3321,
        # run_with_cast_context:4210) — so _cast_begin rides along to every effect
        # with no generator wrapping. Save-safe: __getstate__ nulls cast_contexts /
        # current_cast_context (Level.py:2927-2928), so no instance is ever pickled.
        def __init__(self, *args, **kwargs):
            self._cast_begin = journal._pending_cast_begin
            # Context creation (Level.py:3129) sits AFTER pay_costs and BEFORE the
            # cast queues its own gen (3133) — the exact point to arm the one-shot
            # that lets ONLY that gen skip the wrap. Gate on _pending_queue so a
            # queue=False cast (no own gen) doesn't arm and accidentally swallow the
            # wrap of a later content gen.
            if journal._pending_queue:
                journal._skip_next_queue_wrap = True

    def patched_act_cast(self, unit, spell, x, y, pay_costs=True, queue=True, **cast_kwargs):
        # R3: act_cast is the ENQUEUE boundary (Level.py:3074), not the per-cast one
        # — it appends to pending_casts and drains the whole queue. So it no longer
        # creates a cast_begin (execute_cast does, per-cast). It only captures the
        # triggering cause NOW, while it is still live, into the _pending_cause FIFO
        # that mirrors pending_casts; execute_cast pops it for the cast_begin parent.
        journal._level = self
        if _pre_request_hook is not None:
            _pre_request_hook()
        journal._pending_cause.append(_active_cause(self))
        return original_act_cast(self, unit, spell, x, y,
                                 pay_costs=pay_costs, queue=queue, **cast_kwargs)

    def patched_defer_cast(self, unit, spell, x, y, pay_costs=True, queue=True, **cast_kwargs):
        # Reactions schedule work here (Level.py:3081); it appends to pending_casts
        # and the running drain picks it up LATER, after the triggering cause is off
        # the stack. Capturing _active_cause at append time (not drain time) is
        # exactly what fixes deferred-cast mis-rooting/orphaning.
        journal._level = self
        if _pre_request_hook is not None:
            _pre_request_hook()
        journal._pending_cause.append(_active_cause(self))
        return original_defer_cast(self, unit, spell, x, y,
                                   pay_costs=pay_costs, queue=queue, **cast_kwargs)

    def patched_execute_cast(self, unit, spell, x, y, pay_costs=True, queue=True, cast_kwargs=None):
        # The true per-cast chokepoint (Level.py:3110): every queued/deferred/inline
        # cast passes through here exactly once. Create the one cast_begin, parented
        # by the cause captured at request time (popped from _pending_cause in
        # lockstep with process_pending_casts's popleft). Snapshot BEFORE the
        # original runs so charges are pre-cost (matches the old act_cast timing).
        journal._level = self
        ck = cast_kwargs or {}
        parent = journal._pending_cause.popleft() if journal._pending_cause else None
        cast_begin = journal.begin_chain_with_parent({
            'caster': _snapshot_unit(unit),
            'spell': _snapshot_spell(spell),
            'target_x': x,
            'target_y': y,
            'is_echo': bool(ck.get('is_echo', False)),
            # pay_costs=False marks a passive / proc / auto-cast (vs a real player
            # keypress) — consumed downstream to split keypresses from auto-fires.
            'is_player': bool(getattr(unit, "is_player_controlled", False)),
            'pay_costs': bool(pay_costs),
        }, parent)
        # Hand the cast_begin to the CastContext (built inside the original at 3129)
        # so it rides current_cast_context through deferred/inline resolution. Push
        # it on cause_stack too, so the SYNCHRONOUS pay-cost events + EventOnSpellCast
        # (raised inside the original at 3140) parent to it. _pending_queue lets the
        # CastContext arm the one-shot that skips wrapping ONLY this cast's own gen.
        journal._pending_cast_begin = cast_begin
        journal._pending_queue = queue
        journal.push(cast_begin)
        try:
            return original_execute_cast(self, unit, spell, x, y,
                                         pay_costs=pay_costs, queue=queue, cast_kwargs=cast_kwargs)
        finally:
            journal.pop()
            journal._pending_cast_begin = None
            journal._pending_queue = False
            # Bound any leak: if the own gen was never queued (e.g. an exception
            # before 3133), don't let the armed token skip an unrelated later wrap.
            journal._skip_next_queue_wrap = False

    def patched_process_pending_casts(self):
        # Reconcile the FIFO after the OUTER drain. Normally both queues drain to
        # empty in lockstep; the DEFERRED_CAST_CAP clear (Level.py:3106) empties
        # pending_casts while leaving its un-popped causes behind, so we drop those.
        # But if an exception aborts the drain mid-way, the engine's finally
        # (Level.py:3108) leaves un-drained tuples in pending_casts — and those stay
        # PAIRED with their _pending_cause entries (both popleft in lockstep), so
        # clearing then would let the leftover steal the next cast's cause on the
        # next drain (adversarial-gate finding). Gate on pending_casts being empty:
        # clear only when the engine truly drained (or cap-cleared) it; otherwise
        # leave the aligned remainder. Nested calls (Level.py:3088 guard) must not
        # touch the FIFO.
        was_outer = not self.processing_pending_casts
        try:
            return original_process_pending_casts(self)
        finally:
            if was_outer and not self.pending_casts:
                journal._pending_cause.clear()

    def patched_queue_spell(self, gen):
        journal._level = self
        # execute_cast's OWN gen carries its cause via the CastContext
        # (current_cast_context); wrapping it would desync cast_contexts from
        # active_spells and break is_manual_cast (docs/IS_MANUAL_CAST_DESYNC.md). The
        # one-shot skips EXACTLY that one gen. Every OTHER caller is wrapped —
        # channels, the ~150 content sites, AND reaction handlers that queue_spell
        # during this same execute_cast's EventOnSpellCast/pay-cost raise (a window
        # the old flag wrongly swallowed, orphaning e.g. AlchemistMulticastBuff's
        # free re-cast) — because none of them populate cast_contexts, so cause_stack
        # is their only carrier.
        if journal._skip_next_queue_wrap:
            journal._skip_next_queue_wrap = False
            return original_queue_spell(self, gen)
        if _pre_request_hook is not None:
            _pre_request_hook()
        cause = journal.cause_stack[-1] if journal.cause_stack else None
        if cause is None:
            # A direct queue from inside a cast's own gen body: no event on the
            # stack, but current_cast_context holds the running cast — hang the
            # queued effect under that cast's cast_begin.
            ctx = getattr(self, "current_cast_context", None)
            if ctx is not None:
                cause = getattr(ctx, "_cast_begin", None)
        if cause is not None:
            gen = _wrap_with_cause(gen, cause)
        return original_queue_spell(self, gen)

    def patched_raise_event(self, event, entity=None):
        rec = journal.record(type(event).__name__, _to_payload(event))
        # D2 (Unit 4): an HP-spend's raw write just preceded this raise in the
        # same synchronous body — retro-mark its hp_loss as spend-owned. Runs
        # at record CREATION, before dispatch, so it is independent of what
        # handlers do during the raise (gate-pinned placement).
        if type(event).__name__ == 'EventOnSpendHP':
            try:
                _supersede_spend_loss(journal.records, rec,
                                      id(event.unit), event.hp)
            except Exception:
                pass
        journal.push(rec)
        # Un-bracket cur_hp capture for the duration of the raise. deal_damage
        # suppresses cur_hp capture (its OWN write at Level.py:4073 is the evented
        # damage/heal), but a REACTIVE handler that writes cur_hp during an event
        # raise INSIDE deal_damage — Crisis Charm / Soulbound restoring HP on
        # EventOnDamaged (raised at 4119, after the 4073 write) — is a SILENT heal
        # we must capture, not suppress. The engine's own write is direct (not in
        # a raise); reactive writes are in a raise. So re-enabling capture during
        # raises cleanly separates them. No-op outside deal_damage (flag already
        # False). Save/restore nests correctly with an inner deal_damage.
        prev_cur_hp = journal._suppress_cur_hp_capture
        journal._suppress_cur_hp_capture = False
        try:
            return original_raise_event(self, event, entity)
        finally:
            journal._suppress_cur_hp_capture = prev_cur_hp
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
        # ChannelBuff.spell is the spell's bound cast METHOD, not the spell
        # object (ChannelBuff(self.cast, ...) at every game call site), so a
        # direct snapshot has no name/melee — downstream renders fell back to
        # the literal 'attack'. Unwrap __self__ to the spell object, the same
        # move the game makes for spell_name (Level.py:1527).
        spell_obj = getattr(self.spell, '__self__', None) or self.spell
        cast_record = journal.begin_chain({
            'caster': _snapshot_unit(self.owner),
            'spell': _snapshot_spell(spell_obj),
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
        # Keep the active-level handle current at tick roots too (execute_cast
        # and queue_spell already maintain it) — the container sweep triggered
        # by this push/pop needs a level to walk, and on a fresh floor the
        # first tick can precede the first cast.
        _tick_level = getattr(getattr(self, 'owner', None), 'level', None)
        if _tick_level is not None:
            journal._level = _tick_level
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
        _tick_level = getattr(self, 'level', None)
        if _tick_level is not None:
            journal._level = _tick_level
        journal.push(cause_record)
        try:
            return original_cloud_advance(self)
        finally:
            journal.pop()

    def patched_setattr(self, name, value):
        # The ONE complete capture point for no-event, player-visible state
        # (shields, team, cur_hp, max_hp). Every assignment to a watched attr —
        # add_shields, remove_shields, the block's inline decrement, content
        # shield direct-writes, team flips (Dominate/conversions/Treachery), and
        # raw cur_hp/max_hp changes (Crisis Charm, Soulbound, Ruby Heart, undeath,
        # ...) — routes through here. This is the taxonomy's universal chokepoint.
        #
        # Discipline (this mediates EVERY unit attribute write, so a bug breaks
        # the whole game): the store ALWAYS runs via the captured original;
        # observation is fully try/except-guarded and can never prevent it; and
        # nothing here sets an attribute on `self` except through
        # original_unit_setattr (any `self.x = ...` would re-enter and recurse).
        # Old value is read from __dict__ directly (no __getattribute__ games).
        if name not in _WATCHED_ATTRS:
            original_unit_setattr(self, name, value)
            return
        before = self.__dict__.get(name)
        original_unit_setattr(self, name, value)
        try:
            # Suppression, checked FIRST (cheap) so the hot deal_damage path —
            # which writes cur_hp on every damage/heal instance — short-circuits
            # before the O(n) _is_live_unit scan. _suppress_watched_capture (all
            # attrs) gates Unit.refresh()'s reset + the shield-setter brackets;
            # _suppress_cur_hp_capture (cur_hp only) gates deal_damage's evented
            # cur_hp writes. _is_live_unit (unit in level.units) then gates out
            # construction / off-field writes while still catching on-summon grants
            # (R7) and dropping killed/removed-unit writes (R22, reincarnation).
            if journal._suppress_watched_capture:
                return
            if name == 'cur_hp' and journal._suppress_cur_hp_capture:
                return
            if not _is_live_unit(self):
                return
            after = self.__dict__.get(name)
            classified = _classify_watched(self, name, before, after)
            if classified is not None:
                event_type, payload = classified
                journal.record(event_type, payload)
        except Exception:
            pass

    def patched_add_shields(self, shields, cap=True):
        # Level.Unit.add_shields does `self.shields += n` then `min(...,20)` cap
        # (Level.py:2501-2502) — TWO __setattr__ fires for one logical grant when
        # it crosses the 20 cap (a phantom inflated gain, then a correcting
        # strip). Bracket the body so the interceptor ignores the intermediate
        # writes, then emit ONE net-diff record via _emit_watched_net. The ~96
        # raw `.shields =` content direct-writes bypass this method and still
        # capture per-write via the interceptor. Discipline mirrors
        # patched_unit_refresh: the flag always restores, even on raise.
        before = self.__dict__.get('shields')
        prev = journal._suppress_watched_capture
        journal._suppress_watched_capture = True
        try:
            original_add_shields(self, shields, cap=cap)
        finally:
            journal._suppress_watched_capture = prev
        _emit_watched_net(self, 'shields', before)

    def patched_remove_shields(self, shields):
        # Symmetric to patched_add_shields: remove_shields does `-= n` then a
        # `< 0 -> 0` clamp (Level.py:2507-2508), a double-fire on underflow. NB
        # the block path decrements shields INLINE (Level.py:4061), NOT via this
        # method, so the shield_blocked detail (patched_deal_damage) is
        # unaffected. remove_shields early-returns when shields is 0, so a no-op
        # strip stays a no-op (net before==after -> no record).
        before = self.__dict__.get('shields')
        prev = journal._suppress_watched_capture
        journal._suppress_watched_capture = True
        try:
            original_remove_shields(self, shields)
        finally:
            journal._suppress_watched_capture = prev
        _emit_watched_net(self, 'shields', before)

    def patched_unit_refresh(self):
        # Unit.refresh (Level.py:2510) zeroes shields as part of a full reset at
        # level transitions / deploy / respawn — NOT a combat strip. Bracket it
        # so the __setattr__ interceptor ignores that shields=0 write (and any
        # other watched resets refresh performs), preventing a phantom "shields
        # stripped". The flag always resets, even if refresh raises.
        prev = journal._suppress_watched_capture
        journal._suppress_watched_capture = True
        try:
            return original_unit_refresh(self)
        finally:
            journal._suppress_watched_capture = prev

    def patched_deal_damage(self, x, y, amount, damage_type, source,
                            flash=True, redirect=False):
        # Block DETAIL only (the gain/strip magnitude is owned by the
        # __setattr__ interceptor). The game's DMG_BLOCKED log (text.py:245)
        # shows the blocked amount + type + source, but EventOnShieldRemoved
        # carries none of it — those live only as locals here. Detect the block
        # PRECISELY by whether that event fired for this unit during the call
        # (it's raised only at the real block branch, Level.py:4066), recompute
        # the post-resist "would have been" amount as the game does, read the
        # remaining count off the same target reference, and attribute via the
        # cause stack (player attack chain -> outgoing block; wizard target ->
        # incoming).
        target = self.get_unit_at(x, y)
        seq_before = journal.sequence
        # Bracket cur_hp capture: deal_damage's cur_hp writes (Level.py:4073/4129/
        # 4135) are the EVENTED HP changes (EventOnDamaged 4119 / EventOnHealed
        # 4087), already captured — so suppress the interceptor's silent-heal
        # capture here. cur_hp-ONLY: the block's inline `shields -= 1` (Level.py:
        # 4061) still captures (the shield_blocked detail below depends on it).
        # Save/restore handles nested deal_damage (a handler re-damaging inside
        # this call). shields/team/max_hp are unaffected.
        prev_cur_hp = journal._suppress_cur_hp_capture
        journal._suppress_cur_hp_capture = True
        try:
            result = original_deal_damage(self, x, y, amount, damage_type, source,
                                          flash=flash, redirect=redirect)
        finally:
            journal._suppress_cur_hp_capture = prev_cur_hp
        try:
            if target is not None and _claim_block_event(
                    journal.records, seq_before, id(target)):
                shields_after = getattr(target, 'shields', 0) or 0
                blocked_amount = _resist_blocked_amount(
                    getattr(target, 'resists', None), damage_type, amount)
                journal.record(
                    'shield_blocked',
                    _shield_blocked_payload(
                        target, blocked_amount, damage_type, source, shields_after),
                )
                # The block's inline shields-=1 also logged a generic strip via
                # the interceptor — mark it superseded so it isn't double-voiced.
                _supersede_block_strip(journal.records, seq_before, id(target))
        except Exception:
            pass
        return result

    Level.CastContext = _TrackedCastContext
    Level.Level.act_cast = patched_act_cast
    Level.Level.defer_cast = patched_defer_cast
    Level.Level.execute_cast = patched_execute_cast
    Level.Level.process_pending_casts = patched_process_pending_casts
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
    Level.Unit.__setattr__ = patched_setattr
    Level.Unit.refresh = patched_unit_refresh
    Level.Unit.add_shields = patched_add_shields
    Level.Unit.remove_shields = patched_remove_shields
    Level.Level.deal_damage = patched_deal_damage

    journal._hooks_installed = True
