# Words of Power: RW3

**Version 0.6.0 — beta**

A screen-reader mod for **Rift Wizard 3**: it speaks the game's state and events
through NVDA or JAWS (via Tolk) so the game can be played without sight. It's the Rift
Wizard 3 successor to *Words of Power*, the Rift Wizard 2 mod.

Early beta — core play, combat narration, and the crafting interface all work, but
some features are lightly tested. Please report anything that sounds wrong or stays
silent when it shouldn't.

**Coming from an earlier version?** [TRANSITION.md](TRANSITION.md) — the transition
guide, current through 0.6.0 — covers what moved, what changed, and what's new, with a
five-minute quick start. Useful to anyone, but written primarily for returning
players.

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
3. Launch the game. You'll hear "Words of Power version 0.6.0" if it's working. A debug
   log is written to `screen_reader_debug.log` in the mod folder.

The very first screen on launch is **Language select** (voiced, English only) — just
press **Enter** to pass it and reach the main menu.

### Turn off the Steam overlay (required for Shift + Tab)

Steam silently reserves **Shift + Tab** for its in-game overlay: the keypress is
swallowed inside the game process and Rift Wizard 3 never receives it, even though
the overlay itself never visibly opens in this game. The mod's reverse target
cycling cannot work until the overlay is off for this game:

1. In your Steam library, focus Rift Wizard 3 and open its context menu
   (Applications key or Shift + F10), then choose **Properties**.
2. On the **General** tab, uncheck **Enable the Steam Overlay while in-game**.

This is per-game — the overlay keeps working in your other games, and every other
Shift chord works regardless. Only Shift + Tab is affected.

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
  Rift Wizard 2 mod) is now on **`I`**, riding the game's Highlight Enemies key.
- On first launch the mod moves **tooltip cycling** to **Backslash** (previous) and
  **Backspace** (next) for screen-reader use, keeping PgUp/PgDn as secondary and
  unbinding Fast Forward to free Backspace. Change any of this in Options.
