"""Journal-replay compose profiler — step zero of the composer refactor.

Replays a captured journal_debug JSONL through the REAL fire_pipeline,
turn by turn, timing each producer. This is the attribution instrument
for the S38 perf finding (compose avg 50ms / max 195ms on proc-heavy
swarm turns, action_log #201): it answers WHICH producer and WHICH
turns, offline, from logs the owner's normal play already produces.

How the replay reconstructs live boundaries:
  * ``__meta__ level_reset`` lines mark exactly where the live journal
    wiped its record list without composing (level entry + the
    first-boundary hygiene reset, screen_reader.py:7215). The replay
    drops the pending batch at a meta if no combat turn was seen
    (mirroring the wipe) and fires it if one was (the level's final
    boundary fired live before the transition).
  * Within a level, ``game_log`` records carry payload['turn'] (the
    game's own counter, log_capture.py:99); an increment closes the
    previous turn's batch and fires the pipeline on everything
    accumulated so far — cursors, marks, and index rebuilds behave
    exactly as live because the REAL producer singletons run in the
    REAL pipeline order and the record list accumulates per level.

Documented divergences from the live session (attribution-grade, not
parity-grade):
  * Retro marks are in-memory only — the JSONL serializes at record
    creation (journal._emit), so spend-supersede / superseded_by_block
    marks stamped after creation are absent on replay. A few lines the
    live session suppressed may compose here; timing impact negligible.
  * wizard_unit is a snapshot stand-in rebuilt from the records
    (x/y/cur_hp/max_hp; buffs=[], shields=0), so crisis's live polls
    (threshold label, agency lines) may differ. Producer exceptions
    are caught by the pipeline's _safe_fire as live; the report counts
    them — a nonzero count means the stand-in needs another field.
  * Post-clear turns without log lines lump into the preceding batch
    (the game logs no turn header once enemies are gone, Level.py:3396).
  * telemetry=None, so the pipeline's double-claim watchdog pass, the
    producers' per-fire telemetry emits, and the unmodeled scans are
    all skipped (live they add full-list passes + write/flush pairs).
  * log_fn here is a list append; the live log() pays a console print,
    a file write+flush, and telemetry.capture PER LINE, in-slice — in
    verbose mode the producers log their full composed text. The live
    [Perf] compose slice therefore carries observability cost this
    replay deliberately excludes; the S39 first run measured that gap
    at roughly 7x (see COMPOSER_SYSTEM_MAP.md, perf section).

Pre-0.4.0-schema journal files cannot be replayed (journal.py header).

Run from anywhere (chdirs to the game root; newest log by default):
  python mods/screen_reader/tools/replay_profile.py
  python mods/screen_reader/tools/replay_profile.py --file logs/journal_debug_X.log
  python mods/screen_reader/tools/replay_profile.py --top 10 --profile
"""

import argparse
import glob
import json
import os
import sys
import time

_GAME_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
_MOD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

PRODUCERS = ('crisis', 'digest', 'equipment', 'orphan')


def _bootstrap_game():
    os.chdir(_GAME_ROOT)
    if _GAME_ROOT not in sys.path:
        sys.path.insert(0, _GAME_ROOT)
    if _MOD_DIR not in sys.path:
        sys.path.insert(0, _MOD_DIR)
    import types
    sys.modules.setdefault('steamworks', types.ModuleType('steamworks'))
    import Level      # noqa: F401
    import Game       # noqa: F401
    import Monsters   # noqa: F401


def _newest_log():
    pattern = os.path.join(_MOD_DIR, 'logs', 'journal_debug_*.log')
    files = glob.glob(pattern)
    if not files:
        raise SystemExit(f"no journal logs match {pattern}; pass --file")
    return max(files, key=os.path.getmtime)


def load_events(path):
    """Yield ('meta', obj) / ('rec', obj) in emission order."""
    events = []
    bad = 0
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if '__meta__' in obj:
                events.append(('meta', obj))
            else:
                events.append(('rec', obj))
    return events, bad


