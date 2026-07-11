# The Cursor Update — Transition Guide

**Applies to:** versions 0.5.0 through 0.6.0, coming from 0.3.4 (drafted 2026-07-07,
updated 2026-07-11). This guide is
for players of the previous version: it covers what moved, what changed, and what's new.
If you're new to the mod entirely, read [README.md](README.md) instead — it describes
everything as it now is.

## Quick start — enough to keep playing

Three keys moved, one answer changed, one habit to relearn, one new chord worth
knowing on day one:

- **Enemy scan is now I** (was J). **Ally scan is now U** (was Y). **Landmark scan is
  now O** (was Q). These are the game's own highlight keys — tap to scan, hold to
  light the tiles. The old letters no longer scan.
- **Careful: J does something new.** J now jumps the cursor to the last thing a scan
  spoke. If muscle memory sends you to J for enemies, you'll land in Look mode on top
  of your last result — press Escape and use I.
- **T now answers "Threatened" or "Safe"** for your square, instantly. To hear *who*,
  examine an enemy and press T, same as before.
- **Diagonals: press two arrows together** (Up+Right is northeast). Right Ctrl + arrow
  no longer does diagonals. Numpad diagonals are unchanged.
- **Shift + Tab is now a targeting key.** It cycles targets backward — the reverse of
  the game's Tab — while targeting, walking, or looking. It did nothing before, so
  there's nothing to unlearn, but it needs the Steam overlay turned off for this game
  first (setup steps in the README).

Everything else you knew still works. When you're ready for the new tools — pins,
cursor jumps, overlay latches — read on, or press Shift + / in game for the full
reference.

## What hasn't changed

Your spawner scan (N), hazard scan (X), line-of-sight readout (L, still the full
count by type and direction), vitals (F), charges (G), detail (D), paths (P), spatial
scan (B), repeat (Z), speech history (brackets), help (F1, Shift + /), the deploy
keys 1–5, all shop and crafting keys, the game's Tab cycle, and every one of the
game's own keys. Examining an enemy and pressing T for "Threatens you" / "Can't
hit you" also works exactly as before.

## The changes, one by one

### Scan keys moved onto the game's highlight keys

Before, the scans sat on letters the mod picked: J (enemies), Y (allies), Q
(landmarks). Now the key that lights a category on screen is the key that speaks it:
**I** highlights and scans enemies, **U** allies, **O** landmarks. Tap to scan, hold
to draw the highlight — and the pairing follows your rebinds, so if you rebind
Highlight Enemies, the enemy scan moves with it. The Alt + key pin toggle moved with
the scans. N and X stay put; they have no game highlight. Q and Y now do nothing
(reserved for future features), and J was given a new job, below. This is a hard
switch — there is no old-keys option.

### T answers about you, not about everyone

