# Words of Power II

**Version 0.3.4 — beta**

A screen-reader mod for **Rift Wizard 3**: it speaks the game's state and events
through NVDA or JAWS (via Tolk) so the game can be played without sight. It's the Rift
Wizard 3 successor to *Words of Power*, the Rift Wizard 2 mod.

Early beta — core play, combat narration, and the crafting interface all work, but
some features are lightly tested. Please report anything that sounds wrong or stays
silent when it shouldn't.

(*Words of Power II* is a working title.)

## About Rift Wizard 3

> Rift Wizard 3 is a tough as nails traditional roguelike wizard simulator. You play as an immortal amnesiac wizard who must journey through the cosmos to defeat his nemesis. Each run, you'll build a new repertoire of spells and magical artifacts, discovering new and powerful synergies.
>
> Rift Wizard uses an open build system, and allows the player to buy any spell or spell upgrade at any time. This gives the player massive flexibility, allowing them to test out new ideas each run. Try blasting apart your foes with fire and/or lightning, summoning hordes of metallic constructs, or once you're bored of the simple stuff, delve deep into obscure arcane metamagical trickery.
>
> Rift Wizard's unique portal system means the player always has agency over what challenges they face. You choose which levels to visit, where to start, and how to spend your resources. There is no stealth or stair dancing in Rift Wizard: you must face each level head on and obliterate your foes with whatever tools you possess. Rift Wizard eschews randomness and complex combat formulas in favor of puzzle-like simplicity, where every game piece is easily understandable. Every victory is earned, every death deserved.
>
> New to Rift Wizard 3 is a deep and strategic crafting system. You will find a vast assortment of crafting components strewn across the ruins of the universe, which can be combined to form powerful magical artifacts giving passive buffs to the wizard. The player must make difficult choices about how to allocate their limited and diverse magical resources, with rare crafting ingredients even having unique impacts on the crafted item.
>
> The craftable equipment has a wide variety of effects, ranging from stat bonuses to event triggers. Most passive skills and equipment from the previous games have been brought back in some form as craftable equipment, and more than a hundred new items have been added to the game. Make lightning fly out of your arcane damage, commandeer enemy spawners, give your demon summons fire breath, the possibilities are endless!
>
> Rift Wizard 3 adds a slew of new enemies and bosses to the game, from the spry and pushy Leghead to the alien and terrifying Void Child, from old favorites like Slazephan the Serpent Philosopher to new legends like the vengeful nature god Gaia. The final boss encounters, including Mordred himself, have been greatly expanded and revised as well.
>
> The goal of Rift Wizard 3 is to continually place the player in high pressure tactical situations that challenge their creativity and analytic abilities, and to create a strategic sandbox that rewards brilliance, experimentation, and adaptation to constantly changing circumstances. With every installment in the series, we push farther towards this goal, and we are quite happy to share the depth we have created in Rift Wizard 3.
>
> — [Steam store page](https://store.steampowered.com/app/4366330/Rift_Wizard_3/)

## Requirements

- **Rift Wizard 3** (Steam, Windows).
- A screen reader — **NVDA** or **JAWS** (or any other reader supported by
  [Tolk](https://github.com/dkager/tolk)). Without `Tolk.dll` the mod falls back to
  NVDA only.
- Start your screen reader *before* launching the game, or the mod can't connect.

## Installing and Updating

Both methods put the mod in a folder named exactly `screen_reader` inside your
`Rift Wizard 3/mods/` folder — the name must match the mod's entry file
(`screen_reader.py`).

### Option A — zip (simplest)

Download the latest zip from the
[Releases page](https://github.com/EarthboundPromoter/Words-of-Power-II/releases/latest),
extract it, and copy the `screen_reader` folder into `Rift Wizard 3/mods/` (create
`mods` if it isn't there). To update, download the newest zip and replace the folder.

### Option B — git clone (recommended for the beta)

Fixes land in git before they're bundled into a zip, so cloning gets you updates first.
From your `Rift Wizard 3/mods/` folder:

```
git clone https://github.com/EarthboundPromoter/Words-of-Power-II.git screen_reader
```

To update later, run `git pull` from inside that folder.

### Turning the mod on

Rift Wizard 3 only loads mods listed in its options file, and editing that file before
any speech is running is a catch-22. A bundled program handles it:

1. Close the game if it's open (it re-saves options on exit and would undo the change).
2. Run `screen_reader/bin/enable_screen_reader.exe` (or `python enable_screen_reader.py`
   from the folder). Windows may warn the program is unsigned — choose *Run anyway*.
   `--disable` turns the mod back off.
3. Launch the game. You'll hear "Words of Power version 0.3.4" if it's working. A debug
   log is written to `screen_reader_debug.log` in the mod folder.

The very first screen on launch is **Language select** (voiced, English only) — just
press **Enter** to pass it and reach the main menu.

## What's New in Rift Wizard 3 (vs. Rift Wizard 2)

The biggest changes are in the game itself, not just the mod:

- **Components and crafting replace consumables.** Rift Wizard 2's one-shot consumables
  are gone; **components** drop on the map in their place. Every component you pick up
  banks into your stock to spend later on **crafting equipment** — recipes are paid in
  component *tags* ("1 Fire, 1 Nature, 3 of anything"), not specific items. They come in
  two kinds: **common** components also fire a one-shot effect the instant you pick them
  up, while **rare** ones have no pickup effect and instead permanently boost the
  equipment you craft them into.
- **Equipment, and no equipment slots.** Equipment is crafted, and there are no slots
  to fill — wear as many of any type as you can craft (multiple staves, multiple hats,
  whatever). Much of Rift Wizard 2's skill tree now comes from equipment instead.
- **Spell upgrades.** Each spell can take two upgrades now instead of just one, and
  some of the upgrades themselves have changed.
- **New spells.** There's a batch of new spells.
- **A smaller, sharper board.** Levels are 18-by-18, where Rift Wizard 2 was 33-by-33.
  Fights are more immediate and tactical, with fewer enemies on the board — the game
  feels quite different as a result.

## How the Mod Sounds Different from the Rift Wizard 2 Mod

Combat narration is streamlined and compressed, so a turn is quicker to take in —
related and repeated effects are gathered into tight summaries instead of being read
out one line at a time. For the full blow-by-blow, the game's own combat log still has
every line.

## Keybinds

Two sets of keys: the ones the mod adds, and Rift Wizard 3's own. A few things first:

- **Rift Wizard 3 uses `E` for crafting**, so the mod's enemy scan (was `E` in the
  Rift Wizard 2 mod) is now on **`J`**.
- On first launch the mod moves **tooltip cycling** to **Backslash** (previous) and
  **Backspace** (next) for screen-reader use, keeping PgUp/PgDn as secondary and
  unbinding Fast Forward to free Backspace. Change any of this in Options.
- Press **Shift + /** (**?**) in a level to hear the mod's full reference spoken.

### Mod keybinds

**In a level — scans and info**

| Key | Function |
|-----|----------|
| **J** | Enemy scan. Press repeatedly to cycle enemies, nearest first. Shift+J reverses. |
| **Y** | Ally scan. Cycle allies; Shift+Y reverses. |
| **N** | Spawner/nest scan. Cycle nests; Shift+N reverses. |
| **Q** | Landmark scan — rifts, shops, shrines, orbs, pickups. Cycle; Shift+Q reverses. |
| **L** | Line of sight — enemy count by type and direction. Adds speech to the game's line-of-sight overlay. |
| **T** | Threat — whether your current square is threatened, and by what. Adds speech to the game's threat-zone overlay. |
| **B** | Spatial scan — walkable distance in all 8 directions. |
| **X** | Hazard scan — clouds, webs, and other hazards. |
| **D** | Detail — full description of whatever is under the cursor. |

**In a level — status, marking, movement**

| Key | Function |
|-----|----------|
| **F** | Vitals — HP, shields, and active status effects with durations. |
| **Shift + F** | Ally overview — all allies with HP. |
| **G** | Charges — selected spell's charges, or all spells if none is selected. |
| **Alt + J / N / Q / Y** | Mark or unmark the last target from that scan. One mark at a time; clears when the unit dies or the landmark is collected. |
| **P** | Path to the look-mode cursor — full route to whatever it's on. |
| **Shift + P** | Re-announce the path to your marked target. |
| **Shift + Tab** | Previous target while targeting, walking, or looking (reverse of the game's Tab cycle). A fresh press starts from the far end of the list — in walk mode that jumps straight to the rifts. |
| **RCtrl + Arrow** | Diagonal movement (Up=NW, Right=NE, Down=SE, Left=SW), for keyboards without a numpad. |

**In a level — speech control**

| Key | Function |
|-----|----------|
| **Left Ctrl** | Cancel speech. |
| **Z** | Repeat the last message. |
| **[** / **]** | Speech history back / forward. |
| **F1** | Open the Words of Power reference (mod keybinds and tips) from any screen — level, menus, shops. |
| **Shift + /** | Open the Words of Power reference (same as F1). |

**Deploy mode (placement phase before each level)**

| Key | Function |
|-----|----------|
| **1** | Quadrant overview — enemies, spawners, loot by area. |
| **2 / 3 / 4 / 5** | Cycle memory orbs / pickups / spawners / shops, shrines, and circles. |

**Shops (spell shop and crafting)**

| Key | Function |
|-----|----------|
| **Comma** | Read the active filters and available filter keys aloud. |
| **I** | (Crafting, component selection) Re-read the item you're building. |
| **R** | (Crafting, component selection) Read recipe progress. |

The character sheet also reads your component tag bank (e.g. "Components: 3 Fire, 2 Ice,
1 Nature") when you open it with the game's **C** key.

### Rift Wizard 3 keybinds

The game's own controls, customizable in Options. Press **H** or **/** in a level for
the game's native help.

**Movement and actions (in a level)**

| Key | Function |
|-----|----------|
| **Arrows** or **Numpad 8 / 2 / 4 / 6** | Cardinal movement. |
| **Numpad 7 / 9 / 1 / 3** | Diagonal movement (the mod's RCtrl+Arrow covers keyboards without a numpad). |
| **Space** or **Numpad 5** | Pass turn / channel the current spell. |
| **W** | Walk toward a tile. |
| **A** | Auto-collect — once a level is cleared of enemies, routes the wizard to gather every remaining pickup (memory orbs, components, hearts). Inactive while hostiles remain. |

**Spells and targeting (in a level)**

| Key | Function |
|-----|----------|
| **1–0** | Select Spell 1 through 10. |
| **Shift** | Modifier (held with another key for its alternate action). |
| **Enter** / **Numpad Enter** | Confirm / cast. |
| **Escape** | Abort / back out. |
| **Tab** | Cycle to the next valid target while a spell is selected. |

**Map info and overlays (in a level)**

| Key | Function |
|-----|----------|
| **V** | Look mode — free cursor to examine tiles. |
| **.** (period) | Interact with the prop on your tile. |
| **T** | Show threat zone (the mod adds a spoken readout). |
| **L** | Show line of sight (the mod adds a spoken readout). |
| **U / I / O** | Highlight allies / enemies / objects. |
| **PageUp / PageDown** | Cycle tooltips (the mod rebinds to Backslash / Backspace). |

**Menus**

| Key | Function |
|-----|----------|
| **C** | Character sheet. |
| **S** | Spells. |
| **E** | Crafting. |
| **M** | Message log. |
| **H** or **/** | Help. |
| **R** | Reroll rifts (level-select screen). |

**In lists and shops (character sheet, spells, message log, spell shop, crafting)**

| Key | Function |
|-----|----------|
| **Arrows** | Move the selection. |
| **Enter** | Confirm / select. |
| **Escape** | Back out. |

**Shop filters (shared by the spell shop and crafting)**

| Key | Function |
|-----|----------|
| **Tab** | Cycle which filter category is active. |
| Letter keys | Toggle filters in the active category — element/school tags (Fire, Ice, Arcane…) or spell attributes (Damage, Range, Duration…). The same letter means different things per category, so use the mod's **Comma** readout to hear the live set. |
| **`** (backtick) | Toggle the "can afford" filter. |
| **Q** | Open the search box to filter by name (not yet supported by the mod — see Known Issues). |

In the crafting screen, confirming a craftable blueprint opens **component selection**,
where **Enter** adds or removes the highlighted component and the mod's **I** / **R**
keys re-read the item and recipe.

## Reporting Issues and Feedback

This is a limited beta — bug reports are the point.

- **Discord:** reach the author directly (fastest during the beta).
- **GitHub:** open an issue at
  [Words-of-Power-II issues](https://github.com/EarthboundPromoter/Words-of-Power-II/issues).

Please include what you were doing, what you heard (or didn't), and the relevant part
of `screen_reader_debug.log` from the mod folder. If the game crashed, `crash.txt` (in
the game folder) helps too.

## Known Issues and Untested Features

Expect rough edges in an early beta:

- **In-shop name search isn't supported yet.** The game's **Q** search box (type to
  filter items by name) isn't handled by the mod, so typed search isn't usable yet.
- **Lightly tested narration.** Several newer features are built but not yet heard
  across many real situations; they may not fire in every case, or may sound off when
  they do. Reports welcome.
- **Crafting depth.** The core loop works (browse, filter, pick components, confirm,
  equip), but some extras aren't in — for instance per-component keyboard browsing of
  your bank.
- **The Mods list isn't voiced yet.** Every other screen reads — the main menu,
  options, shops, character sheet, key rebinding, combat log, game-mode/trial/custom-run
  setup, **How to Play**, **Language** select, and so on. The **Mods** screen is the one
  holdout (the bundled enabler program above covers turning this mod on, so you don't
  need that screen to get running).
- **Some mouse-only base-game controls aren't wired up yet.** A few parts of Rift
  Wizard 3's interface are mouse-driven; keyboard and speech support for them is doable
  and simply isn't in yet.
- **Game updates can break things.** Rift Wizard 3 is in early access; an update can
  rename or move the internals the mod reads and silence some narration until the mod
  catches up. If something that used to talk goes quiet after a game update, that's
  likely why — please report it.

## Privacy

This mod makes no network connections and collects, transmits, or uploads nothing —
everything stays on your machine.

If you read the source you'll see a `telemetry` module: a dev-only tool for the
author's own post-run analysis, writing to local disk only. It isn't in release
downloads, so its import quietly fails and every call does nothing; even when present
it has no network code and stays inert unless manually activated.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Credits

- Rift Wizard 3 by Dylan White, Khoops, and Jacob Martinez.
- [Tolk](https://github.com/dkager/tolk) by Davy Kager.
- [NVDA](https://www.nvaccess.org/) by NV Access.
</content>
