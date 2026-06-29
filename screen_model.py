"""
Navigable screen model + focus controller for bespoke text UI screens.

This is the *navigation-driven* sibling of the combat composer (pipeline.py).
Where the combat pipeline is time-driven (producers claim journal records at a
turn boundary and flush one utterance), a screen model is navigation-driven:
nothing is spoken until a key press, and each press speaks exactly one focused
unit. The shared spine is the same as the composer's: raw game data -> a
structured intermediate representation -> rendered speech. Here the IR is a list
of Pages, each a list of Nodes, plus a cursor.

First (and currently only) client: the How to Play screen
(RiftWizard3.get_how_to_play_pages / draw_how_to_play). The screen pages with
left/right in RW3's own UI; this model adds sensible up/down navigation by
*logical unit* — one keybinding, one tip, one status effect per node — rather
than by rendered row. A single physical line that packs four keybindings becomes
four nodes; the multi-row numpad diagram collapses into one compass-mapped node.

Because How to Play is the only screen of this kind today, the builder is allowed
to special-case its text (the numpad grid, the [asseticon:*] status glyphs). A
second screen would warrant a more general bucketing rule. The FocusController,
by contrast, is deliberately generic over "pages of nodes" so it can be reused.

Each Node carries a `level` field (0 = heading, 1 = item) even though navigation
is flat today — that keeps a future two-level (heading -> indented children)
model a cheap addition rather than a rewrite.
"""

import re
from collections import namedtuple

from helpers import _clean_desc

# A navigable unit. `level`: 0 = page heading, 1 = body item. `group`: index of
# the blank-line-delimited source group it came from (not announced yet; reserved
# for future "group transition" cues).
Node = namedtuple('Node', ['text', 'level', 'group'])

# A page of the screen: a display title plus its nodes (node 0 is the heading).
Page = namedtuple('Page', ['title', 'nodes'])


# Standard numpad movement layout -> compass direction. 5 (centre) is excluded;
# in RW3's text "Numpad 5: Pass the turn" is a separate binding, not a move.
_NUMPAD_DIR = {
    '7': 'northwest', '8': 'north', '9': 'northeast',
    '4': 'west',                     '6': 'east',
    '1': 'southwest', '2': 'south',  '3': 'southeast',
}


def _clean_line(raw):
    """Transcode one raw How-to-Play source line into speech text.

    Two markup families appear in this screen:
      * [asseticon:NAME] — a leading status-effect icon glyph with no spoken
        value. Dropped entirely (else _clean_desc would render it as the literal
        word "asseticon").
      * [LABEL:style]   — a styled keybind/term, e.g. [H:shields] or [SH:shields].
        _clean_desc keeps the label ("H", "SH") and discards the style.
    """
    s = re.sub(r'\[asseticon:[^\]]*\]', '', raw)
    s = _clean_desc(s)
    return s.strip()


def _split_multibinding(line):
    """Split a single physical line that packs several keybindings (separated by
    3+ spaces, each part of the form "KEY: desc") into one entry per binding.
    Lines that aren't multi-binding pass through as a single entry."""
    parts = re.split(r'\s{3,}', line)
    nonempty = [p.strip() for p in parts if p.strip()]
    if len(nonempty) > 1 and all(':' in p for p in nonempty):
        return nonempty
    return [line]


# A numpad grid row after cleaning: only digits/spaces, optionally trailed by
# "-> shared description". Group 1 = the digit cells, group 2 = the description.
_GRID_ROW_RE = re.compile(r'^([0-9 ]+?)\s*(?:->\s*(.*))?$')


def _consume_numpad(lines, header_idx, group):
    """Starting at a "Numpad:" header line, fold the following digit-grid rows
    into one compass-mapped Node. Returns (node_or_None, next_index).

    The grid's three rows share one description (attached to the middle row in
    RW3's text). Output preserves the row grouping the owner asked for:
    "Numpad. <desc>. 7 northwest, 8 north, 9 northeast. 4 west, 6 east.
     1 southwest, 2 south, 3 southeast."
    """
    rows = []
    desc = ''
    j = header_idx + 1
    while j < len(lines):
        c = _clean_line(lines[j])
        if not c:
            break
        m = _GRID_ROW_RE.match(c)
        if not m:
            break
        digit_cells = m.group(1)
        if not any(ch.isdigit() for ch in digit_cells):
            break
        rows.append([d for d in digit_cells.split() if d in _NUMPAD_DIR])
        if m.group(2):
            desc = m.group(2).strip()
        j += 1

    if not rows:
        return None, header_idx + 1

    row_strs = []
    for row in rows:
        cells = [f"{d} {_NUMPAD_DIR[d]}" for d in row]
        if cells:
            row_strs.append(", ".join(cells))

    text = "Numpad"
    if desc:
        text += f". {desc}"
    if row_strs:
        text += ". " + ". ".join(row_strs)
    return Node(text, 1, group), j


