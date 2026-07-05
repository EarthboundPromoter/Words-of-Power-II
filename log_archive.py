# Log-archive size cap. Every launch archives the previous session's debug
# log into mods/screen_reader/logs/ — before 0.3.2 nothing ever pruned that
# folder, so it grew without bound across sessions (multi-megabyte files per
# session at verbose). Extracted as its own module so the suite can guard it
# without importing the mod entry (same pattern as settings_schema.py).

import os


def prune(archive_dir, cap_bytes, keep_newest=1):
    """Delete the oldest .log files in archive_dir until the folder's total
    .log size is at or under cap_bytes. The newest keep_newest files are
    never deleted, even if they alone exceed the cap — the last session's
    log is crash evidence and must survive. Best effort: a missing folder
    or an unremovable file is skipped, never fatal. Returns
    (removed_count, removed_bytes)."""
    try:
        names = [n for n in os.listdir(archive_dir)
                 if n.lower().endswith('.log')]
    except OSError:
        return (0, 0)

    entries = []
    for name in names:
        path = os.path.join(archive_dir, name)
        try:
            entries.append((os.path.getmtime(path), name,
                            os.path.getsize(path), path))
        except OSError:
            continue
    entries.sort()  # oldest first; name breaks mtime ties deterministically

    total = sum(size for _, _, size, _ in entries)
    removable = entries[:-keep_newest] if keep_newest > 0 else entries
    removed = 0
    removed_bytes = 0
    for _, _, size, path in removable:
        if total <= cap_bytes:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        removed += 1
        removed_bytes += size
    return (removed, removed_bytes)
