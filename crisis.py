"""
Crisis producer — foregrounds player-state-change events at the top of
each turn's utterance via the Wizard-prefix convention.

Walks journal records since last fire, claims events where the target is
the wizard. Renders Wizard-prefix lines per `design_wizard_prefix_convention.md`.
Stamps `crisis_v1` mark so the digest and orphan producers respect the
claim and don't re-narrate.

Crisis tier scope (7 categories):
- Damage taken by wizard
- HP threshold crossings (half / quarter / tenth)
- Debuff applied to wizard (apply-time)
- Buff fading from wizard (fade-time)
- Wizard death
- Wizard standing in active cloud (per-turn with duration; agency rule)
- Wizard displaced (push / teleport)

See `design_orphan_composer_phrasing.md` for the full pipeline architecture
and `design_critical_tier_agency_rule.md` for the per-turn renotification
scope rule.

DOTs on wizard are NOT renotified per turn by default — only the apply-time
announcement fires. Config flag `dot_renotify_enabled` flips this on for
users who want continuous DOT awareness during multi-DOT pile-ons.
"""


CRISIS_MARK = "crisis_v1"

# Sort key for the unified emitter: lower number = higher priority (earlier
# in the spoken utterance). Crisis sits at 0; digest at 100; orphan at 200.
# These constants live here so producers self-tag and the emitter is purely
# a sort+join.
PRIORITY_CRITICAL = 0


# HP threshold percentages, sorted high → low. The producer crosses one
# threshold per fire at most; if HP drops past multiple thresholds in a
# single turn the lowest-crossed one is announced.
_HP_THRESHOLDS = [
    (0.5, "half"),
    (0.25, "quarter"),
    (0.1, "tenth"),
]


# Wizard-buff phrasings. Some debuffs read better as adjectives ("petrified")
# than as the buff's literal name ("Petrified"). The producer falls back to
# lowercased buff name for unmapped debuffs, which works for the common cases
# (Stunned → stunned, Frozen → frozen, Silenced → silenced, Cursed → cursed).
# Override entries here when the lowercase form reads awkwardly.
_DEBUFF_PHRASING = {
    # buff_name -> adjective form
}


def _is_wizard_snap(snap):
    """True if a unit-snapshot dict refers to the player wizard."""
    return bool(snap and snap.get('is_player_controlled'))


# Unit 4 (G-G/G-F/G-M) capture-only record kinds, staged for the composer
# phase (Track B wizard-highlight / mass-aggregation). Excluded from the
# crisis unmodeled-telemetry scan — see _maybe_emit_unmodeled. Mirror of the
# digest's known-set additions (digest._COMPOSER_KNOWN_EVENT_TYPES).
_STAGED_CAPTURE_ONLY_KINDS = frozenset({
    'hp_loss', 'xp_change', 'EventOnAwakened',
    # Root-1 container-diff kinds (Unit 1, capture-only, composer-staged).
    # These payloads carry unit snapshots — wizard-subject instances land
    # on routine turns constantly (every wizard buff apply folds resists;
    # every cast decrements charges), so counting them would turn
    # wizard_records_no_output into noise, exactly like hp_loss/xp_change.
    'resists_change', 'tags_change', 'stat_bonus_change',
    'charges_change', 'cooldown_change', 'lifespan_change',
    # Unit 5 D3 (tile-keyed payload, no unit snapshot — listed for
    # ALL_KINDS uniformity with the digest set).
    'tile_flavor_change',
    # G-H/G-I attrs fix (2026-07-03 ruling, capture-only). These payloads
    # DO carry 'target' unit snapshots (shield/team precedent), and future
    # content could write them on the wizard — staged so a wizard-subject
    # instance never counts as wizard_records_no_output noise.
    'flight_gained', 'flight_lost', 'unit_renamed', 'sprite_change',
    'debuff_immunity_gained', 'debuff_immunity_lost',
})


# Slice 1 stage A: while a fire() is running this holds the list
# collecting the sequences of records claimed by THAT fire — the
# per-fire item's source refs. None outside a fire. Crisis renders
# line-by-line through many claim sites, so the collector rides the
# claim chokepoint instead of threading a parameter through every
# renderer. Single-threaded by construction (one producer singleton,
# fired only from the pipeline boundary).
_fire_claim_seqs = None


def _claim(record):
    """Stamp CRISIS_MARK on a record so other producers skip it."""
    marks = record.setdefault('marks', [])
    if CRISIS_MARK not in marks:
        marks.append(CRISIS_MARK)
        # Slice 0: feed the pipeline's double-claim watchdog (it drains
        # noted records instead of walking the whole level list).
        from journal import journal as _j
        _j.note_producer_mark(record)
        if _fire_claim_seqs is not None:
            seq = record.get('sequence')
            if seq is not None:
                _fire_claim_seqs.append(seq)


def _has_crisis_mark(record):
    return CRISIS_MARK in (record.get('marks') or [])


def _walk_to_root(record, idx):
    """Walk parent links to the absolute chain root (parent is None).
    `idx` maps sequence -> record (digest.build_record_index). Used by
    the wizard-POSITIVE guard (heal / buff-gain) and the B4 chain-caster
    walk; damage and shield collapse never consult it."""
    cur = record
    guard = 0
    while cur is not None and guard < 100000:
        guard += 1
        parent_seq = cur.get('parent')
        if parent_seq is None:
            return cur
        cur = idx.get(parent_seq)
    return cur


