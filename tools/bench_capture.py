"""Headless capture-cost benchmark — the slime ladder.

Measures per-turn wall time of the engine's own turn loop (Level.advance
full_turn) under the mod's capture configurations, across a unit-count
ladder. Slime swarms are the measured worst case from the 0.3.1 field
report: every slime heals every turn (deal_damage + events), splits grow
the unit count mid-run, and the container-diff sweep's cost model is
boundaries(~units) x compares(~units).

Configs build cumulatively on the journal core except where noted:
  baseline   - no mod code at all (the game's own cost; clears/convicts EA)
  core       - journal.install_hooks() only (always on in shipped builds)
  container  - core + container_diff (the suspected quadratic)
  logcap     - core + log_capture oracle
  markers    - core + cause_markers + reactive_markers
  full       - everything + journal_debug JSONL to a temp file
               (the accidental 0.3.1 field configuration)

One process per measured run — the wraps don't uninstall. --matrix drives
the full grid via subprocesses and prints a comparison report.

Run from anywhere; the script chdirs to the game root:
  python mods/screen_reader/tools/bench_capture.py --matrix
  python mods/screen_reader/tools/bench_capture.py --config full --slimes 80 --profile
"""

import argparse
import os
import random
import subprocess
import sys
import tempfile
import time

_GAME_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
_MOD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

CONFIGS = ('baseline', 'core', 'container', 'logcap', 'markers', 'full')
LADDER = (10, 20, 40, 80, 160)
DEFAULT_TURNS = 30
SEED = 7
N_TARGETS = 4          # sturdy player-team dummies the slimes converge on
TARGET_HP = 10000


def _bootstrap_game():
    os.chdir(_GAME_ROOT)
    if _GAME_ROOT not in sys.path:
        sys.path.insert(0, _GAME_ROOT)
    if _MOD_DIR not in sys.path:
        sys.path.insert(0, _MOD_DIR)
    import types
    sys.modules.setdefault('steamworks', types.ModuleType('steamworks'))
    import Level      # noqa: F401
    import Game       # noqa: F401  -- resolves the LevelRewards/Spells/Monsters cycle
    import Monsters   # noqa: F401


def _install(config, journal_path):
    """Install the mod layers for this config. baseline installs nothing."""
    if config == 'baseline':
        return None
    from journal import journal, install_hooks
    install_hooks()
    if config in ('container', 'full'):
        import container_diff
        # log_fn=print: escaped-write backstop alarms (step 5) must be
        # VISIBLE in bench output — "backstop silent" is an acceptance
        # criterion, so silence has to be observable, not assumed.
        container_diff.install(log_fn=print)
        container_diff.reseed()
    if config in ('logcap', 'full'):
        import log_capture
        log_capture.install()
    if config in ('markers', 'full'):
        import cause_markers
        import reactive_markers
        cause_markers.install()
        reactive_markers.install()
    if config == 'full':
        journal.open_log(journal_path)
    return journal


def _build_level(n_slimes):
    import Level
    import Monsters
    lvl = Level.Level(18, 18)
    lvl.random.seed(SEED)
    random.seed(SEED)

    # Player-team dummies near the center: AI-driven (no input wait), huge
    # HP so the fight runs the whole measurement. Slimes path to them and
    # melee; the dummies pass their turns.
    for i in range(N_TARGETS):
        u = Level.Unit()
        u.name = "Training Dummy %d" % i
        u.max_hp = TARGET_HP
        u.team = Level.TEAM_PLAYER
        lvl.add_obj(u, 8 + (i % 2), 8 + (i // 2))

    # Slimes on a deterministic scatter over the remaining tiles.
    open_tiles = [(x, y) for x in range(18) for y in range(18)
                  if lvl.get_unit_at(x, y) is None]
    rng = random.Random(SEED)
    rng.shuffle(open_tiles)
    if n_slimes > len(open_tiles):
        raise SystemExit("more slimes than open tiles (%d > %d)"
                         % (n_slimes, len(open_tiles)))
    for x, y in open_tiles[:n_slimes]:
        lvl.add_obj(Monsters.GreenSlime(), x, y)
    return lvl


def run_once(config, n_slimes, turns, profile=False):
    _bootstrap_game()
    journal_path = os.path.join(tempfile.gettempdir(), 'bench_journal.jsonl')
    journal = _install(config, journal_path)
    lvl = _build_level(n_slimes)
    if journal is not None:
        journal.reset(id(lvl), lvl)

    def loop():
        for _ in range(turns):
            lvl.advance(full_turn=True)

    if profile:
        import cProfile
        import pstats
        pr = cProfile.Profile()
        pr.enable()
        t0 = time.perf_counter()
        loop()
        elapsed = time.perf_counter() - t0
        pr.disable()
        pstats.Stats(pr).sort_stats('cumulative').print_stats(20)
    else:
        t0 = time.perf_counter()
        loop()
        elapsed = time.perf_counter() - t0

    n_records = len(journal.records) if journal is not None else 0
    if journal is not None and config == 'full':
        journal.close_log()
    # One parseable line; the matrix driver reads it.
    print("RESULT config=%s slimes=%d turns=%d ms_per_turn=%.2f "
          "total_s=%.3f records=%d units_end=%d"
          % (config, n_slimes, turns, elapsed * 1000.0 / turns,
             elapsed, n_records, len(lvl.units)))


def run_matrix(turns):
    results = {}
    for config in CONFIGS:
        for n in LADDER:
            cmd = [sys.executable, os.path.abspath(__file__),
                   '--config', config, '--slimes', str(n), '--turns', str(turns)]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True,
                                     timeout=600, cwd=_GAME_ROOT)
            except subprocess.TimeoutExpired:
                print("%s at %d slimes: TIMEOUT (>600s)" % (config, n))
                continue
            line = next((ln for ln in out.stdout.splitlines()
                         if ln.startswith('RESULT')), None)
            if line is None:
                print("%s at %d slimes: FAILED" % (config, n))
                err_tail = (out.stderr or '').strip().splitlines()[-3:]
                for e in err_tail:
                    print("    " + e)
                continue
            fields = dict(kv.split('=') for kv in line.split()[1:])
            results[(config, n)] = fields
            print("%s at %d slimes: %s ms per turn, %s records, "
                  "%s units at end"
                  % (config, n, fields['ms_per_turn'], fields['records'],
                     fields['units_end']))

    print()
    print("Overhead vs baseline (same slime count):")
    for config in CONFIGS[1:]:
        for n in LADDER:
            r, b = results.get((config, n)), results.get(('baseline', n))
            if not r or not b:
                continue
            base = float(b['ms_per_turn'])
            mult = float(r['ms_per_turn']) / base if base > 0 else float('inf')
            print("  %s at %d slimes: %.1fx baseline" % (config, n, mult))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--config', choices=CONFIGS)
    ap.add_argument('--slimes', type=int, default=40)
    ap.add_argument('--turns', type=int, default=DEFAULT_TURNS)
    ap.add_argument('--profile', action='store_true')
    ap.add_argument('--matrix', action='store_true')
    args = ap.parse_args()
    if args.matrix:
        run_matrix(args.turns)
    elif args.config:
        run_once(args.config, args.slimes, args.turns, profile=args.profile)
    else:
        ap.error('pass --config for one run or --matrix for the grid')


if __name__ == '__main__':
    main()