Before, T listed every enemy that could hit you, with counts and directions — and on
crowded levels, computing that list was the freeze you felt. Now T reads the game's
own threat zone and answers **"Threatened"** or **"Safe"** for your square (the
cursor's square in Look mode), instantly after the first press of a turn. The game
itself shows threat as an anonymous red zone and gates *who* behind examining each
enemy — the mod now matches that. The who-threatens loop is unchanged and, with scans
now parking the cursor (below), takes three keypresses: scan to the enemy, T,
"Threatens you."

### The single mark grew into a pin list

Before, Alt + a scan key marked one target, and marking another replaced it. Now that
gesture **pins**, and you can hold as many pins as you like:

- **Alt + K** pins or unpins the last spoken target. With nothing spoken, it
  bookmarks the tile you're standing on or looking at — bookmarks name themselves
  after what's on the tile ("Bookmark, Mega Chest"), or take a number on bare floor.
  It works from the deploy cursor too, and that pin carries into the level.
- **K** cycles your pins in category blocks — enemies, allies, landmarks, bookmarks —
  nearest first within each block, with a count header on a fresh cycle ("5 pinned.
  2 enemies, 1 ally, 2 bookmarks."). Shift + K reverses; **Ctrl + K** jumps block to
  block. Alt + K on a just-cycled pin unpins it.
- Pins follow their unit as it moves, announce when it dies or is collected, and live
  per level — nothing carries across a rift.
- The newest or last-cycled pin is the **focused** pin: it speaks a short guidance
  line each turn ("North to Wolf, 12 HP."), and **Shift + P** re-announces its full
  path — that key used to path to the old single mark.

Scan lines now say "pinned" where they said "marked." If you relied on marking to
auto-replace, unpin the old target first.

### Scans park the cursor on what they speak

In Look mode, deploy, and pure-teleport aiming (Blink and kin — teleports with no
area to aim), every scan and pin-cycle press now also moves the cursor onto the
result it just spoke. The scan line is the whole announcement — the cursor moves
silently — but T and D immediately answer for that tile, and Enter acts on it. A
cycle keeps measuring from where it started, so repeated presses walk outward instead
of chasing the cursor.

While aiming any spell with an area or a shape, the cursor is your tuned aim and
scans leave it exactly where it is — there, J is the deliberate jump.

### J and Shift + J — jump to what you just heard

**J** jumps the cursor to the last thing a scan or pin cycle spoke. From normal play
it opens Look mode right on it; while aiming, it moves your aim there (any spell — J
is always deliberate). If the target died or was collected since it spoke, J says
"gone" and stays put. **Shift + J** bounces back to where the cursor was before the
jump; a second press bounces forward again.

### Ctrl + Shift + direction — the axis jump

New in 0.5.0 as Ctrl + direction; **0.6.0 moved it to Ctrl + Shift + direction** —
bare Ctrl + direction became the spring look, below. If you learned Ctrl as the jump,
add Shift. In any cursor mode, the chord skims the cursor along that line until what
you'd hear changes — across open floor to the next unit, item, wall, or cloud in one
keypress. It speaks the span crossed, then the landing ("4 floor east, Imp"), and
says "Edge" when the map ends the run. Arrow row only — with NumLock on, Windows
strips Shift from numpad presses. Your wizard never jumps — this is cursor-only.

### Spring look — hold Ctrl to peek (new in 0.6.0)

From normal play, hold Ctrl and press a direction: a look cursor opens at your feet
and steps. Every further press steps it (add Shift to jump), and releasing Ctrl puts
you back in play, cursor gone. Press Enter or V mid-peek to stay in Look mode, or a
spell hotkey to aim that spell where you were looking. Ctrl + Shift + direction from
normal play opens and jumps in one gesture.

### Diagonals are two-arrow chords; Right Ctrl retired

Before, Right Ctrl + arrow stepped diagonally (with an AltGr variant). That's gone:
press **two orthogonal arrows together** — Up+Right is northeast — as one gesture.
Add Shift for the 4-tile diagonal, Ctrl + Shift for the diagonal axis jump. A lone
arrow still steps normally, and held arrows still auto-walk exactly as the game
intends. Ctrl means one thing on either side: bare press cancels speech, chords move
the cursor. If
pressing two arrows at once isn't physically comfortable, the numpad diagonals remain
the modifier-free path.

### Shift + arrow summarizes what you crossed

Before, the 4-tile move read all four tiles in sequence. Now it speaks the landing
tile plus a short "Crossed:" summary of everything skimmed past, grouped with counts
("Crossed: 2 floors, web") — floor is counted so the distance never needs arithmetic.
A move cut short by the map edge appends "Edge"; a press pinned at the edge says
"Edge" instead of nothing.

### Latches — Alt + L and Alt + T keep an overlay on

New. The game's hold-to-see overlays, pinned:

- **Alt + L** latches line of sight; **Alt + T** latches threat. The overlay stays
  drawn without holding the key, and every cursor step gains a short tag — "in
  sight" / "out of sight", "threatened" / "clear".
- Latched from normal play, line of sight **follows you** as you move. Latched from
  Look, aiming, or deploy, it watches from that frozen tile — and a deploy latch
  carries into the level with you.
- Examine an enemy first and Alt + T latches just that enemy's reach ("Latched:
  Night Hag's threat").
- One latch at a time — setting one releases the other. The same chord releases it, F
  reports what's latched, and holding the real key still works and takes over while
  held. A latch ends on its own, with an announcement, when you leave the level or
  its target dies.

### Fixes riding along

- **Speech cancel works now.** Bare Ctrl — either side — cancels speech. It was
  advertised before but only your screen reader's own interrupt was firing; queued
  mod speech kept arriving. Now the mod's queue clears too.
- **Shift + numpad 4-tile moves are repaired** — when NVDA isn't running. A Windows
  keyboard legacy ("fake shift") broke the chord for everyone, sighted players
  included; the mod now repairs it. Under NVDA the keypress is swallowed before any
  application can see it, so use Shift + arrows there — they're immune.

## Where it might bite — and how to report it

The honest list of what's most likely to confuse or break, roughly in order:

- **Old muscle memory on J.** Pressing J for an enemy scan now jumps you into Look
  mode. Nothing is broken — Escape backs out — but expect to do it a few times.
- **The silently parked cursor.** In Look, deploy, and pure-teleport aiming, scans
  move the cursor without saying so. If the cursor isn't where you left it, a scan
  parked it. Distances during a cycle are measured from where the cycle started, not
  from the moving cursor — that's deliberate.
- **Which teleports park.** "No area to aim" is the rule, and a handful of unusual
  spells get hand-picked exceptions. If aiming a spell feels wrong — the cursor moves
  when it shouldn't, stays when it should move, or a "From destination" label sounds
  off — report the spell's exact name. These are per-spell judgment calls and cheap
  to fix.
- **Two-arrow chording feel.** The mod watches for two arrows pressed as one gesture
  and is tuned to tell a deliberate pair from a fast roll. If you get diagonals you
  didn't ask for, or pairs that won't register, report roughly how you pressed —
  together, rolled, one hand or two.
- **Axis jump landings.** The jump stops where what you'd hear changes. If a landing
  surprises you, report where it stopped, what it said, and where you expected it.
- **Threat on an enemy's own square.** An occupied enemy tile reads "threatened" —
  that's the game's red zone speaking, not a bug, but tell me if the wording trips
  you.
- **Latch edge cases.** Latches end announced when the level changes or the latched
  enemy dies; a latched enemy that gets charmed switches to reach wording instead of
  threat wording. If a latch goes quiet without an announcement, or keeps talking
  after it should have ended, that's a bug — report what was latched and what
  happened in between.
- **NVDA and the numpad.** Shift + numpad under NVDA can never work (see Fixes
  above). NumLock on to play, as always.

**A good report** has: the exact keys you pressed, what you heard, and what you
expected; whether NVDA (or JAWS) was running and NumLock state if the numpad is
involved; the spell name if you were aiming; and whether a latch or pins were active.
Then attach the matching slice of `screen_reader_debug.log` from the mod folder —
cursor parking writes `[Route]` lines and diagonal chords write `[Chord]` lines, so
the log shows what the mod decided even where speech is silent by design.

Send it via **Discord** (directly to the author — fastest) or a
[GitHub issue](https://github.com/EarthboundPromoter/Words-of-Power-II/issues).

## Tested less than the rest

Not expected to break, but these have had machine testing and little real-play time —
reports from these areas are worth the most:

- Bare-tile bookmarks, and deploy-set pins carrying into the level.
- Holding L or T while the other's latch is active (the held key should take over,
  then the latch should return).
- Line-of-sight latch performance on turns that destroy many walls.
- Wording around a latched enemy that changes sides mid-latch.
- Aiming behavior of the odder teleport-family spells — Blink is field-proven, its
  strange cousins less so.