def _chain_caster_name(record, idx):
    """Name the actor at the root of this record's causation chain — the
    enemy whose cast/aura produced a wizard-facing non-damage effect
    (debuff, external displace). Used by the B4 attribution path as the
    fallback when the buff didn't carry its own source.

    cast_begin root -> the caster; buff_tick/equipment_tick root -> the
    aura/gear owner; anything else (cloud_tick, bare event) -> None
    (anonymous, matching the original design's floor). Never names the
    wizard as the applier of an effect on the wizard — a deferred reaction
    whose chain roots in the player's own keypress must not read as
    self-inflicted."""
    root = _walk_to_root(record, idx)
    if root is None:
        return None
    et = root.get('event_type')
    payload = root.get('payload') or {}
    if et == 'cast_begin':
        caster = payload.get('caster') or {}
        if caster.get('is_player_controlled'):
            return None
        return caster.get('name')
    if et in ('buff_tick', 'equipment_tick'):
        owner = payload.get('owner') or {}
        if owner.get('is_player_controlled'):
            return None
        return owner.get('name')
    return None


# ----------------------------------------------------------------------
# Per-category render helpers — pure functions over journal record dicts.
# Each returns the rendered line string, or None if the record doesn't
# qualify for that category.
# ----------------------------------------------------------------------


# Generic attack names the game hardcodes for basic melee/ranged
# (CommonContent.py:39 "Melee Attack", :128 "Ranged Attack") — pure
# boilerplate shared by ~160 monsters. When the source is one of these we
# name just the attacker, not "{Attacker}'s Melee Attack". Named attacks
# (breaths, bolts, "Bow") are meaningful and earn the possessive.
_GENERIC_ATTACK_NAMES = ("Melee Attack", "Ranged Attack")


def _attacker_phrase(payload):
    """The 'from X' object for a wizard-damage line.

    - "{Attacker}'s {attack}" (e.g. "Storm Drake's Storm Breath") when the
      source has a distinct attacking owner and a meaningful attack name.
    - "{Attacker}" alone when the attack name is generic boilerplate
      (basic melee/ranged) — adds the attacker without the noise token.
    - "{source}" (e.g. "Bleed") for DOT / temp-buff sources, whose owner is
      the afflicted unit rather than an attacker, and for ownerless or
      self-sourced damage.
    """
    source = payload.get('source_name')
    owner = payload.get('source_owner_name')
    # Bless/curse buff sources (DOTs): owner is the victim, not an attacker.
    is_temp_buff = bool(payload.get('source_is_buff')) and \
        payload.get('source_buff_type') in (1, 2)
    target = payload.get('target') or {}
    if (owner and not is_temp_buff
            and owner != source
            and owner != target.get('name')):
        if source and source not in _GENERIC_ATTACK_NAMES:
            return f"{owner}'s {source}"
        return owner
    return source


def _render_damage_taken(record):
    """EventOnDamaged where target is wizard. Wizard-prefix form. Names the
    attacker (source.owner) plus the specific attack when meaningful — see
    _attacker_phrase. This path is NOT chain-gated: wizard damage, including
    in-chain self-damage, always speaks."""
    if record.get('event_type') != 'EventOnDamaged':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    damage = payload.get('damage')
    dtype = payload.get('damage_type')
    if damage is None or damage <= 0:
        # Defensive: shielded / 0-damage hits surface elsewhere; skip here.
        return None
    dtype_str = f" {dtype}" if dtype else ""
    attacker = _attacker_phrase(payload)
    if attacker:
        return f"Wizard took {damage}{dtype_str} from {attacker}."
    return f"Wizard took {damage}{dtype_str}."


def _render_buff_applied(record, caster_name=None):
    """EventOnBuffApply where target is wizard AND buff is a debuff (curse).
    Self-buffs on wizard go to the orphan producer's equipment passives /
    digest's Side, not crisis. `caster_name` (B4) names the applier when
    known — appended as ", by {caster}"; omitted (anonymous) when None."""
    if record.get('event_type') != 'EventOnBuffApply':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    buff = payload.get('buff') or {}
    # Refresh records use a flag; for first-pass we treat refreshes the
    # same as initial applies — listener still wants to know.
    name = buff.get('name')
    if not name:
        return None
    # buff_type 2 = curse / debuff. Self-buffs (passive=0, bless=1, item=3)
    # are NOT crisis content; they fall through to the orphan composer.
    btype = buff.get('buff_type')
    if btype != 2:
        return None
    turns = buff.get('turns_left')
    adj = _DEBUFF_PHRASING.get(name, name.lower())
    by = f", by {caster_name}" if caster_name else ""
    if turns and turns > 0:
        return f"Wizard {adj}, {turns} turns{by}."
    return f"Wizard {adj}{by}."


def _render_buff_faded(record):
    """EventOnBuffRemove where target is wizard. Renders fade-time
    notification — but only for the cases listener cares about."""
    if record.get('event_type') != 'EventOnBuffRemove':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    # Skip silent unit-removed cases (these are buff cleanup on death;
    # not a "fade" the player should hear about as a crisis).
    if payload.get('is_unit_removed'):
        return None
    buff = payload.get('buff') or {}
    name = buff.get('name')
    if not name:
        return None
    return f"Wizard's {name} faded."


