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
