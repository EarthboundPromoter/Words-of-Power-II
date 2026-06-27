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


def _claim(record):
    """Stamp CRISIS_MARK on a record so other producers skip it."""
    marks = record.setdefault('marks', [])
    if CRISIS_MARK not in marks:
        marks.append(CRISIS_MARK)


def _has_crisis_mark(record):
    return CRISIS_MARK in (record.get('marks') or [])


# ----------------------------------------------------------------------
# Per-category render helpers — pure functions over journal record dicts.
# Each returns the rendered line string, or None if the record doesn't
# qualify for that category.
# ----------------------------------------------------------------------


def _render_damage_taken(record):
    """EventOnDamaged where target is wizard. Wizard-prefix form."""
    if record.get('event_type') != 'EventOnDamaged':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    damage = payload.get('damage')
    dtype = payload.get('damage_type')
    source = payload.get('source_name')
    if damage is None or damage <= 0:
        # Defensive: shielded / 0-damage hits surface elsewhere; skip here.
        return None
    dtype_str = f" {dtype}" if dtype else ""
    if source:
        return f"Wizard took {damage}{dtype_str} from {source}."
    return f"Wizard took {damage}{dtype_str}."


def _render_buff_applied(record):
    """EventOnBuffApply where target is wizard AND buff is a debuff (curse).
    Self-buffs on wizard go to the orphan producer's equipment passives /
    digest's Side, not crisis."""
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
    if turns and turns > 0:
        return f"Wizard {adj}, {turns} turns."
    return f"Wizard {adj}."


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


def _render_wizard_death(record):
    """EventOnDeath where target is wizard. Terminal event."""
    if record.get('event_type') != 'EventOnDeath':
        return None
    payload = record.get('payload') or {}
    target = payload.get('target') or {}
    if not _is_wizard_snap(target):
        return None
    return "Wizard died."


def _render_displaced(record):
    """EventOnMoved where target is wizard AND teleport=True. Sudden
    positional change by external cause (push, enemy teleport)."""
    if record.get('event_type') != 'EventOnMoved':
        return None
    payload = record.get('payload') or {}
    unit = payload.get('unit') or {}
    if not _is_wizard_snap(unit):
        return None
    if not payload.get('teleport'):
        return None
    x = unit.get('x')
    y = unit.get('y')
    return f"Wizard displaced to ({x},{y})."


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

    def fire(self, journal_records, wizard_unit, log_fn, telemetry=None):
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

        new_records = [
            r for r in journal_records
            if r.get('sequence') is not None
            and r.get('sequence') > self._last_processed_seq
            and not _has_crisis_mark(r)
        ]
        if not new_records and wizard_unit is None:
            return (PRIORITY_CRITICAL, "")

        # Advance the "processed" cursor regardless of whether we emit;
        # records we skip this turn won't be re-scanned next turn.
        if journal_records:
            max_seq = max(
                r.get('sequence', -1) for r in journal_records
            )
            self._last_processed_seq = max(
                self._last_processed_seq, max_seq
            )

        lines = []
        categories_present = set()

        for rec in new_records:
            line = _render_damage_taken(rec)
            if line:
                lines.append(line)
                categories_present.add('damage_taken')
                _claim(rec)
                continue
            line = _render_buff_applied(rec)
            if line:
                lines.append(line)
                categories_present.add('debuff_applied')
                _claim(rec)
                continue
            line = _render_buff_faded(rec)
            if line:
                lines.append(line)
                categories_present.add('buff_faded')
                _claim(rec)
                continue
            line = _render_wizard_death(rec)
            if line:
                lines.append(line)
                categories_present.add('wizard_death')
                _claim(rec)
                continue
            line = _render_displaced(rec)
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

        # HP threshold — check after damage events processed so the
        # threshold reflects post-turn HP. Wizard reference required.
        threshold_line = self._maybe_threshold_line(wizard_unit)
        if threshold_line:
            lines.append(threshold_line)
            categories_present.add('hp_threshold')

        text = " ".join(lines).strip()

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

    def _maybe_emit_unmodeled(self, telemetry, scanned_records, output):
        """Surface forensic telemetry when crisis processed records but
        produced empty output (suggests an event type the producer
        should claim but doesn't have a render branch for)."""
        wizard_records = [
            r for r in scanned_records
            if (
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
