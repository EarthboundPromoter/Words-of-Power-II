"""
Combat-log capture — the bin-1 ORACLE (validation-only, never a speech source).

Wraps the game's single combat-log sink, `Level.Level.log` (Level.py:2947-2948),
to record every log write as a `game_log` journal record BEFORE the entry's
`(template, fmt_dict)` semi-structure is destroyed by resolve-at-sink. One hook
captures 100% of the game's bin-1 render channel (the S23 census: 45 call sites,
39 unique templates, zero dynamic).

The records exist for RECONCILIATION: the row table below maps each log
line-type to what the mod is expected to have captured, and the end-of-turn
parity checker diffs the two — coverage validation as a runtime assertion
instead of a periodic hand-audit. Records never feed voice (S25 ledger ruling),
and NOTHING downstream may ever REQUIRE them: this module is separable — the
RW2 backport finds no RW3-shaped sink and stays inert (RW2's combat log is a
logging.Logger written via .debug(); there is nothing here to wrap).

Safety model (stronger than the interceptor's): the wrap calls the ORIGINAL
`Level.log` FIRST — zero mod code executes before the game's own write. A bug
anywhere in the capture path can only lose a record; the combat log and the
M-key viewer are already correct by the time capture runs. Capture failures are
noted to the debug log once per template (never once per write).

Install is self-gating and field-killable: it verifies the RW3 sink shape
before wrapping (declines cleanly otherwise), and screen_reader only calls it
when `log_capture_enabled` (settings.ini) is true — a field misbehavior is
recoverable by flipping the setting, no code push.

Like journal.install_hooks(), install() joins the shared, unrestored,
whole-process monkeypatch category: idempotence flag, no teardown. Tests that
need the wrap route through one canonical fixture; the decline-path test
save/restores `Level.Level.log` itself.

Imports are lazy (the pipeline.py pattern): the pure checker/row tests must not
pay the game import; journal/text load on first use.
"""

from collections import Counter


_installed = False
_capturing = False          # reentrancy guard — no capture path reaches Level.log
                            # today (resolve_text verified log-free); pinned anyway.
_failed_templates = set()   # once-per-template failure-note dedupe
_log_fn = None              # debug logger injected at install


# ----------------------------------------------------------------------
# Record shape (plan O1): template + coerced values + resolved string + turn
# ----------------------------------------------------------------------

