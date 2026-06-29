# Tests for the navigable screen model + focus controller (screen_model.py).
# Run with: cd ~ && python -m pytest "<path_to_mod>/tests/test_screen_model.py" -v

import sys
import os

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from screen_model import (
    build_how_to_play_model, FocusController, Node,
    _clean_line, _split_multibinding, _build_body_nodes,
)


# --- Fixtures mirroring RW3's real get_how_to_play_pages() source text ---

CONTROLS_LINES = [
    '',
    '[H:shields]: Help (This Screen)      [C:shields]: Character Sheet      [S:shields]: Learn Spells     [E:shields]: Craft Equipment',
    '',
    '[Left Click / Enter:shields]: Move, or cast the selected spell at the cursor',
    '[Right Click / Esc:shields]: Cancel the current spell or exit the current menu',
    '',
    '[Numpad:shields]:',
    '  [7 8 9:shields]',
    '  [4   6:shields]  -> Move the wizard or targeting reticule one space in the corresponding direction',
    '  [1 2 3:shields]',
    '',
    '[Numpad 5:shields]: Pass the turn',
    '',
    '[1 2 3 4 5 6 7 8 9 0:shields]: Cast/Select spell 1-10',
    '',
    '[PGUP/PGDN or Mouse Wheel:shields]: Next/Previous page to view related information while examining something',
]

STATUS_LINES = [
    '',
    '[asseticon:stun] Stunned: Cannot act',
    '[asseticon:petrified] Petrified: Cannot act. Gain 100 Ice and Lightning resist. Gain 75 Physical and Fire resist',
    '[asseticon:clarity] Clarity: Removes and prevents Stunned, Fear, Petrified, Glassified, Frozen, and Silenced',
]

PAGES = [
    {'title': 'CONTROLS:', 'lines': CONTROLS_LINES},
    {'title': 'STATUS EFFECTS:', 'lines': STATUS_LINES},
]


# --- _clean_line ---

def test_clean_line_drops_asseticon_glyph():
    assert _clean_line('[asseticon:stun] Stunned: Cannot act') == 'Stunned: Cannot act'

def test_clean_line_keeps_keybind_label():
    assert _clean_line('[H:shields]: Help (This Screen)') == 'H: Help (This Screen)'

def test_clean_line_keeps_multichar_label():
    assert _clean_line('[SH:shields] or Shields, block an instance of damage') == \
        'SH or Shields, block an instance of damage'

def test_clean_line_blank_stays_blank():
    assert _clean_line('') == ''


# --- _split_multibinding ---

def test_split_multibinding_splits_four():
    line = 'H: Help (This Screen)      C: Character Sheet      S: Learn Spells     E: Craft Equipment'
    assert _split_multibinding(line) == [
        'H: Help (This Screen)', 'C: Character Sheet', 'S: Learn Spells', 'E: Craft Equipment',
    ]

def test_split_multibinding_single_passes_through():
    line = 'Left Click / Enter: Move, or cast the selected spell at the cursor'
    assert _split_multibinding(line) == [line]

def test_split_multibinding_requires_colons_in_all_parts():
    # 3+ spaces but not every part is a binding -> keep as one unit.
    line = '4   6  -> Move the wizard'
    assert _split_multibinding(line) == [line]


# --- numpad folding ---

def test_numpad_folds_to_one_node():
    nodes = _build_body_nodes(CONTROLS_LINES)
    numpad = [n for n in nodes if n.text.startswith('Numpad.')]
    assert len(numpad) == 1

def test_numpad_compass_mapping_and_rows():
    nodes = _build_body_nodes(CONTROLS_LINES)
    numpad = next(n for n in nodes if n.text.startswith('Numpad.'))
    assert numpad.text == (
        'Numpad. Move the wizard or targeting reticule one space in the '
        'corresponding direction. '
        '7 northwest, 8 north, 9 northeast. 4 west, 6 east. '
        '1 southwest, 2 south, 3 southeast'
    )

