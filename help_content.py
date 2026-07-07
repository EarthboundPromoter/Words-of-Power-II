"""
Words of Power help content — the mod's own keybind/tips pages, authored in
RW3's native How-to-Play markup so the game's own renderer draws them and our
FocusController (screen_model.py) reads them, from a single source.

This is the passive catalog (the agreed approach): a hand-authored table that the
help screen reads. The level/shop input handlers keep their inline key dispatch;
this catalog sits beside them. Keep the two in sync by hand — when a mod key
changes in screen_reader.py, update the matching line here.

Shape mirrors RiftWizard3.get_how_to_play_source_sections(): a list of
(PAGE_TITLE, [raw_text]) tuples that the patched source-sections method either
appends after the native pages (native H / menu) or shows standalone (F1).

Pages are split by theme. Key labels are styled [KEY:shields] to match the native
pages' key chips; only mod-specific keys appear here (game keys like Look /
Character Sheet already live on the native pages). Literal bracket keys are
spelled "Left Bracket" / "Right Bracket" so they don't collide with the
[tag:style] markup parser.

Punctuation note: the rendered text uses plain ASCII punctuation (periods,
commas, colons, semicolons) rather than em dashes. The game font is a pixel font
of unknown glyph coverage, and a missing em-dash glyph would draw as a tofu box;
ASCII is guaranteed to render. Meaning is unchanged.
"""

# --- Page bodies (native-markup raw text; leading newline matches native) ---

_SCAN_CREATURES = """
The scan keys are the game's own highlight keys: the key that lights a category on screen is the key that speaks it. Tap to scan; hold to show the tiles. In look mode, deploy, and pure teleport targeting, every scan or pin press also parks the cursor on what it spoke, so T, D, and Enter act on it directly; while aiming any other spell, scans leave your aim alone.
[F:shields]: Health, shields, SP, and active buffs and debuffs. Shift+F gives an ally overview
[I:shields]: Enemy scan. Repeat to cycle, nearest first; Shift reverses
[N:shields]: Spawner scan. Repeat to cycle, nearest first; Shift reverses
[U:shields]: Ally scan. Repeat to cycle, nearest first; Shift reverses
[O:shields]: Landmark scan. Rifts, shops, shrines, crafting components, memory orbs, and ruby hearts. Repeat to cycle, nearest first; Shift reverses
"""

_PINS_CURSOR = """
Pins are your cross-category shortlist: any scan result or tile, remembered, tracked each turn, and cycled on one key.
[K:shields]: Pin cycle. Your pinned targets in blocks: enemies, allies, landmarks, bookmarks; nearest first within each block. Repeat to cycle; Shift reverses; Ctrl+K jumps block to block
[Alt + K:shields]: Pin or unpin the last spoken target. With nothing spoken it bookmarks the tile you're on or looking at. The newest pin is the focused one: it speaks a step toward it each turn, and every pin announces when it dies or disappears
[Alt + I/N/O/U:shields]: The same pin toggle, straight off a scan
[J:shields]: Jump the cursor to the last spoken scan or pin result. From normal play it opens look mode on it; while aiming a spell it moves the aim. Says gone, and stays put, if the target died since it spoke
[Shift + J:shields]: Jump back to where the cursor was before the last jump
"""

_SCAN_SURROUNDINGS = """
[L:shields]: Enemies in your line of sight, by type and direction. The game also highlights the tiles you can see
[T:shields]: Whether you're threatened. In look mode, whether the targeted square is threatened
[Alt + L:shields]: Latch the line of sight overlay: it stays drawn, and every cursor step adds in sight or out of sight. From normal play it follows you; from look, aiming, or deploy it watches from that tile. Same chord releases
[Alt + T:shields]: Latch the threat overlay: every cursor step adds threatened or clear. Examine an enemy first to latch just that enemy's reach. One latch at a time; F reports what's latched
[X:shields]: Hazard scan. Clouds and webs
[B:shields]: Spatial scan. Walkable distance in eight directions
[G:shields]: Charges. The active spell if you're targeting, otherwise all your spells
[D:shields]: Full detail of whatever is under the cursor
[P:shields]: Reports the path to the cursor in look mode. Shift+P reports the path to the focused pin
"""