def _coerce_value(v):
    """Journal discipline: payloads are plain primitives only — no live
    references (units die between capture and read), pickle-clean. The log
    fmt dicts carry strings/ints/Raw strings/nested markup tuples today
    (S27 gate verification); str() is insurance for any future caller."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return str(v) if isinstance(v, str) else v  # Raw -> plain str
    return str(v)


def _entry_parts(entry):
    """Split a Level.log entry into (template, values).

    Entries are (template, fmt_dict) tuples at most sites, bare strings at a
    few (Mutators.py:122/155/184, Level.py:2138/2154). Anything else is
    coerced to its string form as the template — an unknown shape is a
    finding for the checker, not an error here.
    """
    if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], str):
        template, fmt = entry
        values = {}
        if isinstance(fmt, dict):
            for k, v in fmt.items():
                values[str(k)] = _coerce_value(v)
        return str(template), values
    if isinstance(entry, str):
        return str(entry), {}
    return str(entry), {}


def _capture(level, entry):
    from journal import journal
    template, values = _entry_parts(entry)
    turn = max(1, getattr(level, 'turn_no', 0) or 0)
    # The original has already run: the resolved line is the newest entry in
    # the game's own bucket (recursion-safe — resolve_text never logs; game
    # logic is single-threaded). Read back, never re-resolve.
    resolved = None
    combat_log = getattr(level, 'combat_log', None)
    if combat_log is not None:
        bucket = combat_log.get(turn)
        if bucket:
            resolved = bucket[-1]
    journal.record('game_log', {
        'template': template,
        'values': values,
        'resolved': resolved,
        'turn': turn,
    })


def _note_failure(entry, exc):
    try:
        key = entry[0] if (isinstance(entry, tuple) and entry) else str(entry)
    except Exception:
        key = '<unkeyable>'
    if key in _failed_templates:
        return
    _failed_templates.add(key)
    if _log_fn:
        try:
            _log_fn(f"[LogCapture] capture failed for {key!r}: {exc!r}")
        except Exception:
            pass


# ----------------------------------------------------------------------
# Install — self-gating, idempotent, separable
# ----------------------------------------------------------------------

def install(log_fn=None):
    """Wrap Level.Level.log. Returns True if the wrap is installed (now or
    previously), False if the sink is missing/mis-shaped (RW2 backport, or a
    future RW3 restructure) — in which case the mod runs oracle-less and
    nothing else changes.
    """
    global _installed, _log_fn
    if _installed:
        return True
    if log_fn is not None:
        _log_fn = log_fn

    try:
        import Level
    except ImportError:
        _note_install_decline("game Level module not importable")
        return False

    sink = getattr(getattr(Level, 'Level', None), 'log', None)
    resolve = getattr(Level, 'resolve_text', None)
    if not callable(sink) or not callable(resolve):
        # The RW3 sink contract we rely on (single Level.log method resolving
        # via resolve_text into combat_log) is absent — decline cleanly.
        _note_install_decline("RW3 log sink not found (Level.log/resolve_text)")
        return False

    original_log = Level.Level.log

    def patched_log(self, entry):
        # ORDERING INVARIANT: the game's write lands first, always. No mod
        # code above this line.
        result = original_log(self, entry)
        global _capturing
        if not _capturing:
            _capturing = True
            try:
                _capture(self, entry)
            except Exception as e:
                _note_failure(entry, e)
            finally:
                _capturing = False
        return result

    Level.Level.log = patched_log
    _installed = True
    if _log_fn:
        try:
            _log_fn("[LogCapture] Level.log wrapped; game_log records active")
        except Exception:
            pass
    return True


def _note_install_decline(reason):
    if _log_fn:
        try:
            _log_fn(f"[LogCapture] install declined: {reason} — mod runs oracle-less")
        except Exception:
            pass


# ----------------------------------------------------------------------
# Reconciliation rows (plan O2)
#
# One row per log line-type, keyed by the PRE-localization template string
# (the record's `template` field). Statuses:
#
#   EXPECT         — mechanically checked: for each occurrence of the line in
#                    a sweep window, one record of ANY listed kind must exist
#                    in the same window (multiplicity-aware, >= semantics —
#                    capture ⊇ render, so over-capture never fails).
#                    Cross-channel rows are a feature: a logged fact may be
#                    supplied by the interceptor, not a log-derived event.
#   VIEW_LAYER     — voiced by immediate view-layer speech; no journal record
#                    exists. Exempt from the runtime check, hand-verified at
#                    authoring. Kept in the table so it stays a full census.
#   PENDING        — logged, currently unvoiced (bin β). The voice-or-justify
#                    verdict is composer-phase work; the row records the debt.
#   JUSTIFIED_DROP — deliberately silent; the note carries the reason.
#
# What EXPECT validates in the capture phase is log->RECORD parity; log->VOICE
# parity is the composer-phase extension of this same table. Rows are authored
# per-unit: this is the as-of-Unit-4 set; later units append theirs.
# ----------------------------------------------------------------------

EXPECT = 'expect'
VIEW_LAYER = 'view_layer'
PENDING = 'pending'
JUSTIFIED_DROP = 'justified_drop'

_ROWS = None


def _expect(*kinds, note=None):
    return {'status': EXPECT, 'kinds': kinds, 'note': note}


def _status(status, note=None):
    return {'status': status, 'kinds': (), 'note': note}


def rows():
    """The reconciliation table, built lazily (text.py loads on first use)."""
    global _ROWS
    if _ROWS is None:
        import text
        _ROWS = {
            # --- The deal_damage cluster + core combat (bin alpha) ---
            text.DMG_DEALS: _expect('EventOnDamaged'),
            text.DMG_TAKES: _expect('EventOnDamaged'),
            text.DMG_BLOCKED: _expect('shield_blocked', 'EventOnShieldRemoved'),
            text.HEAL_BY: _expect('EventOnHealed'),
            text.HEAL_FROM: _expect('EventOnHealed'),
            text.KILLED_BY_UNIT: _expect('EventOnDeath'),
            text.KILLED_BY_ENV: _expect('EventOnDeath'),
            text.USE_SPELL: _expect('EventOnSpellCast', 'cast_begin'),
            text.BUFF_APPLIED: _expect(
                'EventOnBuffApply',
                note="log is a LOWER bound: only bless/curse show_effect "
                     "buffs log; the mod captures all applies"),
            text.PERMANENT_BUFF_APPLIED: _expect('EventOnBuffApply'),

            # --- HP-spends (four logging sites; write -> log -> raise) ---
            text.PAY_HP: _expect('EventOnSpendHP', 'hp_loss'),
            "[{unit}:{color}] paid {amount} HP to activate their {item}":
                _expect('EventOnSpendHP', 'hp_loss',
                        note="Equipment.py:6136 + 7336, one template"),
            "[{unit}:{color}] paid {amount} HP to feed their [Depravity:blood]":
                _expect('EventOnSpendHP', 'hp_loss', note="Spells.py:10056"),

            # --- Cross-channel (bin gamma): interceptor supplies the record ---
            "[{unit}:{color}] used a Crisis Charm":
                _expect('silent_heal',
                        note="Equipment.py:3199; voiced from the cur_hp "
                             "interceptor (wizard_lethal_save), not the log"),
            "[{unit}:ally] joined the enemy team due to mutator.":
                _expect('team_turned', 'team_joined',
                        note="Mutators.py:140; team interceptor supplies"),

            # --- Spell-upgrade grants: the resulting buff record exists ---
            "Shrine of Perfection granted all upgrades to {spell}":
                _expect('EventOnBuffApply',
                        note="granting SOURCE unnamed in voice — composer "
                             "enrichment (eval #F3)"),
            "Evolution Shrine upgraded {spell} with {upgrade}.":
                _expect('EventOnBuffApply'),
            "Codex of Chaotic Evolution upgraded {spell} with {upgrade}":
                _expect('EventOnBuffApply'),
            "Codex Necronomicus upgraded {spell} with {upgrade}":
                _expect('EventOnBuffApply'),
            "[Chimera Eyes:chaos] upgraded {spell} with {upgrade}":
                _expect('EventOnBuffApply', note="CG5; craft-time grant"),

            # --- Watched-attr recoveries ---
            "Wizard gained {count} SP from the {name}":
                _expect('xp_change', note="Components.py:848 write, :849 log"),
            "[Wizard:wizard] ran out of time.":
                _expect('hp_loss',
                        note="chronomancer, RiftWizard3.py:10474-75; the "
                             "threshold readout is Unit 5 / composer work"),

            # --- View-layer voiced (no journal record; hand-verified) ---
            "[Wizard:wizard] takes a step": _status(VIEW_LAYER,
                note="movement speech, view layer"),
            "[Wizard:wizard] channeled {spell}": _status(VIEW_LAYER,
                note="channel speech + synthesized channel cast_begin"),
            "Wizard picked up {component}.":
                _expect('item_pickup',
                        note="Level.py:2822 + LevelRewards.py:277; Unit 2's "
                             "marker owns the moment (both sites wrapped); "
                             "on_item_pickup speech stays view-layer"),
            "Level {lvl}, Turn {turn} begins": _status(VIEW_LAYER,
                note="every combat turn; the mod's own 'Turn N' announcement "
                     "is the counterpart (owner may flip to JUSTIFIED_DROP)"),

            # --- PENDING (bin beta): composer-phase voice-or-justify agenda ---
            text.HP_INCREASE: _expect('max_hp_change',
                note="component max-HP grant; write path verified Unit 2 "
                     "(interceptor capture pinned in the pickup tests)"),
            text.SPELL_STAT_INCREASE:
                _expect('stat_bonus_change', 'charges_change',
                        note="three sites (Components.py:253/338/547), all "
                             "adjust_spell_bonus + refund_charges — watched "
                             "Unit-1 domains, marker-attributed since Unit 2 "
                             "(supersedes the CG1 no-record note)"),
            "{equipment} triggered {component}":
                _expect('equipment_trigger', 'component_effect',
                        note="CG2, Equipment.py:6570/6591; Unit 2's trigger "
                             "markers own the replay, the component window "
                             "carries the identity"),
            "[Wizard:wizard] lost a charge from {spell} due to mutator.":
                _status(PENDING),
            "[Wizard:wizard] stunned for 1 turn due to mutator.":
                _status(PENDING),
            "Object removed from level due to mutator.": _status(PENDING),
            # Crumble (Mutators.py:183-184): the game's ONE terrain log line;
            # make_chasm runs the line before the log write, so the record
            # precedes the line. Flipped PENDING -> EXPECT by Unit 5 step 8.
            "Chasm created due to mutator.": _expect('terrain_change',
                note="navigation-relevant; Unit 5 terrain capture"),
            "[Wizard:wizard] is {buff}": _status(PENDING,
                note="Level.py:2122, per-turn while stun-class-disabled — "
                     "distinct from the apply-time buff line"),
            "[Wizard:wizard] stands still": _status(PENDING),
            "{unit} takes an extra turn": _status(PENDING,
                note="Level.py:3339"),

            # --- Justified drops (deliberate silence, named) ---
            text.DMG_CAP: _status(JUSTIFIED_DROP,
                note="engine safety valve, not world state"),
            text.SPELL_CAST_CAP_REACHED: _status(JUSTIFIED_DROP,
                note="engine recursion guard"),
            "Void Drake's {attr} increased by 10%": _status(JUSTIFIED_DROP,
                note="flavor self-buff line; the stat change is CG1-class"),
        }
    return _ROWS


# ----------------------------------------------------------------------
# Parity checker (plan O3) — end-of-turn sweep, dev-gated, quiet baseline
# ----------------------------------------------------------------------

class ParityChecker:
    """Diffs each sweep window's game_log records against the row table.

    The window (records past the sequence cursor) IS the matching scope —
    the record's `turn` field is informational payload, never a matching
    key, which sidesteps any mod-boundary/game-turn_no mismatch. An unmet
    expectation carries over exactly ONE sweep before failing (the game
    logs before it raises, and in principle a counterpart record could land
    in the next window; a real gap fails one turn late — fine for
    telemetry). Unknown templates alarm once per template per realm.
    """

    def __init__(self):
        self._cursor = 0
        self._carry = []          # [(template, kinds, deficit, turn)]
        self._unknown_seen = set()

    def reset(self):
        """Level transition / post-load boundary: drop carried expectations
        and the per-realm unknown dedupe. The cursor stays — journal.sequence
        is monotonic across journal.reset (records vanish but numbering
        continues above the cursor), so there is nothing below it to re-read
        and nothing above it gets skipped."""
        self._carry = []
        self._unknown_seen.clear()

    def sweep(self, records, telemetry_mod):
        if telemetry_mod is None:
            return
        enabled = getattr(telemetry_mod, 'is_enabled', None)
        if not callable(enabled) or not enabled():
            return

        window = [r for r in records if r.get('sequence', 0) > self._cursor]
        if window:
            self._cursor = max(r.get('sequence', 0) for r in window)

        avail = Counter(r.get('event_type') for r in window)
        table = rows()

        # Carried deficits get first claim on this window's kinds; unmet
        # after this window -> fail now (the one-window grace is spent).
        for (template, kinds, deficit, turn) in self._carry:
            deficit = self._consume(avail, kinds, deficit)
            if deficit > 0:
                telemetry_mod.emit('oracle_parity_fail', template=template,
                                   turn=turn, missing=deficit)
        self._carry = []

        # This window's log lines.
        line_counts = {}
        line_turns = {}
        for r in window:
            if r.get('event_type') != 'game_log':
                continue
            payload = r.get('payload') or {}
            tpl = payload.get('template')
            line_counts[tpl] = line_counts.get(tpl, 0) + 1
            line_turns[tpl] = payload.get('turn')

        for tpl, count in line_counts.items():
            row = table.get(tpl)
            if row is None:
                if tpl not in self._unknown_seen:
                    self._unknown_seen.add(tpl)
                    telemetry_mod.emit('oracle_unknown_template',
                                       template=tpl, turn=line_turns.get(tpl))
                continue
            if row['status'] == EXPECT:
                deficit = self._consume(avail, row['kinds'], count)
                if deficit > 0:
                    self._carry.append(
                        (tpl, row['kinds'], deficit, line_turns.get(tpl)))
            # VIEW_LAYER / PENDING / JUSTIFIED_DROP: exempt by design.

    @staticmethod
    def _consume(avail, kinds, needed):
        """Take up to `needed` records across the any-of kind group,
        consuming from `avail` so one record never satisfies two lines."""
        for k in kinds:
            if needed <= 0:
                break
            take = min(avail.get(k, 0), needed)
            avail[k] -= take
            needed -= take
        return needed


checker = ParityChecker()
