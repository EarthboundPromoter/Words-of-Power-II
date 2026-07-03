# Changelog

All notable changes to **Words of Power II** are listed here, newest first. This is
an early work-in-progress RW3 port — expect frequent changes.

## Unreleased

### Fixed
- Cloud arrivals no longer announce a cloud that never landed. When a new cloud tries
  to claim a tile whose current cloud refuses to be replaced, the game silently discards
  the newcomer — the arrival summary used to announce it anyway.
- Terrain changes now update who is in view. When a wall melts (or appears) while
  everyone stands still, enemies revealed or hidden by the change are re-checked
  immediately — "Fire Lizard appears, 3 north" through the same announcement that
  already speaks when something walks into sight. Before, the check only ran when a
  unit moved, so a standing enemy behind a melted wall stayed silent until something
  else stirred.

## 2026-07-03 — 0.3.0

### Fixed
- One unit is never spoken as several units. Grouped speech used to render repetition as
  multiplicity — "3 Ally Dancing Blades at (3,8), (3,8), (3,8)" for one blade hit three
  times, "2 Ally Sword of Lights" for one blade that blocked twice, "Wizard's Necrosis
  faded" three times for one expiring stack. Repeated hits now read as repetition in the
  existing grammar ("Ally Sword of Light (5,10) blocked 2 hits, 5 Physical each from
  Pinch"), and one-time facts (fades, buff applies, team flips, deaths) speak once. The
  fix covers every grouped speech path: enemy/ally action lines, shield blocks and
  gains, buff onsets and fades, kill and status sections, and equipment effect lines.
- Casts fully absorbed by an enemy's shield no longer read "No damage" — they read the
  blocked clause the game shows as the pips drop: "Cast Blood Bullet. 1 surviving:
  Boggart Assassin (12,9): Blood Bullet 10 Physical blocked by 1 shield." Also fixes AoE
  casts where one target blocked: the blocked target was dropped from speech entirely.
- Enemy channels are no longer voiced as "attack". Channel continuations carry the real
  spell name and the game's own verb ("Scuttler channeled Pinch, hit Wizard, 5
  Physical"), channel starts read "began channeling Pinch" instead of a bare "attacked.",
  and the redundant same-breath "gained Channeling" line is folded away. Player channel
  continuations get their real spell names back too.
- Self-target buff casts no longer append "No damage" ("Cast Ride Drake. Drake Rider
  applied, 10 turns."), and neither does starting a channel. A targeted spell that
  genuinely whiffs still reports it.
- A melee attack fully absorbed by a shield no longer speaks as a bare "X attacked."
  disconnected from its outcome — the blocked line ("Sword of Light blocked 5 Physical
  from Pinch, 1 shield left") is the single voice for the hit, matching the game's
  combat log.
- Clearing a realm no longer swallows the final action's narration. The winning cast's
  results now speak first, then "Level complete" — followed, for the first time, by the
  realm summary the game shows on its stats panel: turns taken, spell casts, top damage
  dealt and taken. (The summary code existed but read a stats file the game hadn't
  written yet, so it had never spoken.)
- Item pickups after a realm is cleared now speak — they were silently queued and
  dropped, every level, all along.
- Spell upgrades whose whole effect is stat changes (e.g. Lightning Bolt's Blood Horizon)
  no longer go silent after their level line — every upgrade now reads exactly what the
  game draws: stat gain lines, description, added tags, stack type.
- Upgrades that introduce new stats to their spell (e.g. Life Funnel's targets and radius)
  now read those gain lines; they were silently dropped before.
- Summon tooltips no longer read bare placeholder words where numbers belong ("damage
  Lightning damage") — ability descriptions now speak their live numbers everywhere
  (the Living Lightning Scroll bug), and the same fix covers passives, on-death effects,
  clouds, and ground items.
- Locked rifts no longer reveal their contents: like the game, they now read only the
  rift's name, the unlock hint, and (for vaults) the vault's description. Contents read
  after the level is cleared — previously the mod spoke concealed contents all level.
- Summon previews no longer read "0 of 48 HP" — they read max HP, as drawn.
- Shields granted to a unit at the moment it is summoned (e.g. Magic Minion Shield's
  "allies gain shields when summoned") are now announced — these on-summon grants used
  to be silently dropped as part of the unit arriving.
- A unit returning to the battlefield via Reincarnation no longer produces a phantom
  "shields gained" announcement.
- A shield grant that pushes a unit past the 20-shield cap now announces the true net
  gain once, instead of an inflated amount followed by a correction.
- Channeling, rituals, and other manual-only cast behavior work again with the mod loaded.

### Added
- Shields are now narrated throughout combat, for you, your allies, and enemies alike:
  gaining shields, having them stripped, and blocking hits — "Wizard blocked 12 Fire
  from Fire Bolt, 2 shields left." Blocked hits report the damage that would have
  landed. Two new settings control whether non-wizard shield lines end with the unit's
  resulting shield count: `enemy_shield_totals` (on by default) and
  `ally_shield_totals` (off by default).
- Allegiance changes are now announced: units that turn friendly (Dominate, conversions) or hostile (betrayals, Treachery).
- Cheating death is now announced: when a hit would have killed you but you survive
  (Crisis Charm restoring you to full, Soulbound / Soul Jar clamping you to 1 HP), you
  hear "You would have died —" and your resulting health (previously silent).
- Auto pickup (the game's A key) now reports one summary when the collection walk ends,
  instead of announcing every item in stride: Memory Orbs with SP gained and the new
  total, Ruby Hearts with the max-HP gain and current HP, components by name. Canceling
  the walk mid-route summarizes what was gathered so far. (Known quirk: the walk's final
  item announces itself in full right after the summary.)
- Pressing the Auto pickup key with nothing left to collect says "Nothing to pick up" —
  the game itself gives no feedback there.
- Rift previews now read each component's tags (shown in-game as colored letters) and
  distinguish the boss tiers the game shows by color: "Boss:" for encounter bosses,
  "Elite:" for named threats.
- Components now read their tags when cycled to or examined on the ground.
- Equipment now reads its tags and its Attributes stat block (live values), matching the
  drawn panel.
- Spell pages now mark already-purchased upgrades with "owned" (shown in-game in green),
  and read "Attributes: None" where the game draws it.
- Vault portals read their real names ("Spiders Lair") instead of a generic "Rift" —
  before and after unlocking.
- Enemy ability lists now include Quick Cast where present, use the game's "Spells:"
  header, and match the game's resist wording.

## 2026-06-29 — 0.2.6

### Fixed
- How to Play now always opens at the first page instead of resuming the last page
  viewed — a native open could otherwise land on the appended Words of Power section.

## 2026-06-29 — 0.2.5

### Added
- How to Play screen is now voiced, with up/down navigation by line and left/right paging.
- Words of Power reference: six pages of mod keybinds and tips, appended to the How to Play
  screen and also available standalone via F1.
- F1 opens the Words of Power reference from any screen; announced at the title screen.
- Language selection screen is now voiced (first-run picker and Options, Language).

### Changed
- **The mod-enabler tool now finds your game on its own.** `enable_screen_reader`
  (and the bundled `enable_screen_reader.exe`) used to have to sit inside your Rift
  Wizard 3 folder to work — it found your settings by looking in the folders above
  itself. It now also locates your Steam copy of the game automatically, so you can
  run it from anywhere: your Desktop, Downloads, wherever. If the game is installed
  somewhere unusual, you can still run the tool from inside the game folder, or point
  it straight at your settings with `--options <path to options2.dat>`.

### Fixed
- **When you teleport yourself, the game now tells you where you landed.** Self-teleports
  — Blink, the Teleporter, and Lightning Form's teleport-when-you-cast-a-lightning-spell —
  used to be completely silent: you'd cast Lightning Bolt and be moved across the map with
  no spoken sign you'd moved at all. Your cast now ends with "Teleported to (x,y)."
- **Being shoved or dragged is more reliably announced.** A forced relocation that moves
  you by swapping places (rather than a straight push) was silent before; it now speaks,
  named with its cause. A multi-tile pull now reports a single final destination instead of
  one line per tile crossed.
- **Character sheet no longer leaks stray "True"/"False" or reads the wrong thing.** The
  post-purchase character-sheet summary is only spoken when you're actually on the sheet,
  the confirm prompt is read through the normal text cleaner, and a selection with nothing
  describable now says "Nothing selected" instead of leaking a raw value.
- **A depleted spell no longer reads out as if it's ready to cast.** Selecting a spell with
  no charges left used to announce its range and shape like any castable spell (the game
  still lets you start aiming it); it now just says "{name}, depleted" so you know at once
  not to bother. Reviewing the spell's full detail in the character sheet is unchanged.
- How to Play and Language screens no longer announce a state number instead of their name.
- The threat check on an examined enemy now says "can't hit you" instead of "can't reach you".

## 2026-06-29 — 0.2.0

A large rewrite of how combat is narrated. The enemy turn is now spoken as a single,
coherent report instead of a flat stream, and your gear finally talks. The new
narration pipeline is now the sole combat voice (the old line-by-line path is retired
for combat).

### Added
- **The enemy/ambient turn is now narrated coherently, in priority order.** What
  enemies and allies do on their turn is composed into one report:
  - **Nearest threats first.** Lines are ordered by distance from you, and everything
    out of your line of sight is grouped at the end behind a single "Out of sight" cue,
    so you can stop listening once the close, visible action is covered.
  - **Enemy and ally actions** — casts and attacks — with who acted, on whom, for how
    much, of what type.
  - **Deaths ride the blow that caused them** — "Aelf cast Lightning Bolt at Goblin,
    6 Lightning, killed" — instead of a separate, disconnected "killed" line.
  - **Enemy summons are announced.** "Goblin Spawner cast Summon Goblin. 1 Goblin
    spawned at (2,4)." Big waves report a count and direction ("7 Bats spawned, 5 north,
    2 southeast") instead of a wall of coordinates.
  - **Cloud damage on units away from you** (storm/blizzard clouds hitting enemies or
    allies off your tile) is now spoken.
  - **Enemy debuffs on allies and enemy self-buffs** are announced when they land.
- **Your gear now talks.** Equipment that acts each turn — damage auras, healing auras,
  status-applying gear (Stone Mask, etc.), and sub-casting items (Explosive Spore
  Manual) — is narrated in its own slot, right after your action and before the enemy
  turn. *Freshly enabled — please report anything that sounds off or repetitive.*

### Changed
- **Damage numbers now match the game.** Overkill is reported as the damage actually
  dealt, not the spell's full value — a 7-damage hit on a 5-HP enemy reads "5",
  exactly like the game's own combat log. Resistance and vulnerability are reflected
  as before.

### Fixed
- **No more phantom announcements when you enter a level.** The level's enemy roster
  and your re-applied equipment are no longer narrated as if they had just happened on
  your first turn.
- **Fixed silent gaps in busy fights.** A crash in the new narration (triggered on
  many debuff-cast turns) was quietly dropping the entire enemy-turn report; it now
  composes reliably.
- **No more phantom "(9,9)" tile** spoken when you check Threat or Space during deploy.
- **Clearer attribution of what hits you** — the source and caster of damage and
  debuffs on you are named more consistently.

### Known issues
- Gear that acts every turn (a persistent aura) currently repeats its line each turn;
  a quieter cadence is planned.
- A few gear effects the game itself never signals — gaining shields each turn, or
  creating clouds — aren't spoken yet.

## 2026-06-28

### Added
- **Full README** for the beta: game description, install (zip and clone/pull),
  what's new in Rift Wizard 3 vs. Rift Wizard 2, how the narration differs, a complete
  keybind reference (mod keys and the game's own, split by screen), reporting/feedback
  channels, known issues, and privacy/credits.

### Removed
- **Bundled `accessible_output2` library** (and its driver DLLs) deleted — the mod
  speaks through Tolk with a direct-NVDA fallback and never imported it.
- **`telemetry.py` removed from distribution.** It was a dev-only, local-disk analysis
  tool with no network code; it is no longer shipped, so the privacy notice that it
  isn't in release downloads is now accurate.

## 2026-06-27

### Added
- **Buff and debuff refreshes, stacks, and lingering control are now spoken.**
  - Stun, freeze, petrify, and silence on you now count down each turn ("Still
    stunned, 2 turns left") until they lift, so you always know when you'll act again.
  - Re-applying a debuff to enemies (or a buff to allies) that *extends* its duration
    reads as its own "extended to N turns" group, distinct from fresh applications;
    re-casting one of your own buffs reads "extended" rather than a second "applied."
  - A debuff that lowers your resistance (e.g. Melted Armor as it stacks) reports the
    new effective value — "Physical resistance now -30%" — when it deepens.
  - Stacked damage-over-time (e.g. several Bleed stacks) sums to its true per-turn
    total instead of repeating identical ticks.
  - Non-stacking re-applications no longer chatter — a debuff is announced once and
    on meaningful escalation, not every turn an aura re-applies it.
  - *Freshly built and unit-tested; not yet heard across many live situations — please
    report anything that sounds off or doesn't fire when it should.*
- **"Catwalk" terrain label for bridges over chasms.** A corridor with chasm on *both*
  sides — a walkway over the abyss — now reads as "catwalk" instead of "corridor," so
  you know there are drops to either side. Tracks your actual position, so a passage
  that shifts between walled and open stretches relabels as you move; any flank that's
  solid wall or a map edge keeps it a plain "corridor."

### Changed
- Version set to **0.1.0** — this Rift Wizard 3 port is its own mod, restarting the
  numbering rather than continuing the Rift Wizard 2 line's count.

### Fixed
- **Deploy quadrant overview now reports the correct quadrants.** The map-center
  used to label northeast/southeast/southwest/northwest was still set for Rift
  Wizard 2's larger 33x33 grid, so on RW3's 18x18 board nearly everything was
  announced as "northwest." The center is now taken from the level's own size, so
  entities are spread across the right quadrants.
- **Rifts no longer clutter the deploy overview.** They were being listed per-rift
  even though they're inert until the level is cleared; they're now omitted.
- **Buff pop-up tooltips now read their full effect.** When cycling a spell's
  tooltips (e.g. on Healing Light), the buffs that pop up were reading as a bare
  name because their effect text lives in different places depending on the buff:
  **Ritual of Rejuvenation**'s regen buff ("Heals 5 HP each turn"), **Clarity** (its
  effect lives in a tooltip override), and resist buffs like **Lightning Immunity**
  ("100% Lightning resist" lives in resist data, with no description at all). All now
  read their resist lines and effect text, matching the on-screen panel — the whole
  class of upgrade buffs, not just these three.
- **Deploy and `Q` now find memory orbs, floor components, and shrines.** Prop
  detection had been keyed to Rift Wizard 2 names: memory orbs were silent, floor
  crafting-components were never listed, and the standalone reward shrines (Spiders,
  Necromancy, Perfection) didn't announce. Unknown props now fall back to their game
  name so future objects surface automatically. Ruby Hearts also read the correct
  "+25 max HP" (was a stale "+10").
- Monster tooltips whose passive abilities use RW3's templated `(text, values)` form
  (e.g. Green Mushboom) no longer crash the readout — they had been producing no
  speech at all.
- Rift-reward components wrapped as pickups (e.g. **Flame Blade Fragment**, a rare
  on-craft component) now read their full description instead of just the name.

## 2026-06-26 — Initial public release

### Added
- First Rift Wizard 3 port of the *Words of Power* screen-reader mod.
- **Equipment-crafting / component narration:** blueprint list (craftable / owned /
  needed-tags + recipe), component-selection toggling with running diffs, and the
  `I` (item) / `R` (recipe progress) review keys.
- **Shop-filter convention:** `Tab` cycles the filter category, `comma` reads the
  full filter list, with reactive on/off + result-count announcements.
- **Component tag-bank readout** on the character sheet.
- Prebuilt `enable_screen_reader.exe` (one-file, no Python needed).

### Fixed
- Spell-attack damage now speaks: registered the `EventOnPreDamaged` trigger that
  RW3 requires before it will emit post-resist damage (previously every attack read
  "no damage").
- Character-sheet down-navigation no longer crashes (RW3 added a `column` argument to
  `adjust_char_sheet_selection`).
- Equipment slot names corrected for RW3 (Trinket / Helmet / Armor / Boots / Weapon).