_MOVEMENT_SPEECH = """
[Two arrows together:shields]: Diagonal movement. Up and Right together is northeast, Down and Left is southwest, and so on. Press the pair as one gesture
[Shift + Arrow:shields]: Move the cursor 4 tiles. Speaks the landing tile, then a short summary of everything crossed, floor included. Says Edge when the map edge cuts the move short
[Shift + two arrows:shields]: The 4 tile move, diagonal. Same landing and crossed summary
[Ctrl + direction:shields]: Jump the cursor along that line until what you would hear changes: past open floor to the next unit, item, or wall. Speaks the landing, then the distance, like 6 east. Says Edge when the map ends the run. Either Ctrl; works with arrows, arrow pairs, and the numpad
[Numpad:shields]: Keep NumLock on. Bare numpad moves and diagonals work; with NumLock off the numpad belongs to your screen reader's review keys, which return when you leave the game. Shift+numpad 4 tile moves only work without NVDA running, because NVDA swallows that chord below the game; use the arrow gestures instead, which always work
[Shift + Tab:shields]: Previous target while targeting, walking, or looking. A fresh press starts from the far end of the list, so in walk mode it jumps straight to the rifts. Needs the Steam overlay turned off for this game, or Steam swallows the keypress. Steps are in the read me
[Ctrl:shields]: Cancel speech. Either control key
[Z:shields]: Repeat the current line
[Left Bracket / Right Bracket:shields]: Speech history back and forward
[F1 or Shift+/:shields]: Open this reference
"""

_SHOPS_CRAFTING = """
[Comma:shields]: Read the current filter page. Each filter value, its hotkey, and whether it's on. Hold Shift for the shadow category
[I:shields]: While crafting, the item being built
[R:shields]: While crafting, recipe progress
"""

_DEPLOY = """
Deploy is the start-of-level placement phase. Move the cursor with the arrows and press Enter to set where your wizard appears. These keys scout the level first.
[1:shields]: Quadrant overview. Enemies, spawners, and items by quadrant
[2:shields]: Cycle to memory orbs
[3:shields]: Cycle to pickups. Components and ruby hearts
[4:shields]: Cycle to spawners
[5:shields]: Cycle to shops and shrines
[L and T:shields]: Also work while placing, measured from the deploy cursor
"""

_TIPS = """
Scans always measure from the cursor, not just your wizard. So in look mode or while deploying, threat and line of sight tell you what would threaten you, and what you'd see, from that tile. They scout, not just report.
Spatial scan (B) reports how far you can walk in each of the eight directions. A fast way to feel out corridors and rooms.
Examine an enemy, then press T to hear whether that one can hit you.
Be creative with what you pin. Depending on the moment, an opportune pickup can matter as much as a spawner or a memory orb.
The path report ignores threat entirely. It can and will route you through danger, so think before you follow it.
Mind your spell charges. Charge economy can end a run as fast as bad positioning.
Use chokepoints. Every adjacent enemy gets an attack each turn.
Synergy is king. Seek upgrades, equipment, and spells that work together.
Each action passes a turn, and every enemy and effect advances with it; movement is never free.
"""


# Title, body. Titles use the native ALL-CAPS, trailing-colon style.
_SECTIONS = [
    ("WORDS OF POWER: SCAN CREATURES:", _SCAN_CREATURES),
    ("WORDS OF POWER: PINS AND CURSOR JUMPS:", _PINS_CURSOR),
    ("WORDS OF POWER: SCAN SURROUNDINGS:", _SCAN_SURROUNDINGS),
    ("WORDS OF POWER: MOVEMENT, SPEECH AND HELP:", _MOVEMENT_SPEECH),
    ("WORDS OF POWER: SHOPS AND CRAFTING:", _SHOPS_CRAFTING),
    ("WORDS OF POWER: DEPLOY:", _DEPLOY),
    ("WORDS OF POWER: TIPS:", _TIPS),
]


def get_mod_help_sections():
    """Return the mod's help pages as RW3 source-section tuples:
    [(page_title, [raw_text]), ...]."""
    return [(title, [body]) for title, body in _SECTIONS]
