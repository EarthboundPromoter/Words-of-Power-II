# Tests for search-box speech — the (focused, query) diff that voices the
# game's shop/bestiary/combat-log search bars, the SyncTTS interrupt tier it
# speaks counts on, and the combat log's query-aware line source (which also
# fixes a desync: the mod used to read combat_log_lines while the screen
# showed combat_log_match_lines whenever a query was active — draw_combat_log,
# RiftWizard3.py:9922).
#
# Like test_shop_prop.py, the functions under test live in screen_reader.py
# (module level for the shared transition, nested in the installer closure for
# the combat log helpers) — not importable without the game — so this file
# extracts their source by signature markers and execs it. A renamed/moved
# function breaks extraction LOUDLY at collection; it can never pass silently.
#
# Run from the game root.

import textwrap
import types
from collections import deque
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


def _extract(marker, terminator, dedent):
    start = _src.index(marker)
    end = _src.index(terminator, start)
    block = _src[start:end]
    return textwrap.dedent(block) if dedent else block


class _Recorder:
    """Stands in for async_tts: records (text, tier) per utterance."""
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append((text, 'normal'))

    def speak_interrupting(self, text):
        self.spoken.append((text, 'interrupt'))


class _BaseTTS:
    """Stands in for the Tolk/NVDA backend under SyncTTS: records call order."""
    def __init__(self):
        self.calls = []

    def speak(self, text):
        self.calls.append(('speak', text))

    def cancel(self):
        self.calls.append(('cancel', None))


# ---- module-level search transition (0-indent) ----
_ns = {
    'log': lambda *a, **k: None,
    'cfg': types.SimpleNamespace(search_key_echo=False),
}
exec(_extract("_SEARCH_PROMPT_SHOP = ",
              "# ============================================================================\n# SPEECH BATCHING", False), _ns)

# ---- newline transcode (0-indent, next to _desc_text) ----
_tc_ns = {}
exec(_extract("_CLAUSE_END = ", "def _desc_text(obj):", False), _tc_ns)
_to_clauses = _tc_ns['_newlines_to_clauses']

# ---- SyncTTS (0-indent class) ----
_tts_ns = {'log': lambda *a, **k: None, 'deque': deque}
exec(_extract("class SyncTTS:", "async_tts = SyncTTS(tts)", False), _tts_ns)
SyncTTS = _tts_ns['SyncTTS']

# ---- combat log helpers (4-indent, nested in the installer closure) ----
_cl_ns = {}
exec(_extract("    def _combat_log_current_line(view):",
              "    def _patched_process_combat_log(self):", True), _cl_ns)
_cl_line = _cl_ns['_combat_log_current_line']
_cl_count = _cl_ns['_combat_log_count']


def _speak(was_focused, was_query, focused, query, readout=False,
           count="7 results", echo=False, landing=None):
    """Drive one transition; return the recorder's spoken list."""
    rec = _Recorder()
    _ns['async_tts'] = rec
    _ns['cfg'].search_key_echo = echo
    _ns['_speak_search_transition'](
        was_focused, was_query, focused, query, readout,
        lambda: count, _ns['_SEARCH_PROMPT_SHOP'],
        landing_fn=(lambda: landing) if landing is not None else None)
    return rec.spoken


# ---- SyncTTS interrupt tier ----


def test_speak_interrupting_cancels_then_speaks():
    base = _BaseTTS()
    tts = SyncTTS(base)
    tts.speak_interrupting("3 results")
    assert base.calls == [('cancel', None), ('speak', "3 results")]


def test_consecutive_interrupts_coalesce_in_history():
    tts = SyncTTS(_BaseTTS())
    tts.speak("Fire tag on")            # normal utterance stays
    tts.speak_interrupting("31 results")
    tts.speak_interrupting("12 results")
    tts.speak_interrupting("4 results")
    assert list(tts._history) == ["Fire tag on", "4 results"]


def test_normal_speak_after_interrupt_is_not_coalesced():
    tts = SyncTTS(_BaseTTS())
    tts.speak_interrupting("12 results")
    tts.speak("Search: fir. 12 results")
    tts.speak_interrupting("5 results")
    assert list(tts._history) == ["12 results", "Search: fir. 12 results", "5 results"]


# ---- focus transitions ----


def test_focus_gained_empty_speaks_prompt():
    spoken = _speak(False, "", True, "")
    assert len(spoken) == 1
    text, tier = spoken[0]
    assert tier == 'normal'
    assert text.startswith("Search.")
    assert "Enter keeps" in text and "Down arrow" in text


def test_focus_gained_with_kept_query_prefixes_it():
    spoken = _speak(False, "fir", True, "fir")
    text, tier = spoken[0]
    assert text.startswith("Search: fir. 7 results. ")
    assert tier == 'normal'


def test_enter_keeps_query_speaks_it_with_count():
    spoken = _speak(True, "fireball", False, "fireball", count="2 results")
    assert spoken == [("Search: fireball. 2 results", 'normal')]


def test_enter_keep_appends_landing_read():
    # The filter's first surviving result rides the Enter announcement — with
    # a single result there may be nothing to arrow to (combat log field
    # report 2026-07-10)
    spoken = _speak(True, "fireball", False, "fireball", count="1 matching line",
                    landing="You cast Fireball")
    assert spoken == [("Search: fireball. 1 matching line. You cast Fireball",
                       'normal')]


def test_enter_keep_with_empty_landing_has_no_dangling_separator():
    spoken = _speak(True, "fireball", False, "fireball", count="No results",
                    landing="")
    assert spoken == [("Search: fireball. No results", 'normal')]


def test_escape_clears_query_speaks_cleared():
    spoken = _speak(True, "fir", False, "", count="40 results")
    assert spoken == [("Search cleared. 40 results", 'normal')]


