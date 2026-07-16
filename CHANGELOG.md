# Changelog

All notable changes to **Words of Power II** are listed here, newest first. This is
an early work-in-progress RW3 port — expect frequent changes.

## Unreleased

### Fixed
- Crowded turns no longer stall speech for minutes: ambient narration gathers
  its chains through the shared index. What you hear is unchanged.

### Changed
- Internal: combat narration reads records through one shared index. No speech
  changes; composition cost no longer grows with time spent on a level.

## 2026-07-14 — 0.6.3 — Pathfinder

### Added
- Blood Bullet says "Obstructed by X" when the shot won't reach the aim — the
  first unit in the path, allies included. Blessed Blood's pass-through is respected.
- Continue Run greets you with a briefing: realm, HP, shields, SP, active
  statuses, adjacent enemies with directions, ally count, and what remains.
- Shop and crafting screens name still-active filters when they open —
  filters survive menu round trips, so a shortened list now says why.
- `P` with no cursor up speaks the path to the last spoken scan result — no
  pin set, no cursor moved. Scan, hear a target, P for the route, keep scanning.
- `P` with a cursor up names what it pathed to: "path to cursor" in Look mode,
  "path to aim" while aiming a spell, "path to destination" for teleports.
- One word per referent: scanned things are targets, Look's cursor is the
  cursor, a spell's is the aim — scans mid-aim say "From aim", not "From target".
- `J` joined the spoken keybind reference (Shift+/), including that it moves
  your aim while a spell is up.
- TUTORIAL.md: a new-player guide to the game and the mod, from first scan to
  first deploy. Linked at the top of the README.

### Fixed
- Summoned allies whose time runs out are "expired", not "died" — a kill
  the game never showed.
- Enter while in Look mode says "Cancelled" (it closes the cursor, same as
  Escape) instead of the false "look: can't target self".
- Item pickups and on-death warnings speak markup as words ("50 HP", not
  "bracket 50 HP colon heal bracket").
- Minion self-heals are tagged Ally, matching their spawn announcements.
- Deploy-screen tiles say the coordinate right after the unit's name, not
  glued to its last ability.
- `P` no longer says "already at target" after leaving Look mode or casting
  without moving.

## 2026-07-11 — 0.6.0 — Good Looking

### Added
- Death line names the killer — direct hit, lingering effect with its source,
  or your own spell. The fatal turn's events now narrate at the death screen.
- Game-over screen announces its choice: the message-log key opens the combat
  log, any other key continues to the slideshow. Speech-review keys (`Z`,
  `[`, `]`) are safe there.
- Save Preferences and Mods menus speak: toggle states, mod positions,
  enable/disable and load-order moves (Shift+Up/Down), and the upload picker.
- Combat log lines speak as words, not raw color markup, and each unit is
  tagged ally or enemy as its color shows (a berserk ally announces as enemy).
- Spring look: hold `Ctrl+direction` to step a temporary look cursor (add
  Shift to jump); release Ctrl to return. Enter or V mid-peek keeps Look
  mode; a spell hotkey aims there.
- Axis jumps speak the span they crossed: "4 floor east, Imp". New settings:
  `jump_count_open_space` (default false), `jump_compass` (true),
  `jump_landing_first` (false).
- Blocked cursor moves at the map boundary say "Edge" on every fresh press —
  single steps included — in look, targeting, and deploy.
- Shop and combat-log search boxes speak: `Q` focuses, the result count
  speaks live as you type, `Enter` keeps the filter, `Escape` clears it,
  `Down arrow` reads back the query.
- New setting `search_key_echo` (default false): speak each character typed
  into a search box.
- Frame heartbeat probe (diagnostic): the debug log records frame gaps over
  50ms, a per-minute summary, and garbage-collector pauses. Setting
  `frame_probe_enabled` (default false).
- Object census (diagnostic): every five minutes the debug log records object
  counts and growth, briefly stalling the game. Setting `frame_probe_census`
  (default false).
- New setting `deploy_scan_routing` (default true): set false to keep the
  deploy cursor parked while scanning; `J` jumps to the last spoken result,
  `Shift+J` returns.
- New setting `key_trace_enabled` (default false): logs key timing and
  diagonal-chord decisions to the local debug log, for diagnosing control
  bugs.

### Changed
- **Spell listings: metadata reordered for the unrefined.** Name first,
  then cost, then everything else, on every shop screen. The four parts are
  independently composable now; configs for the cultivated will follow in
  due course. This one's for you, Chaosbringer216 and company.
