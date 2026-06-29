# Tests for the Words of Power help content (help_content.py) and its round-trip
# through the screen_model builder.
# Run with: cd ~ && python -m pytest "<path_to_mod>/tests/test_help_content.py" -v

import sys
import os

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from help_content import get_mod_help_sections
from screen_model import build_how_to_play_model

# Coarse guard against a page growing past the visible area (RW3 truncates a
# How-to-Play body at the footer). Tip pages carry long prose lines, so this is
# a soft upper bound, not a tight fit — owner confirmed real pages have headroom.
PAGE_LINE_BUDGET = 10


def _sections():
    return get_mod_help_sections()


def _by_title():
    return {t: b[0] for t, b in _sections()}


def test_sections_shape():
    secs = _sections()
    assert len(secs) == 6
    for title, bodies in secs:
        assert isinstance(title, str) and title
        assert isinstance(bodies, list) and len(bodies) == 1
        assert isinstance(bodies[0], str)


def test_titles_branded_and_native_style():
    for title, _ in _sections():
        assert title.startswith("WORDS OF POWER")
        assert title.endswith(":")  # native ALL-CAPS trailing-colon style


def test_every_page_fits_the_truncation_budget():
    for title, bodies in _sections():
        lines = [ln for ln in bodies[0].splitlines() if ln.strip()]
        assert len(lines) <= PAGE_LINE_BUDGET, f"{title} has {len(lines)} lines"


def test_no_em_dashes_in_rendered_text():
    # The pixel font may lack U+2014; keep rendered punctuation ASCII-safe.
    for _, bodies in _sections():
        assert "—" not in bodies[0] and "–" not in bodies[0]


def test_bracket_keys_spelled_out_not_literal_chips():
    # Bracket keys must be spelled out ("Left Bracket"), never authored as a
    # literal "[" chip that could confuse the [tag:style] markup parser.
    speech = _by_title()["WORDS OF POWER: MOVEMENT, SPEECH AND HELP:"]
    assert "[Left Bracket / Right Bracket:shields]" in speech


def test_f1_documented_in_help_page():
    speech = _by_title()["WORDS OF POWER: MOVEMENT, SPEECH AND HELP:"]
    assert "[F1 or Shift+/:shields]" in speech


# --- Round-trip through the screen_model builder (what the live screen does) ---

def _as_pages(sections):
    # Mirror RW3's get_how_to_play_pages for a single-raw-text section: split into
    # lines (trailing blanks dropped, as get_how_to_play_page_lines does).
    pages = []
    for title, bodies in sections:
        lines = bodies[0].splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        pages.append({'title': title, 'lines': lines})
    return pages


def test_scan_creatures_yields_heading_plus_six_nodes():
    model = build_how_to_play_model(_as_pages(_sections()))
    creatures = model[0]
    assert creatures.nodes[0].text == 'Words Of Power: Scan Creatures'
    assert creatures.nodes[0].level == 0
    body = creatures.nodes[1:]
    assert len(body) == 6
    assert body[0].text == (
        'F: Health, shields, SP, and active buffs and debuffs. '
        'Shift+F gives an ally overview'
    )
    assert body[5].text == (
        'Alt + J/N/Q/Y: Mark or unmark the last scanned target, '
        'so Shift+P can report the path to it'
    )


def test_no_appended_page_accidentally_triggers_numpad_fold():
    model = build_how_to_play_model(_as_pages(_sections()))
    for page in model:
        for node in page.nodes:
            assert not node.text.startswith('Numpad.')


def test_tips_page_is_plain_prose_nine_entries():
    model = build_how_to_play_model(_as_pages(_sections()))
    tips = model[5]
    assert tips.nodes[0].text == 'Words Of Power: Tips'
    # Nine prose tips, each its own node.
    assert len(tips.nodes) == 1 + 9
    # First tip is the cursor-scouting centerpiece.
    assert tips.nodes[1].text.startswith('Scans always measure from the cursor')


def test_deploy_explanation_is_first_body_node():
    model = build_how_to_play_model(_as_pages(_sections()))
    deploy = model[4]
    assert deploy.nodes[0].text == 'Words Of Power: Deploy'
    assert deploy.nodes[1].text.startswith('Deploy is the start-of-level placement phase')
