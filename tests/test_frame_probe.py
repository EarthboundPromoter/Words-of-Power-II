# Tests for the frame heartbeat probe (2026-07-07).
#
# Diagnostic for the sustained-runtime stall family: intermittent audio
# cutouts while idle, the unexplained animation freeze, player-reported
# menu double-scrolls (the game's hand-rolled 200ms key repeat converts a
# held-key stall into a synthetic second press). Behaviors pinned:
# - Frame gaps under the spike threshold accumulate silently; a gap at or
#   over it logs immediately with state, latch, and GC context.
# - Each 60s window logs one summary line (frames, avg, max, spikes) and
#   resets; process working set rides the summary when readable.
# - GC passes are timed via gc.callbacks; only pauses >= 10ms log.
# - frame_probe_enabled=false registers no GC callback (and the call site
#   is config-gated).

import sys
import textwrap
import types
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
GAME = MOD.parents[1]
for p in (str(GAME), str(MOD)):
    if p not in sys.path:
        sys.path.insert(0, p)

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator):
    start = _src.index(marker)
    start = _src.rfind("\n", 0, start) + 1
    end = _src.index(terminator, start)
    end = _src.rfind("\n", 0, end) + 1
    return textwrap.dedent(_src[start:end])


import ctypes  # real; the rss helper is exercised for real on Windows
import gc as _real_gc


def _make_ns(enabled=False, census=False, start_clock=100.0):
    """Exec the probe span with a controllable clock. Default DISABLED so
    the span never appends to the real gc.callbacks."""
    logs = []
    clock = [start_clock]
    ns = {
        'cfg': types.SimpleNamespace(frame_probe_enabled=enabled,
                                     frame_probe_census=census),
        'log': logs.append,
        'time': types.SimpleNamespace(perf_counter=lambda: clock[0]),
        'ctypes': ctypes,
        '_STATE_NAMES': {0: 'Level'},
        '_latch': [None],
    }
    code = _extract("# ---- Frame heartbeat probe ----",
                    "# ---- end frame heartbeat probe ----")
    exec(code, ns)
    ns['_logs'] = logs
    ns['_clock'] = clock
    return ns


class _View:
    state = 0


def _tick(ns, dt_s):
    ns['_clock'][0] += dt_s
    ns['_probe_frame'](_View())


# ---- Frame gap measurement ----

def test_first_frame_only_initializes():
    ns = _make_ns()
    ns['_probe_frame'](_View())
    assert ns['_logs'] == []
    assert ns['_probe']['frames'] == 0


def test_quiet_frames_accumulate_without_logging():
    ns = _make_ns()
    ns['_probe_frame'](_View())
    for _ in range(10):
        _tick(ns, 0.033)
    assert ns['_logs'] == []
    assert ns['_probe']['frames'] == 10
    assert ns['_probe']['spikes'] == 0


def test_spike_logs_immediately_with_context():
    ns = _make_ns()
    ns['_probe_frame'](_View())
    _tick(ns, 0.060)
    assert len(ns['_logs']) == 1
    line = ns['_logs'][0]
    assert "[Probe] Frame spike 60ms" in line
    assert "state Level" in line
    assert "latch none" in line
    assert "gc counts" in line


def test_spike_names_the_active_latch():
    ns = _make_ns()
    ns['_latch'][0] = {'overlay': 'los'}
    ns['_probe_frame'](_View())
    _tick(ns, 0.075)
    assert "latch los" in ns['_logs'][0]


def test_window_summary_rolls_over_and_resets():
    ns = _make_ns()
    ns['_probe_frame'](_View())
    for _ in range(9):
        _tick(ns, 0.033)
    _tick(ns, 60.0)  # crosses the window; also a spike
    summary = [l for l in ns['_logs'] if 'frames, avg' in l]
    assert len(summary) == 1
    assert "10 frames" in summary[0]
    assert "spikes 1" in summary[0]
    assert ns['_probe']['frames'] == 0
    assert ns['_probe']['max'] == 0.0
    assert ns['_probe']['spikes'] == 0


# ---- GC pause timing ----

def test_gc_pause_over_threshold_logs():
    ns = _make_ns()
    ns['_probe_gc_callback']('start', {})
    ns['_clock'][0] += 0.015
    ns['_probe_gc_callback']('stop', {'generation': 2, 'collected': 123})
    assert len(ns['_logs']) == 1
    assert "[Probe] GC gen2 pause 15ms, collected 123" in ns['_logs'][0]


def test_gc_pause_under_threshold_is_silent():
    ns = _make_ns()
    ns['_probe_gc_callback']('start', {})
    ns['_clock'][0] += 0.005
    ns['_probe_gc_callback']('stop', {'generation': 0, 'collected': 7})
    assert ns['_logs'] == []