- The axis jump is now `Ctrl+Shift+direction`; bare `Ctrl+direction` is the
  spring-look step. Numpad jump chords are unsupported — NumLock strips Shift
  from numpad presses.
- Jump receipts speak before the landing (`jump_landing_first` restores the
  old order); Shift+move landings name the tile's contents, not the full
  read. D on arrival for detail.
- Latched threat overlay tokens say "safe" instead of "clear", matching the
  T query's "Threatened"/"Safe".
- Tooltip page counter ("2 of 5") moved from the start of each page to the
  end (player feedback).

### Fixed
- Ally attack range now speaks, as reach not threat: examine + `T` answers
  "You're in its reach"; `Alt+T` latches with reach wording. Berserk or
  charmed allies flip to threat wording.
- Grouped bonus readouts now say "spells and equipment gain…", matching the
  game's text — these bonuses apply to gear too.
- Multi-line descriptions (spells, upgrades, equipment) now pause at line
  breaks everywhere they're spoken, instead of running together.
- Keeping a search with Enter now reads the first result after the count; it
  was previously never spoken.
- The combat log line reader now follows the filtered view while a search is
  active.
- Examine + `T` on a hostile now answers for your square ("Threatens
  you"/"Can't hit you"); it was testing the enemy's own tile (field report
  2026-07-08, yujin0986).

## 2026-07-07 — 0.5.1 — Hotfix: the mouse yields to the keyboard

### Fixed
- **The mouse no longer hijacks overlays and examine during keyboard play.** The
  game quietly used the physical mouse position as its attention point: held `L`
  drew line of sight from the tile under the pointer (wherever it happened to
  rest), held highlight keys examined that tile every frame — narrowing the threat
  overlay to whatever unit the mouse sat on, with no keyboard way out — and any
  pointer drift moved the Look cursor. Now whichever device acted last owns the
  attention point: any keypress mutes the mouse; a mouse click or wheel wakes it
  (the waking click only wakes — the next one acts). Mouse motion alone never
  takes control, so desk bumps and screen-reader pointer routing can't steal it.
  With the keyboard in charge, held `L` draws from the deploy target, your
  Look/targeting cursor (a handy visual cursor tracker), or your wizard. New
  setting `mouse_attention_arbitration` (default true); set false for fully
  native mouse behavior.

## 2026-07-07 — 0.5.0 — The Cursor Update

Returning players: [TRANSITION.md](TRANSITION.md) walks these changes in
learning order, quick start first.

### Added
- **Shift + Tab reverse target cycling** while targeting, walking, or looking —
  the game's Tab cycle stepped backward, same voice and "N of M" counter. A fresh
  press starts from the far end of the list, so in walk mode it jumps straight to
  the rifts. Requested by Neurrone.
- **Setup step required:** Steam silently reserves Shift + Tab for its in-game
  overlay and the game never receives the chord — even though the overlay never
  visibly opens in Rift Wizard 3. Disable the overlay for this game (Rift Wizard 3
  Properties, General tab, uncheck "Enable the Steam Overlay while in-game") or
  reverse cycling cannot work. Every other Shift chord is unaffected. Steps in
  the README.
- New setting `speak_pickup_effects` (default true): set false to trim Ruby Heart
  and Memory Orb cursor reads to name-only. Walk-on shrines always speak their
  effect.
- **Two-arrow diagonal chording.** Press two orthogonal arrows as one gesture —
  Up+Right is northeast — for a single diagonal step; add Shift for the 4-tile
  diagonal. No modifier, both hands or one, and it follows your movement
  rebinds. A lone press still steps normally (it waits one imperceptible frame
  for a partner). Held arrows keep the game's native auto-walk untouched.
- **Ctrl + direction: the axis jump.** Skim the cursor along a line until what
  you'd hear changes — across open floor to the next unit, item, wall, or
  cloud, in one keypress. Speaks the landing tile, then the distance
  ("6 east"; "Edge" when the map ends the run). Either Ctrl, and it composes
  with everything: Ctrl+arrow straight, Ctrl+pair diagonal, Ctrl+numpad free.
  By default the jump stops at every unit; the new `jump_coalesce_units`
  setting (default off) strides same-name clusters instead. Cursor modes only
  — your wizard never jumps.
- **Pins: the mark grows into a pin list on K.** Alt+K pins or unpins the
  last spoken target — or, with nothing spoken, bookmarks the tile you're on
  or looking at (the deploy cursor works too, and the pin carries into the
  level). K cycles your pins in category blocks — enemies, allies, landmarks,
  bookmarks — nearest first within each block, with a count header on a fresh
  cycle; Shift+K reverses and Ctrl+K jumps block to block. Alt+K on a
  just-cycled pin unpins it. Pins live per level, follow their unit, and
  announce deaths and collected landmarks. The newest or last-cycled pin is
  the *focused* pin: it carries the per-turn guidance line ("North to Wolf,
  12 HP."), and Shift+P re-announces its full path. New setting
  `pin_speak_all` (default off) speaks every pin's update line each turn.
- **Scans route the cursor.** In Look mode, deploy, and pure-teleport
  targeting (Blink and kin — Translocation spells with no area to aim), every
  scan and pin-cycle press also parks the cursor on the result it just spoke:
  the scan line is the announcement, T and D answer for that tile
  immediately, and Enter acts on it — the missing mouse-flick. A cycle keeps
  measuring from where it started, so repeated presses walk outward instead
  of chasing the cursor. While aiming any other spell the cursor is your
  tuned aim and scans leave it alone.
- **J: jump to the last spoken result.** From normal play, J opens Look mode
  right on the last thing a scan or pin cycle spoke; while aiming, J moves
  the aim there deliberately (any spell). If it died or was collected since
  it spoke, J says "gone" and stays put — it never asserts stale truth.
  Shift+J bounces back to where the cursor was before the jump. J and Y had
  been freed by the scan-key move; J is now the bridge.

- **Overlay latches: Alt+L and Alt+T.** The game's hold-to-see overlays,
  pinned: latch line of sight or threat and it stays active — the overlay
  keeps drawing (like holding the key), and every cursor step gains a short
  tag: "in sight" / "out of sight", "threatened" / "clear". Latched from
  normal play, line of sight follows you as you move; latched from Look,
  aiming, or deploy, it watches from that tile — a deploy latch carries
  into the level with you. Examine an enemy first and Alt+T latches just
  that enemy's reach ("Latched: Night Hag's threat"). One latch at a time,
  same chord releases, F reports what's latched, and holding the real key
  still works and takes over while held. New setting `latch_visual_overlay`
  (default on) controls the drawn half; the spoken tags work either way.

### Changed
- **The global threat query now answers "Threatened" or "Safe" — nothing
  more.** The old readout listed every threatening enemy with counts and
  directions, but the game itself shows threat as an anonymous red zone and
  gates *who threatens* behind examining each enemy one at a time. The old
  report assembled a tactical summary no sighted player ever got in one
  glance — you were overserved — and computing it was the freeze players
  felt pressing T on crowded levels. The new answer reads the game's own
  threat zone (built once per turn, shared with the held-T overlay), so it's
  instant after the first press of a turn. Who-threatens is unchanged where
  it always lived: examine an enemy and press T ("Threatens you" / "Can't
  hit you") — with scans now parking the cursor, that loop is three
  keypresses. The old readout survives verbatim under a new setting,
  `threat_enumeration_legacy` (default off), as a time capsule: it will be
  removed in a future release, so if you rely on it, say so.
- **The "From destination" scan qualifier now follows the routing rule** —
  spoken only for teleports whose target is truly a destination. It used to
  key on the bare Translocation tag, which mislabeled Disperse's area aim as
  a destination.
- **Alt+scan marking is now the pin toggle.** The one-mark-at-a-time limit is
  gone; Alt + I / N / O / U pin without replacing what you marked before, and
  scan lines say "pinned" where they said "marked". If you relied on marking
  to auto-replace, unpin with a second Alt+K on the old target.
- **Right Ctrl no longer means diagonal — Ctrl now means one thing, either
  side.** The RCtrl+arrow diagonal and its AltGr synonym are retired, replaced
  by two-arrow chording; the mod never distinguishes left from right Ctrl
  again. Bare Ctrl (either one) cancels speech; Ctrl chords are the axis jump
  (see Added). Hard switch, no legacy setting.
- **Shift + arrow now speaks the landing tile plus a short "Crossed:" summary**
  of everything the cursor skimmed past — units, props, clouds, walls, chasms,
  and floor, grouped with counts ("Crossed: 2 floors, web") — instead of
  reading all four tiles in sequence. Floor counts so the distance never needs
  arithmetic. A move stopped short by the map edge appends "Edge"; a move
  pinned at the edge says "Edge" instead of nothing. Applies in Look,
  targeting, walking, and deploy, straight or diagonal, arrows or numpad.
- **The scan keys moved onto the game's own highlight keys** — the key that
  lights a category on screen is now the key that speaks it. Enemy scan: J is
  now **I** (Highlight Enemies). Ally scan: Y is now **U** (Highlight Allies).
  Landmark scan: Q is now **O** (Highlight Objects). Tap to scan, hold to show
  the tiles; the pairing follows your rebinds — rebind Highlight Enemies and
  the enemy scan moves with it. N (spawners) and X (hazards) are unchanged;
  they have no game highlight. The Alt+key toggle moved with the scans
  (Alt + I / N / O / U). Q and Y are now unbound, reserved for upcoming
  features; J was given its new job as the cursor jump (see Added). This is
  a hard switch with no legacy-keys setting — the old letters simply stop
  answering.

### Fixed
- **Shift + numpad now actually moves 4 tiles — when NVDA isn't running.** A
  Windows keyboard legacy ("fake shift") strips Shift from NumLock-on numpad
  presses at the driver level, below every application — so the game's own
  Shift+numpad move silently stepped one tile, for every player, screen
  reader or none. The mod now recognizes the stripped chord's signature and
  performs the 4-tile move itself, with the same landing-plus-crossed voice.
  KNOWN LIMIT, verified by event capture: with NVDA running, NVDA's keyboard
  hook consumes the numpad keypress outright and nothing reaches the game to
  repair — use the arrow gestures (Shift+arrow, Shift+pair), which are
  immune. NumLock stays on either way; NVDA's NumLock-off review keys are
  untouched.
- **Ctrl speech cancel actually works now — and either Ctrl does it.** A
  modifier guard swallowed the press before the cancel branch could ever run —
  dead code since it shipped, masked by NVDA's own control-interrupt: the
  synth went quiet, but the mod's queued lines kept arriving afterward. Cancel
  now also clears the mod's speech queue and pending HP announcements, and
  scan cycling survives a mid-cycle cancel.
- Walk-on props (Ruby Heart, Memory Orb, and the three walk-on shrines) now read
  their description in Look mode and the targeting brief, matching the game's
  examine panel, which shows it on mere cursor-over. Reported by Neurrone.
- Units no longer hide the prop or cloud beneath them: the targeting brief, the
  Tab-cycle read, and Look mode's portal branch all spoke the unit and dropped
  what it stood on. All three now speak what's beneath, threat-first. Reported
  by Neurrone.

## 2026-07-05 — 0.3.4

### Fixed
- Shrine tooltips at rift selection now read the shrine's description instead of
  its name alone. Stocked shops read their item list, matching the game's own
  examine panel.

### Changed
- Grouped bonus lines under one prefix per source: "Blood spells gain 50% Minion
  Health, 50% Damage, 1 Max Charges, 2 Range, 3 Minion Damage" instead of five
  separate "Blood spells gain..." sentences. Applies everywhere bonuses are read
  (crafting, character sheet, shops, tooltips); no bonus is dropped.

## 2026-07-05 — 0.3.3

### Fixed
- Rerouted the crafting screen's item reading through the shared examine
  describer, thus restoring the "Attributes:" stat block that the crafting
  reader never spoke — gear whose numbers live only there (Ghost Slippers'
  minion health and damage, for example) was silent about them.

## 2026-07-05 — 0.3.2

### Added
- Spell targeting now follows the game's own footprint. The "Within AoE" census reads
  from the same engine source that paints the blue tiles, so every true area spell
  reports — beams, cones, and linked-group spells like Mass Melt's chain, which the old
  radius-and-keyword guess missed entirely. Single-tile spells (Blink and friends) no
  longer produce false "Within AoE" warnings. The census lists allies before enemies,
  so a friendly about to be caught in the blast is the first thing heard.
- Linked-group spells name their chain: after the targeted unit, who else is connected
  — "Orc (7,4). 2 Goblins." — allies first. New setting `aoe_group_names` (default on);
  off falls back to the plain count phrasing.
- Invalid targets speak their reason at the cursor: "No line of sight.", "No target.",
  "Tile occupied." — the same reasons previously heard only after a failed cast
  attempt. "Out of range." now comes from the game's own range circle for every spell.
- Look mode names your own auras covering the examined tile: "In your Fire Aura."
- Pressing Tab with no valid targets says "No targets" instead of a spurious reason.
- Canceling an auto-pickup walk now says "Auto-pickup stopped." before the partial
  summary, so a canceled walk no longer sounds identical to a completed one. Movement
  bumps during the walk stay silent — the summary is the feedback.
- Performance sentinel: when resolving a turn (your keypress to the turn's speech)
  takes 100ms or more, the log records "[Perf] turn N resolved in Xms, M units" — so a
  session that feels slow documents itself and a bug report carries the evidence.
  Tunable via the new `perf_log_threshold_ms` setting (0 logs every turn; the mod's
  own overhead per turn is microseconds).
- New setting `debug_log` (off / standard / verbose, default standard) controls how
  much the mod writes to screen_reader_debug.log. Standard records what you did and
  what was spoken, plus startup and errors — enough for most problem reports. Verbose
  restores the full per-event internals (multi-megabyte files on long sessions; set it
  when asked to help diagnose a bug). Off keeps only startup and errors. Every spoken
  utterance now also logs as a "[Spoke]" line, so a standard log always shows exactly
  what was heard.

- The debug log no longer echoes to the game's console window by default. The
  console renders each line synchronously, and on busy turns that echo was
  measurably delaying the turn's speech — the log file itself is unchanged and
  still records everything. New setting `console_echo` (default false) restores
  the live console view; error lines always echo regardless.
- The mod now caps its logs folder (mods/screen_reader/logs) at 25 MB: each launch
  deletes the oldest archived debug logs over the cap, newest always kept. Logs that
  piled up before 0.3.2 count toward the cap, so expect a large folder to shrink on
  first launch — this is a good moment to clear the folder out and start fresh; copy
  anything you want to keep somewhere else before upgrading.
- Rift previews now mark a component reward as such — "Component: Flame Blade
  Fragment (Fire, Sorcery)" — matching the cyan name the game draws for reward
  components, the same way "Boss:" and "Elite:" already voice the boss-tier colors.

### Changed
- The composer speech pipeline (direct-action digest, crisis lines, ambient enemy-turn
  narration, equipment narration) is now the default speech engine. 0.3.1 installs were
  already running it; 0.3.2 makes it the default.

### Fixed
- 0.3.1's settings.ini enabled the mod's internal diagnostics for all players: per-event
  disk writes (journal_debug.log) plus capture instruments whose cost climbs steeply in
  crowded fights. The result was heavy slowdown, worst in swarm fights. settings.ini is
  no longer distributed — the mod generates one with correct defaults on first run — and
  the five diagnostic flags (journal_log_enabled, log_capture_enabled,
  container_diff_enabled, cause_markers_enabled, reactive_markers_enabled) now default
  to off.
- Upgrading from 0.3.1: the fix applies itself — on first launch, 0.3.2 turns the five
  diagnostic flags off in your existing settings.ini, once, in place. Comments and every
  other setting you may have customized are untouched, and the file keeps working exactly
  as before for future edits. No manual steps needed.
- The final auto-pickup item now lands inside the batch summary instead of announcing
  itself separately afterward — walk-end detection is now exact.
- Memory Orb pickups say "4 SP total," matching the summary's wording.
- Aiming at your own tile with a spell that can't target you no longer buries the
  tile's contents under "Can't target self." — the tile (you) speaks first; the
  reason still speaks if you actually press confirm there. Reasons that depend on
  the tile's state, like "Tile occupied.", are unchanged.
- The capture layer's last known gap is closed: in-place monster transforms (the
  Mind Maggot growing wings, gaining a new name and attack) and boss debuff-immunity
  flips (the Snow Queen's Diamond Aegis) are now recorded internally with their
  causes — groundwork for transform announcements in a later release.

## 2026-07-03 — 0.3.1

### Added
- The world-state capture layer is complete: terrain changes, tile flavor (lava, swamp,
  water chasms and wall skins), cloud lifecycles, props and rifts, and the Chronomancer
  trial clock are now all recorded internally with their causes. This is groundwork — the
  speech that reads from it comes in later releases; the fixes below are its first fruit.

### Fixed
- "X appears" announcements are back — they had been silent on every normally-entered
  level since the RW3 port began. The enemy-enters-view tracker only armed itself when a
  level already contained the wizard at load time, which is true after loading a save and
  false on every ordinary rift transition (the game builds the level first and places you
  second). The tracker now arms the moment the wizard lands, so enemies walking into view
  — and enemies revealed by melting walls — announce on fresh levels too.
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