def _render_wizard_death(record, idx=None):
    """EventOnDeath where target is wizard. Terminal event.

    Attribution is within-turn causal ONLY (owner ruling 2026-07-10): the
    payload's killing fields first, then a walk to this record's own chain
    root — never a cross-turn history search. The forms follow the game's
    death line ("{unit} killed by {owner} {source}", text.py:247), with two
    deliberate corrections:

    - DOT recovery: Buff.owner is the afflicted unit (Level.py:1137-1139),
      so the vanilla log prints "Wizard killed by Wizard Poison" for a DOT
      kill. A curse on the wizard renders as its applier when capture
      recovered one ("killed by Poison, from Goblin Shaman"), anonymous
      otherwise — never the bearer-as-actor form.
    - Self-kills stay attributed: unlike B4's _chain_caster_name, this path
      DOES name the wizard as the actor ("killed by own Fire Storm") — the
      game's own line attributes self-kills, and hiding that would suppress
      game-shown truth.
    """
    if record.get('event_type') != 'EventOnDeath':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None

    source = payload.get('killing_source')
    owner = payload.get('source_owner_name')
    wiz_name = target.get('name')

    if source:
        # Buff sitting on the wizard: owner is the bearer, not an actor.
        if payload.get('source_is_buff') and owner == wiz_name:
            # buff_type 2 = curse / debuff: enemy-applied.
            if payload.get('source_buff_type') == 2:
                caster = payload.get('killing_source_caster')
                if caster:
                    return f"Wizard killed by {source}, from {caster}."
                return f"Wizard killed by {source}."
            # bless / passive / item on the wizard: the wizard's own effect.
            return f"Wizard killed by own {source}."
        if owner == wiz_name:
            # Own spell, direct (game: "Wizard killed by Wizard X").
            return f"Wizard killed by own {source}."
        if owner:
            # Direct hit: the game's KILLED_BY_UNIT juxtaposition.
            return f"Wizard killed by {owner} {source}."
        # Ownerless source: environmental (game: KILLED_BY_ENV).
        return f"Wizard killed by {source}."

    # No killing fields (kill() without a damage event, or legacy record).
    # A within-turn chain root, when one exists, still names the actor whose
    # action the log showed this turn.
    if idx is not None:
        root = _walk_to_root(record, idx)
        if root is not None and root is not record:
            et = root.get('event_type')
            rp = root.get('payload') or {}
            if et == 'cast_begin':
                cname = (rp.get('caster') or {}).get('name')
                sname = (rp.get('spell') or {}).get('name')
                if sname:
                    if (rp.get('caster') or {}).get('is_player_controlled'):
                        return f"Wizard killed by own {sname}."
                    if cname:
                        return f"Wizard killed by {cname} {sname}."
                    return f"Wizard killed by {sname}."
            if et in ('buff_tick', 'equipment_tick'):
                rowner = rp.get('owner') or {}
                bname = (rp.get('buff') or {}).get('name')
                if bname:
                    if rowner.get('is_player_controlled'):
                        return f"Wizard killed by own {bname}."
                    if rowner.get('name'):
                        return f"Wizard killed by {rowner['name']} {bname}."
                    return f"Wizard killed by {bname}."

    return "Wizard died."


def _render_displaced(record, caster_name=None):
    """EventOnMoved where target is wizard, by an EXTERNAL cause (push,
    pull, enemy teleport, force-swap). The player's own Blink / Lightning
    Form / gear teleport is filtered out by the out-of-chain guard at the
    call site (B3) and owned by the digest — this branch only renders
    external displacement. `caster_name` (B4) names the pusher when the
    chain-walk found one; moves carry no buff.source, so this is
    chain-walk only.

    Render gate: teleport=True (pushes/pulls/enemy teleports all set it)
    OR a caster was found. The OR catches the force-swap case — the unit
    swapped INTO a vacated tile gets EventOnMoved with teleport=False
    (Level.py:3043), so a teleport-only gate would silently drop a wizard
    that was force-swapped by an enemy effect. A teleport=False move with
    NO caster is an ordinary manual step (parent=None, out-of-chain) and
    must stay silent — hence requiring the caster on the non-teleport
    path. The flee/normal-move swap residual (teleport=False, parent=None,
    indistinguishable from a manual step) is out of scope — it needs the
    relocation-aware adjacency-tracker rework, not a gate tweak."""
    if record.get('event_type') != 'EventOnMoved':
        return None
    payload = record.get('payload') or {}
    unit = payload.get('unit') or {}
    if not _is_wizard_snap(unit):
        return None
    if not payload.get('teleport') and not caster_name:
        return None
    x = unit.get('x')
    y = unit.get('y')
    by = f" by {caster_name}" if caster_name else ""
    return f"Wizard displaced to ({x},{y}){by}."


def _render_cloud_on_wizard(record, wizard_pos):
    """cloud_tick record where the cloud is at the wizard's tile.
    Per the agency rule, this fires every turn the wizard remains
    standing in the cloud — the player decides each turn whether to
    stay or step out, so duration matters per-turn.

    Renders one line aggregating the cloud's effects this tick (damage
    + remaining duration). The cloud_tick chain may contain damage /
    buff-apply events that are also on the wizard; those are claimed
    by their respective render branches independently, leaving this
    line as the cloud-presence header.

    `wizard_pos` is a (x, y) tuple. Returns None if either coordinate
    is missing or the cloud isn't on the wizard's tile."""
    if record.get('event_type') != 'cloud_tick':
        return None
    payload = record.get('payload') or {}
    wx, wy = wizard_pos
    if wx is None or wy is None:
        return None
    if payload.get('x') != wx or payload.get('y') != wy:
        return None
    cloud_name = payload.get('cloud_name') or 'cloud'
    duration_after = payload.get('duration_after_tick')
    # The cloud's damage to the wizard appears in a child EventOnDamaged
    # record and is claimed by _render_damage_taken; this line carries
    # only the cloud-presence + remaining-duration info.
    if duration_after is not None and duration_after <= 0:
        return f"{cloud_name} ending."
    if duration_after is not None and duration_after > 0:
        return f"In {cloud_name}, {duration_after} turns left."
    return f"In {cloud_name}."


def _shields_phrase(n):
    """'1 shield' / 'N shields'."""
    return "1 shield" if n == 1 else f"{n} shields"


