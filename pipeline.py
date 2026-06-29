"""
Unified composer pipeline — coordinates the four section producers
(crisis, digest, equipment, orphan) at each turn boundary and emits a
single spoken utterance per turn.

Producer ordering:
1. Crisis claims first by event-content (target=wizard etc.). Stamps
   crisis_v1 on claimed records. Produces a tagged section at priority 0.
2. Digest claims player-keypress chains. Stamps digest_v1. Produces a
   tagged section at priority 100.
3. Equipment claims equipment_tick chains. Stamps equipment_v1.
   Produces a tagged section at priority 150 — gear narrative renders
   between the player's keypress narrative and the ambient body.
4. Orphan claims everything else (orphan records with no prior mark).
   Stamps orphan_v1. Produces a tagged section at priority 200.

The emitter sorts the four sections by priority, joins non-empty texts
with a single space, and calls tts.speak ONCE. Single TTS call per turn
keeps the spoken utterance cohesive (no inter-utterance pauses from
multiple invocations of the screen reader's speech queue).

See `design_orphan_composer_phrasing.md` for the full architectural spec.

Each producer is config-gated; disabled producers are skipped entirely
(no marks stamped, no telemetry fired). This is the strangler-fig
control surface — flip producer flags individually for parallel-mode
validation, then flip `legacy_batcher_combat_enabled` to false to
finalize the rollout.
"""


def _safe_fire(name, fn, log_fn):
    """Run a producer's fire/compose method, catching exceptions so a
    crash in one producer doesn't take down the whole pipeline. Returns
    a (priority, text) tuple, or (priority, "") on failure."""
    try:
        section = fn()
        if not section:
            return None
        priority, text = section
        return (priority, text or "")
    except Exception as e:
        log_fn(f"[Pipeline] {name} fire failed: {e!r}")
        return None


def fire_pipeline(tts, log_fn, cfg, wizard_unit, telemetry=None):
    """Fire all enabled producers in mark-precedence order, then emit
    one composed utterance.

    Args:
        tts: object with .speak(text) — async TTS interface.
        log_fn: callable(str) for diagnostic logging.
        cfg: settings object with .crisis_enabled / .digest_enabled /
            .equipment_enabled / .orphan_enabled / .show_coordinates flags.
        wizard_unit: live wizard Unit reference (for crisis HP threshold
            and cloud-at-tile detection). May be None between levels.
        telemetry: optional telemetry module reference. Forwarded to each
            producer for crisis_emit / digest_emit / orphan_emit and the
            three sibling unmodeled events. The pipeline itself emits
            composer_double_claimed when the cross-producer mark
            invariant is violated (any record carries marks from two
            producers — should be zero in healthy operation).
    """
    # Lazy imports keep this module free of cross-cutting dependencies
    # during test collection. journal indirectly imports Level which is
    # game-only.
    import crisis as _crisis
    import digest as _digest
    import equipment as _equipment
    import orphan as _orphan
    from journal import journal

    sections = []

    if cfg.crisis_enabled:
        section = _safe_fire(
            'crisis',
            lambda: _crisis.producer.fire(
                journal.records, wizard_unit, log_fn, telemetry=telemetry,
                damage_summed=getattr(cfg, 'crisis_damage_summed', False),
            ),
            log_fn,
        )
        if section is not None:
            sections.append(section)

    if cfg.digest_enabled:
        section = _safe_fire(
            'digest',
            lambda: _digest.composer.compose_section(log_fn, telemetry=telemetry),
            log_fn,
        )
        if section is not None:
            sections.append(section)

    # Equipment producer: gear narrative between digest (player keypress)
    # and orphan (ambient). Gated independently from orphan because gear
    # narrative and ambient enemy narrative evolve on different timelines
    # and may want to ship to default-on at different times.
    if cfg.equipment_enabled:
        section = _safe_fire(
            'equipment',
            lambda: _equipment.producer.fire(
                journal.records, cfg.show_coordinates,
                log_fn, telemetry=telemetry,
            ),
            log_fn,
        )
        if section is not None:
            sections.append(section)

    if cfg.orphan_enabled:
        # Wizard position drives the orphan composer's proximity/LoS ordering
        # (R2). None between levels (no spatial frame) — the producer then
        # falls back to rank order with no 'Out of sight.' gate.
        wizard_pos = None
        if wizard_unit is not None:
            wx = getattr(wizard_unit, 'x', None)
            wy = getattr(wizard_unit, 'y', None)
            if wx is not None and wy is not None:
                wizard_pos = (wx, wy)
        section = _safe_fire(
            'orphan',
            lambda: _orphan.producer.fire(
                journal.records, cfg.show_coordinates,
                getattr(cfg, 'movement_verbose', False),
                log_fn, telemetry=telemetry,
                wizard_pos=wizard_pos,
                los_grouping=getattr(cfg, 'orphan_los_grouping', 'section'),
                spawn_coord_cap=getattr(cfg, 'spawn_coord_cap', 5),
            ),
            log_fn,
        )
        if section is not None:
            sections.append(section)

    # Mark-invariant watchdog. Any record with marks from two producers
    # indicates a coordination bug. Telemetry fires once per offending
    # record — should always be zero in healthy operation.
    if telemetry is not None:
        try:
            _check_double_claims(journal.records, telemetry)
        except Exception:
            pass

    # Sort by priority and join. Skip empty sections.
    if not sections:
        return
    sections.sort(key=lambda s: s[0])
    parts = [text for _, text in sections if text]
    if not parts:
        return

    text = " ".join(parts).strip()
    if not text:
        return

    log_fn(f"[Pipeline] emitting: {text}")
    try:
        tts.speak(text)
    except Exception as e:
        log_fn(f"[Pipeline] speak failed: {e!r}")


# All known producer marks. Used by the double-claim watchdog. Update
# this set if a new producer ships.
_PRODUCER_MARKS = ('crisis_v1', 'digest_v1', 'equipment_v1', 'orphan_v1')


def _check_double_claims(records, telemetry):
    """Emit composer_double_claimed telemetry for any record that carries
    marks from two or more producers. This is a coordination bug — the
    mark-and-respect protocol assumes each record is claimed by at most
    one producer. Cheap to scan: linear in record count, runs once per
    boundary fire."""
    for rec in records:
        marks = rec.get('marks') or []
        producer_marks = [m for m in marks if m in _PRODUCER_MARKS]
        if len(producer_marks) >= 2:
            telemetry.emit(
                'composer_double_claimed',
                sequence=rec.get('sequence'),
                event_type=rec.get('event_type'),
                marks=producer_marks,
            )
