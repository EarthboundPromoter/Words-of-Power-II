# Regression pins for the settings-file machinery (settings_schema.py) —
# extracted from screen_reader.py after the S29 load-brick, which the suite
# could never have caught while the logic lived in the (untestable) mod
# entry module.
#
# The brick, reconstructed in test_legacy_eof_backfill_shape_recovers:
# the old back-fill appended missing [words_of_power] keys at END OF FILE
# (inside [Composer], the last section). The key then still read as missing
# -> re-appended every launch -> two copies in one section ->
# DuplicateOptionError at the next launch's read -> the mod died before its
# first post-banner log line.

import os
import sys

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

import settings_schema


SCHEMA = [
    ('words_of_power', 'alpha', 'true', "# alpha comment"),
    ('words_of_power', 'beta', 'false', "# beta comment\n# second line"),
    ('Composer', 'gamma', '5', "# gamma comment"),
]


def _write(tmp_path, content):
    p = tmp_path / "settings.ini"
    p.write_text(content, encoding='utf-8')
    return str(p)


def _read(path):
    parser = settings_schema.make_parser()
    parser.read(path, encoding='utf-8')
    return parser


def test_render_groups_by_section():
    text = settings_schema.render_default_settings(SCHEMA)
    assert text.index('[words_of_power]') < text.index('alpha = true')
    assert text.index('alpha = true') < text.index('[Composer]')
    assert text.index('[Composer]') < text.index('gamma = 5')


def test_backfill_inserts_into_declared_section(tmp_path):
    # The core fix: a missing [words_of_power] key lands INSIDE
    # [words_of_power], not at end of file under the last section.
    path = _write(tmp_path,
                  "[words_of_power]\nalpha = false\n\n[Composer]\ngamma = 9\n")
    added = settings_schema.backfill_missing(path, _read(path), SCHEMA)
    assert ('words_of_power', 'beta') in added
    reparsed = _read(path)
    assert reparsed.get('words_of_power', 'beta') == 'false'
    assert not reparsed.has_option('Composer', 'beta')
    # user values untouched
    assert reparsed.get('words_of_power', 'alpha') == 'false'
    assert reparsed.get('Composer', 'gamma') == '9'
    # and the file layout is truly sectioned, not just parser-visible
    lines = open(path, encoding='utf-8').read().splitlines()
    assert lines.index('beta = false') < lines.index('[Composer]')


def test_backfill_creates_missing_section_at_eof(tmp_path):
    path = _write(tmp_path, "[words_of_power]\nalpha = true\nbeta = true\n")
    settings_schema.backfill_missing(path, _read(path), SCHEMA)
    reparsed = _read(path)
    assert reparsed.get('Composer', 'gamma') == '5'


def test_backfill_noop_when_complete(tmp_path):
    path = _write(tmp_path, settings_schema.render_default_settings(SCHEMA))
    before = open(path, encoding='utf-8').read()
    assert settings_schema.backfill_missing(path, _read(path), SCHEMA) == []
    assert open(path, encoding='utf-8').read() == before


def test_backfill_is_idempotent_across_launches(tmp_path):
    # The re-append loop that armed the brick: after one correct backfill,
    # subsequent launches must add NOTHING.
    path = _write(tmp_path, "[words_of_power]\nalpha = true\n")
    settings_schema.backfill_missing(path, _read(path), SCHEMA)
    assert settings_schema.backfill_missing(path, _read(path), SCHEMA) == []


def test_duplicate_keys_never_brick_the_parse(tmp_path):
    # strict=False: the damaged S29 file shape (same key twice in one
    # section) reads last-value-wins instead of raising.
    path = _write(tmp_path,
                  "[words_of_power]\nalpha = true\n"
                  "[Composer]\ngamma = 1\ngamma = 2\n")
    parser = _read(path)
    assert parser.get('Composer', 'gamma') == '2'


def test_legacy_eof_backfill_shape_recovers(tmp_path):
    # A file damaged by the OLD backfill (a words_of_power key stranded
    # under [Composer]): the new backfill inserts the key into its right
    # section; the stranded copy is inert (different section, no
    # duplicate error); reads resolve from the correct section.
    path = _write(tmp_path,
                  "[words_of_power]\nalpha = true\n\n"
                  "[Composer]\ngamma = 5\n\n# stranded by the old backfill\n"
                  "beta = true\n")
    settings_schema.backfill_missing(path, _read(path), SCHEMA)
    reparsed = _read(path)
    assert reparsed.get('words_of_power', 'beta') == 'false'   # schema default
    assert reparsed.get('Composer', 'beta') == 'true'          # inert leftover


# ---------------------------------------------------------------------------
# force_values — the targeted in-place writer (0.3.2 diagnostics adjustment,
# keybinds-migrated flag). Its contract: change ONLY the named keys' value
# text; every comment and every other line survives byte-for-byte.
# ---------------------------------------------------------------------------