- Press **Shift + /** (**?**) in a level to hear the mod's full reference spoken.
- **The mouse yields to the keyboard.** While you play by keyboard, the game ignores
  where the physical mouse happens to rest: held `L` draws line of sight from your
  wizard — or from your Look/targeting cursor when one is active, which doubles as a
  visual cursor tracker — held highlight keys don't examine the tile under the
  pointer, and pointer drift can't move your cursor. Click once to hand control back
  to the mouse (that first click only wakes it); any keypress takes it back. Mouse
  motion alone never takes control, so desk bumps and screen-reader pointer routing
  are harmless. `mouse_attention_arbitration` in settings.ini turns this off.

### Mod keybinds

**In a level — scans and info**

| Key | Function |
|-----|----------|
| **I** | Enemy scan. Press repeatedly to cycle enemies, nearest first. Shift+I reverses. Same key as the game's Highlight Enemies: tap to scan, hold to light the tiles. |
| **U** | Ally scan. Cycle allies; Shift+U reverses. Rides the game's Highlight Allies key. |
| **N** | Spawner/nest scan. Cycle nests; Shift+N reverses. |
| **O** | Landmark scan — rifts, shops, shrines, orbs, pickups. Cycle; Shift+O reverses. Rides the game's Highlight Objects key. |
| **L** | Line of sight — enemy count by type and direction. Adds speech to the game's line-of-sight overlay. |
| **T** | Threat — "Threatened" or "Safe" for the square your attention is on. To hear *who*: examine an enemy and press T — "Threatens you" / "Can't hit you". Setting `threat_enumeration_legacy` restores the old count-and-names readout for now. |
| **Alt + L** | Latch the line-of-sight overlay: it stays drawn without holding the key, and every cursor step appends "in sight" / "out of sight". Latched from normal play it follows you; from Look, aiming, or deploy it watches from that frozen tile. Same chord releases; latching the other overlay replaces it. |
| **Alt + T** | Latch the threat overlay: every cursor step appends "threatened" / "clear". Examine an enemy first and Alt+T latches just that enemy's reach. F reports the current latch; `latch_visual_overlay` (default on) controls the drawn half — speech works either way. |
| **B** | Spatial scan — walkable distance in all 8 directions. |
| **X** | Hazard scan — clouds and webs. |
| **D** | Detail — full description of whatever is under the cursor. |

Every scan and query measures from wherever your attention is: you in normal play,
the cursor in Look mode, your aim while targeting, the deploy cursor while placing.

In Look mode, deploy, and pure-teleport targeting (Blink and kin — spells with no
area to aim), every scan or pin press also **parks the cursor on the result it
spoke**, so T and D answer for it immediately and Enter acts on it. Everywhere
else the cursor is your aim and scans leave it alone — J is the deliberate jump.

**In a level — status, pins, movement**

| Key | Function |
|-----|----------|
| **F** | Vitals — HP, shields, SP, and active buffs and debuffs with durations. |
| **Shift + F** | Ally overview — all allies with HP. |
| **G** | Charges — selected spell's charges, or all spells if none is selected. |
| **K** | Pin cycle — walk your pinned targets in category blocks (enemies, allies, landmarks, bookmarks), nearest first within each block. Shift+K reverses; **Ctrl+K** jumps block to block (Shift+Ctrl+K jumps back). |
| **Alt + K** | Pin or unpin the last spoken target; with nothing spoken, bookmark the tile you're on or looking at (works from the deploy cursor too — the pin carries into the level). The newest pin is the *focused* pin: it speaks a step toward it each turn, and every pin announces when it dies or is collected. Setting `pin_speak_all` (default off) speaks every pin's update each turn. |
| **Alt + I / N / O / U** | The same pin toggle, straight off a scan. |
| **P** | Path to the look-mode cursor — full route to whatever it's on. |
| **Shift + P** | Re-announce the path to the focused pin. |
| **J** | Jump the cursor to the last spoken scan or pin result. From normal play, opens Look mode on it; while aiming a spell, moves the aim (any spell — J is deliberate). Says "gone", and stays put, if the target died or was collected since it spoke. |
| **Shift + J** | Jump back to where the cursor was before the last J (a second press bounces forward again). |
| **Shift + Tab** | Previous target while targeting, walking, or looking (reverse of the game's Tab cycle). A fresh press starts from the far end of the list — in walk mode that jumps straight to the rifts. Needs the Steam overlay turned off for this game (see setup above). |
| **Two arrows together** | Diagonal movement: Up+Right = NE, Down+Left = SW, and so on. Press the pair as one gesture; a lone press still steps normally. |
| **Ctrl + direction** | Spring look: press once to open a look cursor at your feet and step it; keep holding Ctrl and every direction steps (add Shift to jump). Release Ctrl and you're back in play, cursor gone. Enter or V mid-peek stays in Look mode; a spell hotkey aims that spell where you were looking. |
| **Ctrl + Shift + direction** | Axis jump: skim the cursor along that line until what you'd hear changes — past open floor to the next unit, item, wall, or cloud. Speaks the span crossed, then the landing ("4 floor east, Imp"; "Edge" when the map ends the run). From normal play the same chord opens the spring look and jumps in one gesture. Arrow row only — with NumLock on, Windows strips Shift from numpad presses. Settings: `jump_coalesce_units` (default off) strides same-name unit clusters; `jump_count_open_space`, `jump_compass`, and `jump_landing_first` tune the receipt. |
| **Shift + Arrow** | Move the cursor 4 tiles. Speaks the landing tile plus a short "Crossed:" summary of everything skimmed past, floor included. Says "Edge" when the map edge cuts the move short. Shift + a pair does the 4-tile diagonal. |
| **Numpad** | Keep NumLock on: bare numpad moves and diagonals work. Shift+numpad 4-tile moves work only **without** NVDA running — the mod repairs a Windows keyboard legacy ("fake shift") that broke the chord for everyone including sighted players, but NVDA's keyboard hook swallows the keypress before any application can see it. Under NVDA, use the arrow gestures, which are immune. NumLock off leaves the numpad to NVDA review, as usual. |

**In a level — speech control**

| Key | Function |
|-----|----------|
| **Ctrl** | Cancel speech — either control key. |
| **Z** | Repeat the current line — the last message, or the history line the brackets moved to. |
| **[** / **]** | Speech history back / forward. |
| **F1** | Open the Words of Power reference (mod keybinds and tips) from any screen — level, menus, shops. |
| **Shift + /** | Open the Words of Power reference (same as F1). |

**Deploy mode (placement phase before each level)**

| Key | Function |
|-----|----------|
| **1** | Quadrant overview — enemies, spawners, loot by area. |
| **2 / 3 / 4 / 5** | Cycle memory orbs / pickups / spawners / shops and shrines. |
| **L / T** | Work while placing, measured from the deploy cursor. |

**Shops (spell shop and crafting)**

| Key | Function |
|-----|----------|
| **Comma** | Read the active filter page — each value, its hotkey, and whether it's on, plus the Shift-held companion category where one exists (crafting's Recipe / Bonus filters). |
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
| **Numpad 7 / 9 / 1 / 3** | Diagonal movement (arrow pairs cover keyboards without a numpad). |
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
| **Q** | Search box — type to filter by name; the mod speaks the live result count as you type. Enter keeps the filter and reads the first result, Escape clears it, Down arrow reads back the query. The shop search also matches descriptions, tags, and upgrade text. Setting `search_key_echo` (default off) speaks each typed character. |

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

- **Lightly tested narration.** Several newer features are built but not yet heard
  across many real situations; they may not fire in every case, or may sound off when
  they do. Reports welcome.
- **Crafting depth.** The core loop works (browse, filter, pick components, confirm,
  equip), but some extras aren't in — for instance per-component keyboard browsing of
  your bank.
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