def test_gc_stop_without_start_is_harmless():
    ns = _make_ns()
    ns['_probe_gc_callback']('stop', {'generation': 0, 'collected': 0})
    assert ns['_logs'] == []


def test_disabled_probe_registers_no_gc_callback():
    before = list(_real_gc.callbacks)
    ns = _make_ns(enabled=False)
    assert _real_gc.callbacks == before
    # And enabled registration appends exactly the probe's callback.
    ns2 = _make_ns(enabled=True)
    try:
        assert ns2['_probe_gc_callback'] in _real_gc.callbacks
    finally:
        if ns2['_probe_gc_callback'] in _real_gc.callbacks:
            _real_gc.callbacks.remove(ns2['_probe_gc_callback'])


# ---- Working set readout ----

def test_meminfo_returns_megabytes_and_faults_on_windows():
    ns = _make_ns()
    mem = ns['_probe_meminfo']()
    if sys.platform == 'win32':
        assert mem is not None
        rss, faults = mem
        assert isinstance(rss, float) and 10 < rss < 65536
        assert isinstance(faults, int) and faults > 0
    else:
        assert mem is None


def test_summary_reports_fault_delta_from_second_window():
    ns = _make_ns()
    ns['_probe_frame'](_View())
    _tick(ns, 61.0)   # first summary: rss but no faults delta yet
    _tick(ns, 61.0)   # second summary: faults delta appears
    summaries = [l for l in ns['_logs'] if 'frames, avg' in l]
    assert len(summaries) == 2
    if sys.platform == 'win32':
        assert 'faults +' not in summaries[0]
        assert 'faults +' in summaries[1]


# ---- Object census ----

class _FakeGC:
    """Stand-in for the span's real `import gc as _probe_gc` (swapped into
    the exec namespace after the fact; functions resolve it at call time).
    Population is stride-aligned so sampled counts are exact."""
    def __init__(self):
        self.population = []
        self.callbacks = []

    def get_objects(self):
        return list(self.population)

    def get_count(self):
        return (0, 0, 0)


def _census_ns(stride_items):
    ns = _make_ns(census=True)
    fake = _FakeGC()
    # stride_items maps a sample object -> how many strides of it to plant.
    for obj, strides in stride_items:
        fake.population.extend([obj] * (strides * ns['_PROBE_CENSUS_STRIDE']))
    ns['_probe_gc'] = fake
    ns['_fake_gc'] = fake
    return ns


def test_census_first_fires_after_baseline_delay_then_every_five_minutes():
    ns = _census_ns([({}, 2)])
    ns['_probe_frame'](_View())          # init at t=100
    _tick(ns, 30.0)                      # t=130: not yet
    assert not any('Census' in l for l in ns['_logs'])
    _tick(ns, 31.0)                      # t=161: baseline due
    assert sum('Census baseline' in l for l in ns['_logs']) == 1
    _tick(ns, 200.0)                     # t=361: within 300s of census
    assert sum('Census' in l for l in ns['_logs']) == 1
    _tick(ns, 101.0)                     # t=462: 301s since census
    assert sum('Census' in l for l in ns['_logs']) == 2


def test_census_reports_growers_scaled_by_stride():
    ns = _census_ns([({}, 3), ([], 1)])
    ns['_probe_frame'](_View())
    _tick(ns, 61.0)                      # baseline
    stride = ns['_PROBE_CENSUS_STRIDE']
    # A list-heavy growth spurt: 5 more strides of lists.
    ns['_fake_gc'].population.extend([[]] * (5 * stride))
    _tick(ns, 301.0)
    delta_lines = [l for l in ns['_logs'] if 'Growers' in l]
    assert len(delta_lines) == 1
    assert f"builtins.list +~{5 * stride}" in delta_lines[0]
    assert 'self-inflicted stall' in delta_lines[0]


def test_census_with_no_growth_says_none():
    ns = _census_ns([({}, 2)])
    ns['_probe_frame'](_View())
    _tick(ns, 61.0)
    _tick(ns, 301.0)
    delta_lines = [l for l in ns['_logs'] if 'Growers' in l]
    assert len(delta_lines) == 1 and 'Growers: none' in delta_lines[0]


def test_census_disabled_never_walks():
    ns = _make_ns(census=False)
    fake = _FakeGC()
    ns['_probe_gc'] = fake
    ns['_probe_frame'](_View())
    _tick(ns, 61.0)
    _tick(ns, 301.0)
    assert not any('Census' in l for l in ns['_logs'])


# ---- Wiring pins ----

def test_call_site_is_config_gated():
    at = _src.index("_probe_frame(self)")
    gate = _src.rfind("if cfg.frame_probe_enabled", 0, at)
    assert gate != -1 and at - gate < 120, (
        "the draw_screen call site must be gated on frame_probe_enabled")