def test_force_rewrites_value_preserving_everything_else(tmp_path):
    path = _write(tmp_path,
                  "# file header\n[words_of_power]\n# alpha comment\n"
                  "alpha = true\nbeta = false\n\n[Composer]\ngamma = 5\n")
    parser = _read(path)
    changed = settings_schema.force_values(
        path, parser, [('words_of_power', 'alpha', 'false')])
    assert changed == [('words_of_power', 'alpha')]
    lines = open(path, encoding='utf-8').read().splitlines()
    assert "# file header" in lines and "# alpha comment" in lines
    assert "alpha = false" in lines
    assert "beta = false" in lines and "gamma = 5" in lines
    # the LIVE parser reflects the new value without a re-read
    assert parser.get('words_of_power', 'alpha') == 'false'


def test_force_only_touches_the_named_section(tmp_path):
    # Same key name in two sections: only the addressed one changes.
    path = _write(tmp_path,
                  "[words_of_power]\ngamma = 1\n[Composer]\ngamma = 1\n")
    settings_schema.force_values(
        path, _read(path), [('Composer', 'gamma', '2')])
    reparsed = _read(path)
    assert reparsed.get('words_of_power', 'gamma') == '1'
    assert reparsed.get('Composer', 'gamma') == '2'


def test_force_rewrites_all_duplicate_copies(tmp_path):
    # strict=False reads last-value-wins, so a half-rewritten duplicate
    # pair would silently revert the change: every copy must agree.
    path = _write(tmp_path,
                  "[words_of_power]\nalpha = true\nalpha = true\n")
    settings_schema.force_values(
        path, _read(path), [('words_of_power', 'alpha', 'false')])
    content = open(path, encoding='utf-8').read()
    assert content.count("alpha = false") == 2
    assert "alpha = true" not in content


def test_force_skips_absent_key_by_default(tmp_path):
    # Insertion (with schema comments) is backfill_missing's job.
    path = _write(tmp_path, "[words_of_power]\nalpha = true\n")
    before = open(path, encoding='utf-8').read()
    changed = settings_schema.force_values(
        path, _read(path), [('words_of_power', 'beta', 'false')])
    assert changed == []
    assert open(path, encoding='utf-8').read() == before


def test_force_insert_missing_lands_in_declared_section(tmp_path):
    path = _write(tmp_path,
                  "[words_of_power]\nalpha = true\n\n[Composer]\ngamma = 5\n")
    parser = _read(path)
    changed = settings_schema.force_values(
        path, parser, [('words_of_power', 'beta', 'true')],
        insert_missing=True)
    assert changed == [('words_of_power', 'beta')]
    lines = open(path, encoding='utf-8').read().splitlines()
    assert lines.index('beta = true') < lines.index('[Composer]')
    assert parser.get('words_of_power', 'beta') == 'true'


def test_force_noop_when_value_already_set(tmp_path):
    path = _write(tmp_path, "[words_of_power]\nalpha = true\n")
    before = open(path, encoding='utf-8').read()
    changed = settings_schema.force_values(
        path, _read(path), [('words_of_power', 'alpha', 'true')])
    assert changed == []
    assert open(path, encoding='utf-8').read() == before


def test_force_matches_colon_delimited_keys(tmp_path):
    # configparser accepts "key: value"; a hand-edited file must not dodge
    # the rewrite on delimiter style.
    path = _write(tmp_path, "[words_of_power]\nalpha: true\n")
    settings_schema.force_values(
        path, _read(path), [('words_of_power', 'alpha', 'false')])
    assert _read(path).get('words_of_power', 'alpha') == 'false'


def test_upgrade_scenario_031_ini(tmp_path):
    # Facsimile of the real 0.3.2 upgrade pass over a 0.3.1 shipped file:
    # force the diagnostic flags off, then backfill stamps the marker.
    # Customizations (coordinates) and comments survive.
    schema = [
        ('words_of_power', 'show_coordinates', 'true', "# coords comment"),
        ('words_of_power', 'journal_log_enabled', 'false', "# journal comment"),
        ('words_of_power', 'diagnostics_migrated', 'true', "# marker comment"),
    ]
    path = _write(tmp_path,
                  "# user file from 0.3.1\n[words_of_power]\n"
                  "show_coordinates = false\njournal_log_enabled = true\n")
    parser = _read(path)
    assert not parser.has_option('words_of_power', 'diagnostics_migrated')
    settings_schema.force_values(
        path, parser, [('words_of_power', 'journal_log_enabled', 'false')])
    settings_schema.backfill_missing(path, parser, schema)
    reparsed = _read(path)
    assert reparsed.get('words_of_power', 'journal_log_enabled') == 'false'
    assert reparsed.get('words_of_power', 'show_coordinates') == 'false'  # kept
    assert reparsed.get('words_of_power', 'diagnostics_migrated') == 'true'
    assert "# user file from 0.3.1" in open(path, encoding='utf-8').read()
