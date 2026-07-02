"""
Combat-log capture — the bin-1 ORACLE (validation-only, never a speech source).

Wraps the game's single combat-log sink, `Level.Level.log` (Level.py:2947-2948),
to record every log write as a `game_log` journal record BEFORE the entry's
`(template, fmt_dict)` semi-structure is destroyed by resolve-at-sink. One hook
captures 100% of the game's bin-1 render channel (the S23 census: 45 call sites,
zero dynamic templates).

The records exist for RECONCILIATION: a row table (this module, step 4) maps each
log line-type to what the mod is expected to have captured, and an end-of-turn
parity checker diffs the two — coverage validation as a runtime assertion instead
of a periodic hand-audit. Records never feed voice (S25 ledger ruling), and
NOTHING downstream may ever REQUIRE them: this module is separable — the RW2
backport finds no RW3-shaped sink and stays inert (RW2's combat log is a
logging.Logger written via .debug(); there is nothing here to wrap).

Safety model (stronger than the interceptor's): the wrap calls the ORIGINAL
`Level.log` FIRST — zero mod code executes before the game's own write. A bug
anywhere in the capture path can only lose a record; the combat log and the M-key
viewer are already correct by the time capture runs. Capture failures are noted
to the debug log once per template (never once per write).

Install is self-gating and field-killable: it verifies the RW3 sink shape before
wrapping (declines cleanly otherwise), and screen_reader only calls it when
`log_capture_enabled` (settings.ini) is true — a field misbehavior is recoverable
by flipping the setting, no code push.

Like journal.install_hooks(), install() joins the shared, unrestored,
whole-process monkeypatch category: idempotence flag, no teardown. Tests that
need the wrap route through one canonical fixture; the decline-path test
save/restores `Level.Level.log` itself.
"""

from journal import journal


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
