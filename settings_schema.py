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


def force_values(path, parser, items, insert_missing=False, log_fn=None):
    """Set (section, key, value) triples in the settings file IN PLACE,
    preserving comments and layout — the counterpart to backfill_missing
    for keys that must be CHANGED, not added (the 0.3.2 diagnostics
    adjustment, the keybinds-migrated flag). Never rewrites the whole file
    through ConfigParser: that strips every comment.

    A key present in the file has every occurrence rewritten (the tolerant
    parser reads last-value-wins, so all copies must agree). A key absent
    from the file is skipped unless insert_missing=True, in which case it
    is inserted at the end of its section's block (section appended at end
    of file when truly absent) — schema-comment insertion stays
    backfill_missing's job. The live parser is updated to match. Returns
    the list of (section, key) pairs whose stored value actually changed.
    """
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    changed = []
    file_dirty = False
    for section, key, value in items:
        header_idx = None
        section_end = len(lines)
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if header_idx is None:
                if stripped == f"[{section}]":
                    header_idx = i
            elif stripped.startswith('[') and stripped.endswith(']'):
                section_end = i
                break

        found = False
        value_differs = False
        if header_idx is not None:
            for j in range(header_idx + 1, section_end):
                s = lines[j].strip()
                if s.startswith('#') or s.startswith(';'):
                    continue
                for delim in ('=', ':'):
                    name, _, _rest = s.partition(delim)
                    if _ and name.strip().lower() == key.lower():
                        found = True
                        if _rest.strip() != value:
                            lines[j] = f"{key} = {value}"
                            value_differs = True
                            file_dirty = True
                        break

        if not found:
            if not insert_missing:
                continue
            if header_idx is None:
                lines.append("")
                lines.append(f"[{section}]")
                lines.append(f"{key} = {value}")
            else:
                lines[section_end:section_end] = [f"{key} = {value}"]
            value_differs = True
            file_dirty = True

        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, value)
        if value_differs:
            changed.append((section, key))

    if file_dirty:
        with open(path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n")

    if changed and log_fn:
        try:
            keys = ', '.join(f"[{s}].{k}" for s, k in changed)
            log_fn(f"[Settings] Forced values: {keys}")
        except Exception:
            pass
    return changed


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
