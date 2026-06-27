# Changelog

All notable changes to **Words of Power II** are listed here, newest first. This is
an early work-in-progress RW3 port — expect frequent changes.

## 2026-06-27

### Fixed
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
