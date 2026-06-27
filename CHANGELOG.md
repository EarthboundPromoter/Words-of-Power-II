# Changelog

All notable changes to **Words of Power II** are listed here, newest first. This is
an early work-in-progress RW3 port — expect frequent changes.

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