def _build_body_nodes(lines):
    """Turn a page's raw source lines into a flat list of navigable Nodes.

    Per-line is the default unit; blank lines delimit groups (skipped, not
    navigable). Two exceptions: a multi-binding line splits into one node per
    binding, and the numpad header + its digit rows fold into a single node.
    """
    nodes = []
    group = 0
    i = 0
    n = len(lines)
    while i < n:
        cleaned = _clean_line(lines[i])
        if not cleaned:
            group += 1
            i += 1
            continue

        if re.match(r'(?i)^numpad:?$', cleaned):
            node, j = _consume_numpad(lines, i, group)
            if node is not None:
                nodes.append(node)
                i = j
                continue

        for part in _split_multibinding(cleaned):
            nodes.append(Node(part, 1, group))
        i += 1
    return nodes


def _title_text(raw_title):
    """RW3 page titles are shouty with a trailing colon ("CONTROLS:"). Render as
    a clean spoken heading ("Controls")."""
    return (raw_title or '').rstrip(':').strip().title()


def build_how_to_play_model(pages):
    """Build the navigable model from RW3's get_how_to_play_pages() output.

    `pages` is a list of {"title": str, "lines": [str, ...]} dicts (raw source
    lines, still carrying [...] markup). Returns a list of Page namedtuples whose
    node 0 is the page heading and whose remaining nodes are the body units.
    Pure: takes data in, returns data out — unit-testable off-game.
    """
    out = []
    for p in pages:
        title = _title_text(p.get('title'))
        nodes = [Node(title, 0, 0)]
        nodes.extend(_build_body_nodes(p.get('lines') or []))
        out.append(Page(title, nodes))
    return out


class FocusController:
    """A flat cursor over a paged list of Nodes.

    Up/down move within the current page (clamped, with a terse edge cue — they
    do NOT cross page boundaries; that's what left/right paging is for). Paging
    is owned by the host screen (RW3 advances how_to_play_page); the host calls
    set_page() to sync this cursor after a page change. Generic over any
    `pages` of `nodes` — How to Play is just the first client.
    """

    def __init__(self, pages, screen_label="", nav_hint=""):
        self.pages = pages
        self.screen_label = screen_label
        # Spoken once on entry, after the screen label and before the page/heading
        # — e.g. "Up and down to read, left and right to change page." Client-
        # supplied (not baked) so a single-page client can omit the paging cue.
        self.nav_hint = nav_hint
        self.page_index = 0
        self.node_index = 0

    @property
    def _nodes(self):
        if not self.pages:
            return []
        return self.pages[self.page_index].nodes

    def _current_text(self):
        nodes = self._nodes
        if not nodes:
            return ""
        return nodes[self.node_index].text

    def _page_meta(self):
        return f"Page {self.page_index + 1} of {len(self.pages)}."

    def set_page(self, idx):
        """Sync to the host's page index and reset focus to the heading."""
        if not self.pages:
            return
        self.page_index = max(0, min(len(self.pages) - 1, idx))
        self.node_index = 0

    def enter(self):
        """Utterance for first entering the screen: label, nav hint, then the
        current page position and heading."""
        parts = []
        if self.screen_label:
            parts.append(f"{self.screen_label}.")
        if self.nav_hint:
            parts.append(self.nav_hint)
        parts.append(self._page_meta())
        body = self._current_text()
        if body:
            parts.append(body)
        return " ".join(parts).strip()

    def announce_page(self):
        """Utterance after a page change (host already moved the page; cursor was
        reset by set_page)."""
        return f"{self._page_meta()} {self._current_text()}".strip()

    def up(self):
        if self.node_index <= 0:
            self.node_index = 0
            return "Top."
        self.node_index -= 1
        return self._current_text()

    def down(self):
        last = len(self._nodes) - 1
        if self.node_index >= last:
            self.node_index = max(0, last)
            return "End of page."
        self.node_index += 1
        return self._current_text()
