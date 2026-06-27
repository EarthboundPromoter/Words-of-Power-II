# Words of Power II

Screen-reader mod for **Rift Wizard 3** — the RW3 port of *Words of Power* (RW2).
Speaks game state through NVDA/JAWS (via Tolk).

**Early WIP port.** Core play and combat narration work; the crafting UI just
landed and is lightly tested — expect rough edges.

## Install

1. You need your own copy of **Rift Wizard 3** and a screen reader (NVDA or JAWS).
2. From your `Rift Wizard 3/mods/` folder, clone this repo into a folder named
   `screen_reader` — the folder name must match the mod's entry module:

   ```
   git clone https://github.com/EarthboundPromoter/Words-of-Power-II.git screen_reader
   ```

   Update later with `git pull` from inside that folder.
3. Run `screen_reader/bin/enable_screen_reader.exe` (or, from the folder,
   `python enable_screen_reader.py`), then launch the game — the reader speaks on
   startup. Close the game first; it re-saves options on exit. Windows may warn about
   the unsigned exe — choose *Run anyway*. `--disable` turns the mod back off.

## What's new vs. the RW2 mod — crafting

RW3 swapped the skill tree and consumables for an **equipment-crafting / component**
system, so that's the biggest new surface. Press **e** in-level (or **c** →
*Craft Equipment*) for the blueprint list; each blueprint reads its state —
craftable, owned, or which tags it still needs — plus its recipe and effect. Confirm
a craftable one to enter **component selection** and pick which held components to
spend. Components are tag-based ingredients that drop on the map; a recipe is paid in
component *tags* (e.g. "1 Fire, 1 Nature, 3 any").

## New keys

- **Shop / blueprint list:** **Tab** cycles the filter category, letter keys toggle
  filters, **comma** reads the full filter list, backtick toggles "can afford."
- **Component selection:** **Enter** adds/removes the highlighted component (you hear
  which requirements it fills), **I** re-reads the item you're building, **R** reads
  recipe progress.

## Component readout

Open the Character Sheet (**c**) to hear your component tag bank — e.g.
"Components: 3 Fire, 2 Ice, 1 Nature" — the stock crafting spends from.
(Per-component keyboard browsing isn't in yet; it's coming.)

## Combat speech (mid-refactor)

Combat narration is partway through a rewrite. The new **speech-composition layer**
(which summarizes each turn's effects into cleaner utterances) ships **on**, and the
**original speech model runs alongside it** for now while the new one is validated.
You may occasionally hear overlap — that's expected during this transition.