def test_numpad5_stays_its_own_binding():
    # "Numpad 5: Pass the turn" must NOT be eaten by the grid folder.
    nodes = _build_body_nodes(CONTROLS_LINES)
    assert any(n.text == 'Numpad 5: Pass the turn' for n in nodes)

def test_spell_select_row_is_a_binding_not_a_grid():
    nodes = _build_body_nodes(CONTROLS_LINES)
    assert any(n.text == '1 2 3 4 5 6 7 8 9 0: Cast/Select spell 1-10' for n in nodes)


# --- multi-binding row produces four separate nodes ---

def test_controls_first_row_yields_four_nodes():
    nodes = _build_body_nodes(CONTROLS_LINES)
    labels = [n.text for n in nodes[:4]]
    assert labels == [
        'H: Help (This Screen)', 'C: Character Sheet', 'S: Learn Spells', 'E: Craft Equipment',
    ]


# --- status effects: one node each, icon dropped ---

def test_status_effects_one_node_each():
    nodes = _build_body_nodes(STATUS_LINES)
    assert [n.text for n in nodes] == [
        'Stunned: Cannot act',
        'Petrified: Cannot act. Gain 100 Ice and Lightning resist. Gain 75 Physical and Fire resist',
        'Clarity: Removes and prevents Stunned, Fear, Petrified, Glassified, Frozen, and Silenced',
    ]


# --- full model: heading node 0, title cleaned ---

def test_model_heading_is_node_zero():
    model = build_how_to_play_model(PAGES)
    assert model[0].nodes[0] == Node('Controls', 0, 0)
    assert model[1].nodes[0] == Node('Status Effects', 0, 0)

def test_model_page_count():
    model = build_how_to_play_model(PAGES)
    assert len(model) == 2


# --- FocusController navigation ---

def _ctrl():
    return FocusController(
        build_how_to_play_model(PAGES), screen_label="How to Play",
        nav_hint="Up and down to read, left and right to change page.",
    )

def test_enter_announces_label_hint_meta_and_heading():
    c = _ctrl()
    assert c.enter() == (
        'How to Play. Up and down to read, left and right to change page. '
        'Page 1 of 2. Controls'
    )

def test_enter_without_hint_omits_it():
    c = FocusController(build_how_to_play_model(PAGES), screen_label="How to Play")
    assert c.enter() == 'How to Play. Page 1 of 2. Controls'

def test_down_walks_into_body():
    c = _ctrl()
    assert c.down() == 'H: Help (This Screen)'
    assert c.down() == 'C: Character Sheet'

def test_up_at_top_gives_edge_cue():
    c = _ctrl()
    assert c.up() == 'Top.'
    assert c.node_index == 0

def test_down_at_bottom_gives_edge_cue():
    c = _ctrl()
    last = len(c.pages[0].nodes) - 1
    c.node_index = last
    assert c.down() == 'End of page.'
    assert c.node_index == last

def test_up_down_do_not_cross_pages():
    c = _ctrl()
    c.node_index = 0
    c.up()  # clamps, stays on page 0
    assert c.page_index == 0

def test_set_page_resets_to_heading_and_announces():
    c = _ctrl()
    c.down(); c.down()  # move focus into body
    c.set_page(1)
    assert c.page_index == 1 and c.node_index == 0
    assert c.announce_page() == 'Page 2 of 2. Status Effects'

def test_set_page_clamps_out_of_range():
    c = _ctrl()
    c.set_page(99)
    assert c.page_index == 1  # last page


# --- robustness: empty model doesn't explode ---

def test_empty_model_is_safe():
    c = FocusController([], screen_label="X")
    # up/down/set_page on an empty model must not raise.
    assert c.up() == 'Top.'
    assert c.down() == 'End of page.'
    c.set_page(0)
    assert c.page_index == 0
