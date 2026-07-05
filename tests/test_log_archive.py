# Pins for the log-archive cap (log_archive.py). Contract: oldest .log
# files go first, the newest always survives (crash evidence), non-.log
# files are never touched, and a missing folder is a quiet no-op.

import os
import sys

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

import log_archive


def _make(tmp_path, name, size, mtime):
    p = tmp_path / name
    p.write_bytes(b"x" * size)
    os.utime(str(p), (mtime, mtime))
    return p


def test_under_cap_removes_nothing(tmp_path):
    _make(tmp_path, "a.log", 100, 1000)
    _make(tmp_path, "b.log", 100, 2000)
    assert log_archive.prune(str(tmp_path), cap_bytes=500) == (0, 0)
    assert (tmp_path / "a.log").exists() and (tmp_path / "b.log").exists()


def test_prunes_oldest_first_until_under_cap(tmp_path):
    _make(tmp_path, "old.log", 300, 1000)
    _make(tmp_path, "mid.log", 300, 2000)
    _make(tmp_path, "new.log", 300, 3000)
    removed, removed_bytes = log_archive.prune(str(tmp_path), cap_bytes=650)
    assert (removed, removed_bytes) == (1, 300)
    assert not (tmp_path / "old.log").exists()
    assert (tmp_path / "mid.log").exists() and (tmp_path / "new.log").exists()


def test_newest_survives_even_alone_over_cap(tmp_path):
    _make(tmp_path, "old.log", 400, 1000)
    _make(tmp_path, "huge_new.log", 900, 2000)
    log_archive.prune(str(tmp_path), cap_bytes=500)
    assert not (tmp_path / "old.log").exists()
    assert (tmp_path / "huge_new.log").exists()  # never deleted


def test_single_file_never_deleted(tmp_path):
    _make(tmp_path, "only.log", 900, 1000)
    assert log_archive.prune(str(tmp_path), cap_bytes=500) == (0, 0)
    assert (tmp_path / "only.log").exists()


def test_non_log_files_untouched_and_not_counted(tmp_path):
    _make(tmp_path, "old.log", 300, 1000)
    _make(tmp_path, "new.log", 300, 2000)
    _make(tmp_path, "keep.txt", 5000, 500)  # oldest AND biggest, but not .log
    log_archive.prune(str(tmp_path), cap_bytes=350)
    assert (tmp_path / "keep.txt").exists()
    assert not (tmp_path / "old.log").exists()
    assert (tmp_path / "new.log").exists()


def test_missing_dir_is_quiet_noop(tmp_path):
    assert log_archive.prune(str(tmp_path / "nope"), cap_bytes=100) == (0, 0)


def test_mtime_tie_broken_by_name(tmp_path):
    # Same timestamp (fast consecutive sessions): deterministic order,
    # still respects the cap and the newest-survivor rule.
    _make(tmp_path, "a.log", 300, 1000)
    _make(tmp_path, "b.log", 300, 1000)
    removed, _ = log_archive.prune(str(tmp_path), cap_bytes=300)
    assert removed == 1
    assert (tmp_path / "b.log").exists()  # 'b' sorts last -> newest -> kept