def _render_wizard_shield_blocked(record):
    """shield_blocked where target is wizard — the rich block report the game's
    DMG_BLOCKED log shows (amount that would have hit + type + source + shields
    left), which the thin EventOnShieldRemoved event can't carry. Always crisis
    (a blocked hit is otherwise silent — the player must know they're under fire
    and spending shields); not chain-gated."""
    if record.get('event_type') != 'shield_blocked':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    amount = payload.get('blocked_amount')
    dtype = payload.get('damage_type')
    source = payload.get('source_name')
    remaining = payload.get('shields_remaining')
    head = "Wizard blocked"
    if amount is not None and dtype:
        head += f" {amount} {dtype}"
    elif dtype:
        head += f" {dtype}"
    if source:
        head += f" from {source}"
    if remaining:
        return f"{head}, {_shields_phrase(remaining)} left."
    return f"{head}, last shield."


def _render_wizard_shield_stripped(record):
    """shield_stripped where target is wizard — a defensive loss with no
    incoming hit (Siphon, dispel). Always crisis. A block's coincident strip is
    marked superseded at capture, so it never reaches this line."""
    if record.get('event_type') != 'shield_stripped':
        return None
    if 'superseded_by_block' in (record.get('marks') or []):
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    remaining = payload.get('shields_after')
    if remaining:
        return f"Wizard shields stripped, {_shields_phrase(remaining)} left."
    return "Wizard shields stripped."


def _render_wizard_shield_gained(record):
    """shield_gained where target is wizard. Out-of-chain only — an in-chain
    self-cast shield is the digest's cast-line capstone. 'Wizard gained N
    shields, M total.'"""
    if record.get('event_type') != 'shield_gained':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    amount = payload.get('amount')
    if not amount:
        return None
    total = payload.get('shields_after')
    if total is not None:
        return f"Wizard gained {_shields_phrase(amount)}, {total} total."
    return f"Wizard gained {_shields_phrase(amount)}."


def _render_wizard_healed(record):
    """EventOnHealed where target is wizard. Covers OUT-OF-CHAIN heals (regen
    ticks, ally heal-auras) that no other producer claims; the chain-aware
    guard in fire() leaves in-chain (digest Side) and equipment-sourced heals
    to those producers."""
    if record.get('event_type') != 'EventOnHealed':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    amount = payload.get('heal_amount')
    if not amount or amount <= 0:
        return None
    source = payload.get('source_name')
    if source:
        return f"Wizard healed {amount} from {source}."
    return f"Wizard healed {amount}."


def _render_wizard_lethal_save(record):
    """Interim render for a wizard LETHAL-SAVE (R5) — a silent cur_hp rise from
    <=0 back to positive, i.e. a hit that would have killed you but didn't (Crisis
    Charm restores to full, Equipment.py:3195; Soulbound clamps to 1,
    CommonContent.py:1392). These raise no EventOnHealed and their log lines
    aren't auto-voiced, so the mod supplies the voice from the captured
    silent_heal.

    DATA-DRIVEN, not source-named: universal capture doesn't know which item
    saved you (that's cause-attribution, Track B, §3). The lethal-save predicate
    is cur_hp_before <= 0 < cur_hp_after — which cleanly distinguishes the
    must-speak saves from ordinary silent heals (Ruby Heart, components: their
    cur_hp_before is positive) that stay captured-but-inert, staged for Track B.
    The OUTCOME (restored-to-full vs survived-at-N) is read from the data. Not
    chain-gated — a death-save must always be heard. Track B re-homes this off
    crisis into the wizard-highlight + adds 'from Crisis Charm' via the cause."""
    if record.get('event_type') != 'silent_heal':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    before = payload.get('cur_hp_before')
    after = payload.get('cur_hp_after')
    if before is None or after is None or before > 0 or after <= 0:
        return None
    max_hp = payload.get('max_hp_after')
    if max_hp is not None and after >= max_hp:
        # Restored to full — report the max (a reminder of your maximum).
        return f"You would have died — restored to full, {max_hp} health."
    return f"You would have died — survived at {after} health."


def _render_wizard_buff_gained(record):
    """EventOnBuffApply where target is wizard and the buff is NOT a curse
    (buff_type != 2; curses are owned by _handle_wizard_debuff_apply). Covers
    OUT-OF-CHAIN self-buffs no other producer claims; the guard in fire()
    leaves in-chain (digest Side) and equipment-sourced buffs to them."""
    if record.get('event_type') != 'EventOnBuffApply':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    buff = payload.get('buff') or {}
    if buff.get('buff_type') == 2:
        return None
    name = buff.get('name')
    if not name:
        return None
    turns = buff.get('turns_left')
    if turns and turns > 0:
        return f"Wizard gained {name}, {turns} turns."
    return f"Wizard gained {name}."


# ----------------------------------------------------------------------
# HP threshold detection — stateful across fires.
# ----------------------------------------------------------------------


def _current_threshold_label(cur_hp, max_hp):
    """Return the highest threshold the wizard's HP has crossed below,
    or None if HP is above the half mark. Sorted high → low so we hit
    the lowest threshold first."""
    if max_hp is None or max_hp <= 0 or cur_hp is None:
        return None
    ratio = cur_hp / max_hp
    crossed = None
    for cutoff, label in _HP_THRESHOLDS:
        if ratio < cutoff:
            crossed = (cutoff, label)
        else:
            break
    return crossed


# ----------------------------------------------------------------------
# Producer
# ----------------------------------------------------------------------