class _SnapWizard(object):
    """Stand-in for the live wizard unit, rebuilt from record snapshots.
    Carries only what fire_pipeline and crisis's polls read; anything
    else is an attribute miss that _safe_fire catches and the report
    counts."""

    def __init__(self):
        self.x = None
        self.y = None
        self.cur_hp = None
        self.max_hp = None
        self.buffs = []
        self.shields = 0

    def update_from_batch(self, batch):
        for rec in batch:
            p = rec.get('payload') or {}
            for key in ('unit', 'target', 'caster', 'source'):
                s = p.get(key)
                if isinstance(s, dict) and s.get('is_player_controlled'):
                    if s.get('x') is not None:
                        self.x = s.get('x')
                        self.y = s.get('y')
                    if s.get('cur_hp') is not None:
                        self.cur_hp = s.get('cur_hp')
                    if s.get('max_hp') is not None:
                        self.max_hp = s.get('max_hp')


def _wrap_timed(obj, attr, sink, key):
    orig = getattr(obj, attr)

    def wrapped(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            sink[key] += time.perf_counter() - t0
    setattr(obj, attr, wrapped)


def segment_fires(events):
    """Group records into (level_id, turn, batch) fire units, mirroring
    the live boundary discipline (see module docstring)."""
    fires = []
    batch = []
    turn = None
    dropped_batches = 0

    def close(reason_meta=False):
        nonlocal batch, turn, dropped_batches
        if batch:
            if turn is not None:
                fires.append((batch[0].get('level_id'), turn, batch))
            else:
                dropped_batches += 1
        batch = []
        if reason_meta:
            turn = None

    for kind, obj in events:
        if kind == 'meta':
            if obj.get('__meta__') == 'level_reset':
                close(reason_meta=True)
                fires.append(('__reset__', None, None))
            continue
        if obj.get('event_type') == 'game_log':
            t = (obj.get('payload') or {}).get('turn')
            if t is not None:
                if turn is None:
                    turn = t
                elif t != turn:
                    close()
                    turn = t
        batch.append(obj)
    close()
    return fires, dropped_batches


def replay(path, show_coords=True, top=8, quiet=False):
    import pipeline
    import crisis
    import digest
    import equipment
    import orphan
    from journal import journal as J

    events, bad_lines = load_events(path)
    fires, dropped = segment_fires(events)

    slice_ms = {k: 0.0 for k in PRODUCERS}
    _wrap_timed(crisis.producer, 'fire', slice_ms, 'crisis')
    _wrap_timed(digest.composer, 'compose_section', slice_ms, 'digest')
    _wrap_timed(equipment.producer, 'fire', slice_ms, 'equipment')
    _wrap_timed(orphan.producer, 'fire', slice_ms, 'orphan')

    class _Cfg(object):
        crisis_enabled = True
        digest_enabled = True
        equipment_enabled = True
        orphan_enabled = True
        movement_verbose = False
    cfg = _Cfg()
    cfg.show_coordinates = show_coords

    spoken = []

    class _Sink(object):
        def speak(self, text):
            spoken.append(text)
    sink = _Sink()

    fail_counts = {k: 0 for k in PRODUCERS}
    log_lines = []

    def log_fn(msg):
        log_lines.append(msg)
        for k in PRODUCERS:
            if f'{k} fire failed' in msg:
                fail_counts[k] += 1

    wizard = _SnapWizard()
    accumulated = []
    rows = []
    n_records = 0
    level_ids = set()

    for level_id, turn, batch in fires:
        if level_id == '__reset__':
            accumulated = []
            continue
        accumulated.extend(batch)
        n_records += len(batch)
        level_ids.add(level_id)
        J.records = accumulated
        wizard.update_from_batch(batch)
        wiz = wizard if wizard.x is not None else None

        for k in PRODUCERS:
            slice_ms[k] = 0.0
        spoken_before = len(spoken)
        t0 = time.perf_counter()
        pipeline.fire_pipeline(sink, log_fn, cfg, wiz, telemetry=None)
        total = (time.perf_counter() - t0) * 1000.0

        text = spoken[-1] if len(spoken) > spoken_before else ''
        rows.append({
            'level': level_id,
            'turn': turn,
            'records': len(batch),
            'total': total,
            'chars': len(text),
            **{k: slice_ms[k] * 1000.0 for k in PRODUCERS},
        })

    _report(path, rows, n_records, level_ids, dropped, bad_lines,
            fail_counts, top, log_lines, quiet)
    return rows


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(round(p / 100.0 * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def _report(path, rows, n_records, level_ids, dropped, bad_lines,
            fail_counts, top, log_lines, quiet):
    print(f"Replay: {os.path.basename(path)}")
    print(f"  {n_records} records / {len(level_ids)} levels / "
          f"{len(rows)} composed fires "
          f"({dropped} setup batches dropped, {bad_lines} bad lines)")
    fails = ", ".join(f"{k} {v}" for k, v in fail_counts.items() if v)
    print(f"  producer failures: {fails if fails else 'none'}")

    if not rows:
        print("  nothing composed - is this a pre-0.4.0-schema file?")
        return

    totals = sorted(r['total'] for r in rows)
    mean = sum(totals) / len(totals)
    print(f"\nPer-fire total ms: mean {mean:.1f}  median {_pct(totals, 50):.1f}  "
          f"p95 {_pct(totals, 95):.1f}  max {totals[-1]:.1f}")

    grand = sum(totals) or 1e-9
    print("\nProducer attribution (share of total compose time):")
    for k in PRODUCERS:
        s = sum(r[k] for r in rows)
        mx = max(rows, key=lambda r: r[k])
        print(f"  {k:<10} {s:8.1f} ms  {100.0 * s / grand:5.1f}%   "
              f"worst fire {mx[k]:6.1f} ms (level {mx['level']} turn {mx['turn']})")

    print(f"\nTop {top} slowest fires:")
    for r in sorted(rows, key=lambda r: -r['total'])[:top]:
        parts = "  ".join(f"{k} {r[k]:.1f}" for k in PRODUCERS)
        print(f"  level {r['level']} turn {r['turn']}: {r['total']:7.1f} ms, "
              f"{r['records']:4d} records, {r['chars']:4d} chars | {parts}")

    if not quiet:
        errs = [l for l in log_lines if 'failed' in l or 'error' in l]
        for l in errs[:10]:
            print(f"  ! {l}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--file', help='journal_debug JSONL (default: newest in logs/)')
    ap.add_argument('--top', type=int, default=8, help='slowest fires to list')
    ap.add_argument('--no-coords', action='store_true',
                    help='compose with show_coordinates off')
    ap.add_argument('--profile', action='store_true',
                    help='cProfile the replay; print top functions')
    ap.add_argument('--quiet', action='store_true', help='suppress error echo')
    args = ap.parse_args()

    _bootstrap_game()
    path = os.path.abspath(args.file) if args.file else _newest_log()

    if args.profile:
        import cProfile
        import pstats
        prof = cProfile.Profile()
        prof.enable()
        replay(path, show_coords=not args.no_coords, top=args.top,
               quiet=args.quiet)
        prof.disable()
        stats = pstats.Stats(prof).strip_dirs()
        print("\n--- cProfile: top 25 by cumulative time ---")
        stats.sort_stats('cumulative').print_stats(25)
        print("\n--- cProfile: top 25 by own time ---")
        stats.sort_stats('tottime').print_stats(25)
    else:
        replay(path, show_coords=not args.no_coords, top=args.top,
               quiet=args.quiet)


if __name__ == '__main__':
    main()