def test_close_on_empty_box_speaks_closed():
    spoken = _speak(True, "", False, "")
    assert spoken == [("Search closed", 'normal')]


# ---- typing: silent keys, live count on the interrupt tier ----


def test_typing_echo_off_speaks_count_only():
    spoken = _speak(True, "fi", True, "fir", count="12 results")
    assert spoken == [("12 results", 'interrupt')]


def test_typing_echo_on_speaks_char_then_count():
    spoken = _speak(True, "fi", True, "fir", count="12 results", echo=True)
    assert spoken == [("r. 12 results", 'interrupt')]


def test_space_echoes_as_the_word_space():
    spoken = _speak(True, "bone", True, "bone ", count="3 results", echo=True)
    assert spoken == [("space. 3 results", 'interrupt')]


def test_backspace_echo_on_names_deleted_char():
    spoken = _speak(True, "fir", True, "fi", count="31 results", echo=True)
    assert spoken == [("r deleted. 31 results", 'interrupt')]


def test_backspace_echo_off_speaks_count_only():
    spoken = _speak(True, "fir", True, "fi", count="31 results")
    assert spoken == [("31 results", 'interrupt')]


def test_backspace_to_empty_says_empty():
    spoken = _speak(True, "f", True, "", count="40 results")
    assert spoken == [("Empty. 40 results", 'interrupt')]


def test_count_error_path_stays_silent_on_typing():
    # _filter_result_count returns "" when the view throws — no utterance
    spoken = _speak(True, "fi", True, "fir", count="")
    assert spoken == []


def test_no_change_speaks_nothing_and_returns_false():
    rec = _Recorder()
    _ns['async_tts'] = rec
    result = _ns['_speak_search_transition'](
        True, "fir", True, "fir", False, lambda: "7 results",
        _ns['_SEARCH_PROMPT_SHOP'])
    assert result is False
    assert rec.spoken == []


# ---- Down readback ----


def test_readout_speaks_query_and_count_interrupting():
    spoken = _speak(True, "fir", True, "fir", readout=True, count="12 results")
    assert spoken == [("Search: fir. 12 results", 'interrupt')]


def test_readout_on_empty_box():
    spoken = _speak(True, "", True, "", readout=True, count="40 results")
    assert spoken == [("Search empty. 40 results", 'interrupt')]


# ---- combat log: query-aware line source (the desync fix) + count ----


def _cl_view(**kw):
    defaults = dict(combat_log_query="", combat_log_match_lines=[],
                    combat_log_lines=[], combat_log_offset=0)
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_unfiltered_line_keeps_header_skip():
    view = _cl_view(combat_log_lines=["Turn 3", "Wolf bites you", "You cast Fireball"])
    assert _cl_line(view) == "Wolf bites you"
    view.combat_log_offset = 1
    assert _cl_line(view) == "You cast Fireball"


def test_filtered_line_reads_match_list_from_zero():
    # The screen shows combat_log_match_lines when a query is active — the
    # reader must follow it (previously it kept reading combat_log_lines)
    view = _cl_view(combat_log_query="fire",
                    combat_log_lines=["Turn 3", "Wolf bites you", "You cast Fireball"],
                    combat_log_match_lines=["You cast Fireball"])
    assert _cl_line(view) == "You cast Fireball"


def test_filtered_line_out_of_range_is_empty():
    view = _cl_view(combat_log_query="fire", combat_log_match_lines=["m"],
                    combat_log_offset=5)
    assert _cl_line(view) == ""


def test_combat_log_count_phrasing():
    assert _cl_count(_cl_view(combat_log_query="fire",
                              combat_log_match_lines=[])) == "No matching lines"
    assert _cl_count(_cl_view(combat_log_query="fire",
                              combat_log_match_lines=["a"])) == "1 matching line"
    assert _cl_count(_cl_view(combat_log_query="fire",
                              combat_log_match_lines=["a", "b"])) == "2 matching lines"
    assert _cl_count(_cl_view(combat_log_lines=["h", "a", "b"])) == "3 lines"


# ---- newline transcode (the description mash fix) ----


def test_single_line_passes_through_untouched():
    assert _to_clauses("Deal 9 fire damage.") == "Deal 9 fire damage."


def test_unpunctuated_lines_gain_clause_boundaries():
    # The Moon Glaive shape from the field log: description lines with no
    # terminal punctuation ran together at the synth
    text = ("Hurl a Moon Glaive at the target\n"
            "Deal 14 Arcane and Physical damage in a line\n"
            "Must target an unoccupied tile.")
    assert _to_clauses(text) == ("Hurl a Moon Glaive at the target. "
                                 "Deal 14 Arcane and Physical damage in a line. "
                                 "Must target an unoccupied tile.")


def test_punctuated_lines_gain_nothing_extra():
    # A line already ending in clause punctuation is joined as-is — no ".."
    text = "Deal 16 damage.\nThen heal 4 HP,\nif the target dies"
    assert _to_clauses(text) == "Deal 16 damage. Then heal 4 HP, if the target dies"


def test_stat_list_lines_each_become_clauses():
    # The Knightly Oath shape: bare "Label: value" lines, no terminal punctuation
    text = "Void Knight: Arcane\nStorm Knight: Lightning or Ice"
    assert _to_clauses(text) == "Void Knight: Arcane. Storm Knight: Lightning or Ice"


def test_blank_lines_are_dropped():
    assert _to_clauses("First\n\n\nSecond") == "First. Second"


def test_last_line_never_gains_punctuation():
    assert _to_clauses("One\nTwo") == "One. Two"
    assert _to_clauses("only") == "only"