class _CrisisProducer:
    """Stateful across calls: tracks the highest journal sequence
    processed and the last HP threshold announced.

    Fires once per turn boundary. Emits a single (priority, text)
    section for the unified emitter. Returns ('', empty section) when
    nothing crisis-worthy happened this turn."""

    def __init__(self):
        self._last_processed_seq = -1
        # Track HP threshold "deepest crossed" across the session so we
        # don't re-announce the same threshold each turn while the wizard
        # stays below it. Resets on heal-back-up.
        self._last_threshold_index = -1
        # Refresh/stack cadence state (Model A — see
        # docs/REFRESH_STACK_NARRATION_DESIGN.md).
        # name -> 'control' / 'silence', learned from apply-record snapshots
        # (the journal tags each buff with its agency class). Drives the
        # per-turn countdown poll, which reads LIVE buffs (no snapshot, no
        # Level import) and looks agency up by name.
        self._agency_by_name = {}
        # name -> announced severity high-water mark. A wizard debuff is
        # voiced at apply only on a NEW worst-known high (first onset, or a
        # longer remaining duration), never on flat re-application — that is
        # the noise gate. Cleared when the debuff fades.
        self._debuff_high = {}
        # dtype name -> announced effective resist low-water (a negative
        # percent). Class-4: scaling resist penalties (Melted Armor,
        # Electrified, Blood Curse, Idol of Weakness) stack at the same
        # duration, so the severity gate can't catch them — the escalation
        # lives in the resist total. Spoken on a new low; synced to the
        # post-removal effective when the debuff fades.
        self._resist_low = {}

    @staticmethod
    def _severity(turns):
        """Severity ordering for the high-water gate. A permanent debuff
        (turns_left 0 or None) is the worst possible, so it sorts above any
        finite duration."""
        return float('inf') if not turns else turns

    def fire(self, journal_records, wizard_unit, log_fn, telemetry=None,
             damage_summed=False, shared_index=None, items_sink=None):
        """Walk journal records since last fire. Identify wizard-target
        events. Stamp marks. Compose Wizard-prefix lines.

        Args:
            journal_records: list of journal record dicts (typically
                journal.records).
            wizard_unit: live wizard Unit reference (for HP threshold
                check + position lookup for cloud-at-tile detection).
                None if wizard is unavailable (e.g., between levels).
            log_fn: callable(str) for diagnostic logging.
            telemetry: optional telemetry module reference.
            damage_summed: cosmetic flag for repeated-hit collapse (B1) —
                False reports per-hit value with a multiplier, True the total.

        Returns:
            (priority, text) tuple. text is empty string if no crisis
            content this turn.
        """
        if wizard_unit is not None:
            wizard_pos = (
                getattr(wizard_unit, 'x', None),
                getattr(wizard_unit, 'y', None),
            )
        else:
            wizard_pos = (None, None)

        # Slice 0: the tail is derived from THIS producer's own cursor by
        # sequence comparison (tail_after scans back from the end — O(new)),
        # never from a list offset or the journal's extension watermark
        # (build law 1/4). Semantics identical to the old full-list filter.
        from journal import tail_after as _tail_after
        tail = _tail_after(journal_records, self._last_processed_seq)
        new_records = [
            r for r in tail
            if r.get('sequence') is not None
            and not _has_crisis_mark(r)
        ]
        if not new_records and wizard_unit is None:
            return (PRIORITY_CRITICAL, "")

        # Advance the "processed" cursor regardless of whether we emit;
        # records we skip this turn won't be re-scanned next turn.
        # (Append-order sequences: the tail's last record carries its max.)
        if tail:
            self._last_processed_seq = max(
                self._last_processed_seq,
                tail[-1].get('sequence', -1),
            )

        # Slice 1 stage A: arm the per-fire claim collector — the item's
        # source refs. Re-armed (fresh list) at every fire, read and
        # disarmed at the emit point below; a renderer exception leaves it
        # armed but the next fire's re-arm makes that harmless (crisis
        # claims happen only inside fire).
        global _fire_claim_seqs
        _fire_claim_seqs = []

        lines = []
        categories_present = set()
        # Names voiced at apply/escalation THIS fire — the countdown poll
        # skips them so a control debuff isn't both announced and counted
        # down in the same turn.
        announced = set()

        # Wizard damage first — DOT ticks summed per source, repeated
        # identical direct hits collapsed (B1).
        damage_lines = self._compose_damage_taken(new_records, damage_summed)
        if damage_lines:
            lines.extend(damage_lines)
            categories_present.add('damage_taken')

        # Chain-aware guard for wizard-facing POSITIVES (heal / buff-gain)
        # ONLY. Crisis claims a positive only when it is neither inside a
        # player-keypress chain (digest's) nor an equipment_tick chain
        # (equipment's). Damage and shield below are unconditional and do
        # NOT use this. Lazy digest import mirrors the pipeline's pattern.
        import digest as _digest
        _idx = (shared_index.by_seq if shared_index is not None
                else _digest.build_record_index(journal_records))

        def _positive_out_of_chain(rec):
            if _digest.walk_to_keypress_root(rec, _idx) is not None:
                return False
            root = _walk_to_root(rec, _idx)
            if root is not None and root.get('event_type') == 'equipment_tick':
                return False
            return True

        # B5: a debuff re-applied this turn fires a fade (EventOnBuffRemove)
        # plus a fresh apply. Names with BOTH a wizard fade and a later wizard
        # apply this turn are refresh churn — suppress the fade so the pair
        # doesn't read "Blind faded. Wizard blind." every re-application. The
        # apply is still high-water-gated, so a same-duration refresh stays
        # silent and only a real escalation speaks.
        refreshed_names = self._refresh_churn_names(new_records)

        # Collapse multi-step forced relocation: a pull/push fires one
        # EventOnMoved per tile stepped (CommonContent.pull, teleport=True
        # each), so an N-tile pull would otherwise speak N "Wizard
        # displaced" lines. Only the final destination matters — find the
        # last out-of-chain wizard move this turn; earlier ones are claimed
        # silently in the loop below.
        last_displaced = None
        for rec in new_records:
            if (rec.get('event_type') == 'EventOnMoved'
                    and _is_wizard_snap((rec.get('payload') or {}).get('unit'))
                    and _positive_out_of_chain(rec)):
                last_displaced = rec

        # Multi-stack buffs fire one EventOnBuffRemove per stack when they
        # expire together, which spoke "Wizard's Necrosis faded." once per
        # stack (the 2026-07-02 x3 specimen). The fade fact is singular —
        # the buff is gone — matching the apply side's single collapsed
        # line, so each buff name fades at most once per window.
        spoken_fades = set()
        for rec in new_records:
            if self._handle_wizard_debuff_apply(
                    rec, lines, categories_present, announced, _idx):
                continue
            line = _render_buff_faded(rec)
            if line:
                _claim(rec)
                bname = ((rec.get('payload') or {}).get('buff') or {}).get('name')
                if bname in refreshed_names:
                    # Refresh churn: the debuff fades and re-applies the same
                    # turn. Suppress the fade AND keep the high-water mark, so
                    # the paired re-apply is still gated by severity — a flat
                    # re-up stays silent, only a real escalation speaks.
                    continue
                if bname in spoken_fades:
                    continue
                spoken_fades.add(bname)
                # Genuine fade: the debuff is gone. Drop its high-water mark so
                # a fresh application later re-announces from scratch, sync the
                # resist low-water, and speak.
                self._forget_debuff(rec)
                lines.append(line)
                categories_present.add('buff_faded')
                continue
            line = _render_wizard_death(rec, _idx)
            if line:
                lines.append(line)
                categories_present.add('wizard_death')
                _claim(rec)
                continue
            # Displacement is crisis only when EXTERNAL (B3): the player's own
            # Blink / Lightning Form / gear teleport is in-chain and owned by
            # the digest (compose_moved_section), so abstain on it. Only an
            # enemy push / pull / teleport / force-swap surfaces here, named
            # with its cause when the walk finds one.
            if (rec.get('event_type') == 'EventOnMoved'
                    and _positive_out_of_chain(rec)):
                # Intermediate step of a multi-step pull: claim, no line —
                # only last_displaced (the final tile) speaks.
                if (rec is not last_displaced
                        and _is_wizard_snap((rec.get('payload') or {}).get('unit'))):
                    _claim(rec)
                    continue
                line = _render_displaced(rec, _chain_caster_name(rec, _idx))
                if line:
                    lines.append(line)
                    categories_present.add('displaced')
                    _claim(rec)
                    continue
            line = _render_cloud_on_wizard(rec, wizard_pos)
            if line:
                lines.append(line)
                categories_present.add('cloud_on_tile')
                _claim(rec)
                continue
            # Shield block — always crisis (a blocked hit is otherwise silent);
            # not chain-gated. The rich block report supersedes the thin
            # EventOnShieldRemoved (now data-only) and the coincident strip.
            line = _render_wizard_shield_blocked(rec)
            if line:
                lines.append(line)
                categories_present.add('shield_blocked')
                _claim(rec)
                continue
            # Shield strip — always crisis (a defensive loss the player must
            # know). Block-coincident strips are superseded at capture.
            line = _render_wizard_shield_stripped(rec)
            if line:
                lines.append(line)
                categories_present.add('shield_stripped')
                _claim(rec)
                continue
            # Wizard lethal-save — always crisis (an otherwise-silent restore from
            # a would-be-lethal hit: Crisis Charm, Soulbound). Not chain-gated: a
            # death-save must always be heard. INTERIM (data-driven predicate);
            # Track B re-homes to the wizard-highlight + cause-attribution.
            line = _render_wizard_lethal_save(rec)
            if line:
                lines.append(line)
                categories_present.add('wizard_lethal_save')
                _claim(rec)
                continue
            # Wizard-facing positives — only when out-of-chain (in-chain ->
            # digest Side; equipment_tick -> equipment producer).
            if _positive_out_of_chain(rec):
                line = _render_wizard_healed(rec)
                if line:
                    lines.append(line)
                    categories_present.add('wizard_healed')
                    _claim(rec)
                    continue
                line = _render_wizard_buff_gained(rec)
                if line:
                    lines.append(line)
                    categories_present.add('buff_gained')
                    _claim(rec)
                    continue
                line = _render_wizard_shield_gained(rec)
                if line:
                    lines.append(line)
                    categories_present.add('shield_gained')
                    _claim(rec)
                    continue

        # HP threshold — check after damage events processed so the
        # threshold reflects post-turn HP. Wizard reference required.
        threshold_line = self._maybe_threshold_line(wizard_unit)
        if threshold_line:
            lines.append(threshold_line)
            categories_present.add('hp_threshold')

        # Per-turn countdown for control/agency debuffs still active on the
        # wizard (Model A agency rule — like the cloud-on-tile renotify).
        agency_lines = self._maybe_agency_lines(wizard_unit, announced)
        if agency_lines:
            lines.extend(agency_lines)
            categories_present.add('agency_countdown')

        text = " ".join(lines).strip()

        # Slice 1 stage A: one coarse item per fire (crisis is wizard-lane,
        # single-subject — per-line items are additive later). seqs = the
        # records this fire claimed; may be empty when only the polls spoke
        # (HP threshold / agency countdown have no records). Collector
        # disarmed here.
        claimed_seqs = _fire_claim_seqs or []
        _fire_claim_seqs = None
        if items_sink is not None and text:
            from composed_items import make_item
            items_sink.append(make_item(
                None, [], text, row_key='crisis.fire', seqs=claimed_seqs))

        if telemetry is not None:
            try:
                telemetry.emit(
                    'crisis_emit',
                    claimed_count=len(lines),
                    categories_present=sorted(categories_present),
                    output=text,
                    empty=not bool(text),
                )
            except Exception:
                pass
            try:
                self._maybe_emit_unmodeled(
                    telemetry, new_records, text
                )
            except Exception:
                pass

        if text:
            log_fn(f"[Crisis] composed: {text}")

        return (PRIORITY_CRITICAL, text)

    def _maybe_threshold_line(self, wizard_unit):
        """Compute current threshold and emit only if the wizard has
        descended to a new (lower) threshold since the last announcement.
        Healing back above resets the index."""
        if wizard_unit is None:
            return None
        cur_hp = getattr(wizard_unit, 'cur_hp', None)
        max_hp = getattr(wizard_unit, 'max_hp', None)
        crossed = _current_threshold_label(cur_hp, max_hp)
        if crossed is None:
            # Above any threshold — reset so next descent re-announces.
            self._last_threshold_index = -1
            return None
        # Find the index of this threshold in _HP_THRESHOLDS (sorted high→low).
        cutoff, label = crossed
        idx = next(
            (i for i, (c, _) in enumerate(_HP_THRESHOLDS) if c == cutoff),
            -1,
        )
        if idx <= self._last_threshold_index:
            # Same or higher threshold than already announced. Don't repeat.
            return None
        self._last_threshold_index = idx
        return f"Wizard at {cur_hp} HP, {label}."

    def _compose_damage_taken(self, new_records, damage_summed=False):
        """Wizard damage lines, collapsed (B1).

        DOT ticks (source is a buff, so source_turns_left is set) sum per
        (source, type) so a 3-stack Bleed reads one "Wizard took 9 Physical
        from Bleed." line. Non-DOT direct hits collapse by
        (attacker phrase, dtype, damage) — identical repeated hits (three
        Ravens pecking for 3 each) become one line with a count, instead of
        N copies. Varying magnitude splits into separate groups (the variance
        is information — resist/vulnerability — so it is preserved, not summed
        away). `damage_summed` is the cosmetic flag: False (default) keeps the
        per-hit value with a multiplier ("3 from Raven's Peck, 3 times");
        True reports the total ("9 from Raven's Peck"). Claims what it
        consumes."""
        lines = []
        dot_totals = {}   # (source, dtype) -> summed damage
        dot_order = []
        # Non-DOT direct hits: (attacker_phrase, dtype, damage) -> count.
        hit_counts = {}
        hit_order = []
        for rec in new_records:
            if rec.get('event_type') != 'EventOnDamaged':
                continue
            payload = rec.get('payload') or {}
            target = payload.get('target') or {}
            if not _is_wizard_snap(target):
                continue
            damage = payload.get('damage')
            if damage is None or damage <= 0:
                continue
            # A DOT sits on the VICTIM (its buff.owner is the wizard), so its
            # per-turn ticks sum. An attacker-owned damage aura (DamageAuraBuff)
            # also has turns_left (every Buff defaults turns_left=0), but its
            # owner is the ENEMY, not the wizard — so turns_left alone would
            # misclassify aura hits as the wizard's own DOT and sum them across
            # separate aura-bearers. Gate the DOT branch on the source actually
            # sitting on the wizard; everything else collapses as a direct hit
            # (which names the attacker via _attacker_phrase).
            is_dot = (
                payload.get('source_turns_left') is not None
                and payload.get('source_owner_name') == target.get('name')
            )
            if is_dot:
                # DOT tick — aggregate by source and damage type.
                key = (payload.get('source_name'), payload.get('damage_type'))
                if key not in dot_totals:
                    dot_order.append(key)
                    dot_totals[key] = 0
                dot_totals[key] += damage
                _claim(rec)
            else:
                # Direct hit — collapse identical (attacker, dtype, damage).
                attacker = _attacker_phrase(payload)
                dtype = payload.get('damage_type')
                key = (attacker, dtype, damage)
                if key not in hit_counts:
                    hit_order.append(key)
                    hit_counts[key] = 0
                hit_counts[key] += 1
                _claim(rec)
        for key in hit_order:
            attacker, dtype, damage = key
            count = hit_counts[key]
            dtype_str = f" {dtype}" if dtype else ""
            frm = f" from {attacker}" if attacker else ""
            if count == 1:
                lines.append(f"Wizard took {damage}{dtype_str}{frm}.")
            elif damage_summed:
                lines.append(f"Wizard took {damage * count}{dtype_str}{frm}.")
            else:
                lines.append(
                    f"Wizard took {damage}{dtype_str}{frm}, {count} times."
                )
        for key in dot_order:
            source, dtype = key
            total = dot_totals[key]
            dtype_str = f" {dtype}" if dtype else ""
            if source:
                lines.append(f"Wizard took {total}{dtype_str} from {source}.")
            else:
                lines.append(f"Wizard took {total}{dtype_str}.")
        return lines

    @staticmethod
    def _refresh_churn_names(new_records):
        """Buff names that this turn have BOTH a wizard-target fade
        (EventOnBuffRemove) AND a wizard-target apply (EventOnBuffApply) —
        i.e. a re-application that RW3 expressed as remove+apply rather than a
        silent refresh. The fade line for these is suppressed (B5) so the pair
        doesn't narrate 'X faded. Wizard X.' on every re-up. Unit-removed
        fades (death cleanup) are not churn and don't count."""
        faded = set()
        applied = set()
        for rec in new_records:
            payload = rec.get('payload') or {}
            target = payload.get('target') or {}
            if not _is_wizard_snap(target):
                continue
            name = (payload.get('buff') or {}).get('name')
            if not name:
                continue
            et = rec.get('event_type')
            if et == 'EventOnBuffRemove' and not payload.get('is_unit_removed'):
                faded.add(name)
            elif et == 'EventOnBuffApply':
                applied.add(name)
        return faded & applied

    def _handle_wizard_debuff_apply(self, rec, lines, categories, announced,
                                    idx=None):
        """Claim and conditionally voice an EventOnBuffApply whose target is
        the wizard and whose buff is a debuff (curse). Returns True if the
        record was ours (claimed), False to let other branches try it.

        Voicing is gated by a per-buff high-water mark: the apply line fires
        only on a new worst-known severity (first onset, or a longer remaining
        duration), never on flat re-application. That single gate kills the
        sustained-aura chatter (Fear/Poison re-applied every turn) while still
        announcing real onset and escalation. Control debuffs also feed the
        agency cache for the per-turn countdown poll."""
        if rec.get('event_type') != 'EventOnBuffApply':
            return False
        payload = rec.get('payload') or {}
        target = payload.get('target') or {}
        if not _is_wizard_snap(target):
            return False
        buff = payload.get('buff') or {}
        name = buff.get('name')
        # buff_type 2 = curse / debuff. Self-buffs are not crisis content.
        if not name or buff.get('buff_type') != 2:
            return False

        # Learn the agency class for the countdown poll (which reads live
        # buffs and has no snapshot to consult).
        agency = buff.get('agency')
        if agency in ('control', 'silence'):
            self._agency_by_name[name] = agency

        # This debuff apply is crisis's to own — claim it even when the line
        # is suppressed, so the orphan producer doesn't re-narrate it.
        _claim(rec)

        severity = self._severity(buff.get('turns_left'))
        prev = self._debuff_high.get(name)
        if prev is None or severity > prev:
            # B4: name the applier. Prefer the buff's own source (set at
            # capture, deferred-proof) and fall back to the chain root's
            # caster; anonymous when neither resolves.
            caster = buff.get('source_caster')
            if not caster and idx is not None:
                caster = _chain_caster_name(rec, idx)
            line = _render_buff_applied(rec, caster)
            if line:
                lines.append(line)
                categories.add('debuff_applied')
                announced.add(name)
            self._debuff_high[name] = severity

        # Class-4: a deepening resist penalty has no damage tick to reveal
        # it, so read the effective resist total the game shows.
        self._maybe_resist_lines(buff, lines, categories)
        return True

    def _maybe_resist_lines(self, buff_snap, lines, categories):
        """Emit '{Type} resistance now -N%.' when a debuff drives the
        wizard's effective resist for a damage type to a new negative low.
        Reads the effective total captured at apply time (the character-sheet
        value), gated by a per-type low-water mark so a flat re-application or
        a non-deepening stack stays silent."""
        penalties = buff_snap.get('resist_penalty') or {}
        for dtype, effective in penalties.items():
            if effective is None or effective >= 0:
                continue
            prev = self._resist_low.get(dtype)
            if prev is None or effective < prev:
                lines.append(f"{dtype} resistance now {effective}%.")
                categories.add('resist_penalty')
                self._resist_low[dtype] = effective

    def _forget_debuff(self, rec):
        """Drop a faded debuff's high-water mark so a later re-application
        re-announces from onset, and sync the resist low-water to the
        post-removal effective totals (Buff.unapply has already subtracted
        this buff's contribution by EventOnBuffRemove time)."""
        buff = ((rec.get('payload') or {}).get('buff')) or {}
        name = buff.get('name')
        if name is not None:
            self._debuff_high.pop(name, None)
        for dtype, effective in (buff.get('resist_penalty') or {}).items():
            if effective is None or effective >= 0:
                # Recovered to no penalty — a later debuff re-announces.
                self._resist_low.pop(dtype, None)
            else:
                # Partial recovery (other stacks remain): raise the floor so
                # the next deepening re-announces from the current level.
                self._resist_low[dtype] = effective

    def _maybe_agency_lines(self, wizard_unit, announced):
        """Per-turn countdown for control/agency debuffs active on the wizard.
        Reads LIVE buffs (post-advance, so turns_left is the remaining count)
        and emits 'Still stunned, N turns left' for each control/silence buff
        — except those already voiced at apply/escalation this turn (in
        `announced`), so onset and countdown never double up."""
        if wizard_unit is None:
            return []
        buffs = getattr(wizard_unit, 'buffs', None)
        if not buffs:
            return []
        out = []
        seen = set()
        for buff in buffs:
            name = getattr(buff, 'name', None)
            if not name or name in seen or name in announced:
                continue
            if self._agency_by_name.get(name) not in ('control', 'silence'):
                continue
            seen.add(name)
            adj = _DEBUFF_PHRASING.get(name, name.lower())
            turns = getattr(buff, 'turns_left', None)
            if turns:
                turn_word = "turn" if turns == 1 else "turns"
                out.append(f"Still {adj}, {turns} {turn_word} left.")
            else:
                out.append(f"Still {adj}.")
        return out

    def _maybe_emit_unmodeled(self, telemetry, scanned_records, output):
        """Surface forensic telemetry when crisis processed records but
        produced empty output (suggests an event type the producer
        should claim but doesn't have a render branch for).

        Unit-4 capture-only kinds are excluded from the scan: wizard-subject
        instances land on quiet turns constantly (every HP-cost cast leaves a
        spend-superseded hp_loss; every SP pickup an xp_change), and they are
        deliberately staged for the composer phase, not missing crisis
        branches — counting them would make wizard_records_no_output fire as
        routine noise instead of a diagnostic."""
        wizard_records = [
            r for r in scanned_records
            if r.get('event_type') not in _STAGED_CAPTURE_ONLY_KINDS
            and (
                _is_wizard_snap((r.get('payload') or {}).get('target'))
                or _is_wizard_snap((r.get('payload') or {}).get('unit'))
            )
        ]
        if wizard_records and not output:
            telemetry.emit(
                'crisis_unmodeled',
                reasons=['wizard_records_no_output'],
                wizard_record_count=len(wizard_records),
                event_types=sorted({
                    r.get('event_type') for r in wizard_records
                    if r.get('event_type')
                }),
            )


# Module-level singleton — there is exactly one crisis producer per session.
producer = _CrisisProducer()
