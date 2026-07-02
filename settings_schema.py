# Settings-file machinery: default rendering, upgrade back-fill, and the
# tolerant parser. Extracted from screen_reader.py so the suite can guard it —
# the S29 load-brick proved this logic needs tests: the old back-fill appended
# missing keys at END OF FILE, which lands them inside whatever section is
# textually last. A [words_of_power] key written under [Composer] is then
# still "missing" to the parser, gets re-appended on every launch, and the
# second copy in one section is a fatal DuplicateOptionError at the NEXT
# launch's read — the mod bricks before its first log line after the banner.
#
# Two structural fixes, both pinned by tests/test_settings_schema.py:
# - backfill_missing() inserts each key at the end of its DECLARED section's
#   block (creating the section at EOF only when truly absent).
# - make_parser() returns ConfigParser(strict=False): duplicate keys can
#   never brick the load again (last value wins) — settings damage degrades
#   to a wrong value, never to a dead mod.
#
# The schema itself (the single source of truth for keys, defaults, comment
# blocks) stays in screen_reader.py — content belongs with the mod entry;
# this module is pure mechanism, schema passed in.

import configparser


def make_parser():
    """The settings parser. strict=False: a duplicated key (however a user's
    file got that way) reads last-value-wins instead of raising — a settings
    file must never be able to brick the mod's load."""
    return configparser.ConfigParser(strict=False)


def render_default_settings(schema):
    """Build the default settings.ini content from the schema, grouping keys
    by their declared section in declaration order."""
    parts = [
        "# Words of Power settings",
        "# Edit this file to customize mod behavior. Restart the game after changes.",
        "",
    ]
    section_order = []
    by_section = {}
    for section, key, default, comment in schema:
        if section not in by_section:
            section_order.append(section)
            by_section[section] = []
        by_section[section].append((key, default, comment))

    for section in section_order:
        parts.append(f"[{section}]")
        parts.append("")
        for key, default, comment in by_section[section]:
            parts.append(comment)
            parts.append(f"{key} = {default}")
            parts.append("")
    return "\n".join(parts)


def backfill_missing(path, parser, schema, log_fn=None):
    """Insert schema keys missing from the loaded settings.ini into their
    DECLARED section (never appended blindly at end of file).

    Existing user values are preserved untouched. A missing key is inserted
    at the end of its section's block (just before the next section header);
    a missing section is appended at end of file with its header. Returns
    the list of (section, key) pairs added.
    """
    missing_by_section = {}
    for section, key, default, comment in schema:
        if not parser.has_section(section) or not parser.has_option(section, key):
            missing_by_section.setdefault(section, []).append(
                (key, default, comment))
    if not missing_by_section:
        return []

    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    for section, items in missing_by_section.items():
        block = []
        for key, default, comment in items:
            block.append("")
            block.extend(comment.splitlines())
            block.append(f"{key} = {default}")
        header_idx = None
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if stripped == f"[{section}]":
                header_idx = i
                break
        if header_idx is None:
            lines.append("")
            lines.append(f"[{section}]")
            lines.extend(block)
        else:
            end = len(lines)
            for j in range(header_idx + 1, len(lines)):
                s = lines[j].strip()
                if s.startswith('[') and s.endswith(']'):
                    end = j
                    break
            lines[end:end] = block

    with open(path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")

    added = [(s, k) for s, items in missing_by_section.items()
             for k, _, _ in items]
    if log_fn:
        try:
            keys = ', '.join(f"[{s}].{k}" for s, k in added)
            log_fn(f"[Settings] Back-filled missing keys: {keys}")
        except Exception:
            pass
    return added
