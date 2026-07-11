# Words of Power — Pure helper functions
# No game imports, no TTS, no global state.
# These can be tested independently of the running game.

import math
import re
from collections import Counter


# ---- Group-member dedup (one unit must never speak as N units) ----


def dedupe_unit_members(members):
    """Collapse a per-RECORD member list to per-UNIT: [(snapshot, count)]
    in first-appearance order.

    Group-render sites collect target snapshots one per record, so a unit
    that produced N same-signature records in one window used to read as
    N units at one coordinate ("3 Ally Dancing Blades at (3,8), (3,8),
    (3,8)" for one blade hit three times — the 2026-07-02 run-review
    multiplicity specimens). Renderers use the returned counts to speak
    repetition ("2 hits, 5 Physical each") or to speak once for
    categorical facts (fades, buff applies).

    Snapshots without an id never merge (each stays its own entry with
    count 1) — false merging would be as wrong as false multiplicity."""
    out = []
    index = {}
    for m in members:
        key = (m or {}).get('id')
        if key is not None and key in index:
            out[index[key]][1] += 1
            continue
        if key is not None:
            index[key] = len(out)
        out.append([m, 1])
    return [(m, c) for m, c in out]


# ---- Damage / resistance outcome classifier ----


def classify_resist_outcome(damage_pre, damage_post, target_resist_pct=None):
    """Classify a damage event's resistance outcome for rendering.

    Returns one of:
    - 'immune': target's effective resistance is >= 100 (hard-cap per
      Level.py:4173). No damage taken; render "X immune."
    - 'resisted': resistance < 100 but damage rounded to 0 (post=0,
      pre>0). Render "X resisted." Listener distinguishes from immune.
    - 'vulnerable': post > pre (negative resistance amplifies damage).
    - 'partial': post < pre but post > 0 (some damage absorbed).
    - 'normal': post == pre (no resistance modifier applied).

    Used by composers wherever a damage outcome gets rendered, so the
    word "immune" surfaces consistently across digest, equipment, and
    orphan producers when applicable. RW2 caps resistance at 100 before
    the multiplier (no heal-from-overresist behavior; the dev comment
    at Level.py:4172 explicitly notes the cap prevents "shenanigans"),
    so >= 100 is the immunity threshold. The journal's
    _payload_pre_damaged captures target_resist_pct post-cap so this
    classifier can dispatch reliably.
    """
    if damage_pre is None or damage_post is None:
        return 'normal'
    if target_resist_pct is not None and target_resist_pct >= 100:
        return 'immune'
    if damage_post == 0 and damage_pre > 0:
        return 'resisted'
    if damage_post > damage_pre:
        return 'vulnerable'
    if damage_post < damage_pre:
        return 'partial'
    return 'normal'


# ---- Source attribution (game combat-log convention) ----

# Buff-type integers mirror Level.BUFF_TYPE_* (Level.py:1024-1028). Kept as
# local constants so this module stays import-pure (no Level dependency,
# testable outside the game). Stable game constants.
_BUFF_TYPE_BLESS = 1
_BUFF_TYPE_CURSE = 2


def source_attributed_line(kind, *, amount, dtype, target_name,
                           source_name, source_owner_name,
                           source_is_buff, source_buff_type):
    """Render one damage/heal line the way RW3's own combat log attributes
    it (Level.deal_damage, Level.py:4064-4072).

    The game's branch: an effect reads ACTIVELY — "{owner} deals N dtype to
    {target} with {source}" — when the source has an owner AND is not a
    BLESS/CURSE buff; otherwise PASSIVELY — "{target} took N dtype from
    {source}". Equipment (buff_type ITEM) is NOT a temp buff, so a gear hit
    reads actively with the wizard as owner and the item named as the
    instrument; a spell reads actively with its caster as owner; a DOT
    (bless/curse buff source) reads passively, named by the buff.

    Self-collapse: when the owner and target are the same unit (self-heal,
    self-hit), the active form would read "Wizard ... to Wizard", so we fall
    back to the passive form ("Wizard healed N from {source}"). Compared by
    name — adequate for our sites (the only self case is the wizard); a
    rare same-name enemy-on-enemy pair would collapse cosmetically, which is
    acceptable.

    `kind` is 'damage' or 'heal'. Returns the sentence BODY with no trailing
    period — callers punctuate per their section style. All inputs are
    primitives from the journal payload (source_owner_name / source_is_buff /
    source_buff_type land via journal._source_attribution); pure and
    Level-free for testing. The word "damage" is intentionally omitted
    (house style; owner-approved)."""
    src = source_name or "unknown"
    is_temp_buff = bool(source_is_buff) and source_buff_type in (
        _BUFF_TYPE_BLESS, _BUFF_TYPE_CURSE
    )
    active = (
        bool(source_owner_name)
        and not is_temp_buff
        and source_owner_name != target_name
    )
    dtype_str = f" {dtype}" if dtype else ""
    if kind == 'heal':
        if active:
            return f"{source_owner_name} heals {target_name} for {amount} with {src}"
        return f"{target_name} healed {amount} from {src}"
    if active:
        return f"{source_owner_name} deals {amount}{dtype_str} to {target_name} with {src}"
    return f"{target_name} took {amount}{dtype_str} from {src}"


# ---- Direction & Spatial Helpers ----

def _cardinal_direction(dx, dy):
    """Convert dx, dy offset to cardinal direction string. Screen coords: y+ = south."""
    if dx == 0 and dy == 0:
        return ""
    angle = math.atan2(-dy, dx)
    degrees = math.degrees(angle) % 360
    directions = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"]
    index = round(degrees / 45) % 8
    return directions[index]

# Maps atan2-based index (E=0,NE=1,N=2,...) to clockwise-from-north (N=0,NE=1,E=2,...)
_ATAN_TO_CW = [2, 1, 0, 7, 6, 5, 4, 3]

def _bearing_index(dx, dy):
    """Convert (dx, dy) to 8-way compass index (0=N, 1=NE, 2=E, ... 7=NW).
    Returns None if dx == dy == 0."""
    if dx == 0 and dy == 0:
        return None
    angle = math.atan2(-dy, dx)
    degrees = math.degrees(angle) % 360
    return _ATAN_TO_CW[round(degrees / 45) % 8]

def _direction_offset(dx, dy):
    """Exact directional offset for wayfinding. Screen coords: x+ = east, y+ = south.
    Examples: '5 south', '3 southeast', '5 south 3 east', 'here'."""
    if dx == 0 and dy == 0:
        return "here"
    adx, ady = abs(dx), abs(dy)
    ew = "east" if dx > 0 else "west" if dx < 0 else ""
    ns = "south" if dy > 0 else "north" if dy < 0 else ""
    if dx == 0:
        return f"{ady} {ns}"
    if dy == 0:
        return f"{adx} {ew}"
    if adx == ady:
        return f"{adx} {ns}{ew}"
    # Off-axis: larger component first
    if ady >= adx:
        return f"{ady} {ns} {adx} {ew}"
    return f"{adx} {ew} {ady} {ns}"


def chebyshev_distance(x1, y1, x2, y2):
    """8-directional grid distance (max of the axis deltas). None if any
    coordinate is missing. Used by the orphan composer to proximity-order
    ambient lines by their subject's distance from the wizard."""
    if None in (x1, y1, x2, y2):
        return None
    return max(abs(x1 - x2), abs(y1 - y2))


def _spawn_direction_summary(pts, wx, wy):
    """Top-two-direction coverage summary of points relative to (wx, wy).
    Bin into 8 compass slices; if only one slice is populated return that
    direction; if the two heaviest hold the bulk (>= 60%) return
    '5 north, 3 southeast' (+ 'N more' for the remainder); otherwise
    'scattered'. Returns '' if positions are unusable. The 60% bulk gate is
    the reliable scatter detector — it fires 'scattered' exactly when no two
    directions dominate (even spread)."""
    if wx is None or wy is None:
        return ""
    bins = {}
    total = 0
    for x, y in pts:
        d = _cardinal_direction(x - wx, y - wy)
        if not d:
            continue  # same tile as the wizard
        bins[d] = bins.get(d, 0) + 1
        total += 1
    if total == 0:
        return ""
    ranked = sorted(bins.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) == 1:
        return ranked[0][0]
    top = ranked[:2]
    top_sum = top[0][1] + top[1][1]
    if top_sum < 0.6 * total:
        return "scattered"
    parts = [f"{c} {d}" for d, c in top]
    rest = total - top_sum
    if rest > 0:
        parts.append(f"{rest} more")
    return ", ".join(parts)


def format_spawn_locality(members, wx, wy, show_coords, cap):
    """Locality clause for a spawn/cluster line, appended after
    '{count} {type}'. Scale-tiered (per the S11 summon-extent survey):
    - coords-on and count <= cap: exact ' at (x,y), (x,y), ...'.
    - coords-off and count == 1: precise offset ', N dir M dir'.
    - otherwise (count > cap, or coords-off multi): ', ' + directional summary
      (top-two-direction with a 'scattered' fallback).
    `cap` is the player-tunable spawn_coord_cap (0 = always cluster). Returns
    '' when there are no usable positions. Shared by the orphan (enemy) and
    digest (player) spawn rendering so the grammar is uniform."""
    pts = [(m.get('x'), m.get('y')) for m in members
           if m.get('x') is not None and m.get('y') is not None]
    if not pts:
        return ""
    n = len(pts)
    if show_coords and n <= cap:
        return " at " + ", ".join(f"({x},{y})" for x, y in pts)
    if not show_coords and n == 1 and wx is not None and wy is not None:
        return ", " + _direction_offset(pts[0][0] - wx, pts[0][1] - wy)
    summary = _spawn_direction_summary(pts, wx, wy)
    return (", " + summary) if summary else ""


# ---- Text Processing ----

def _pluralize(name):
    """Simple English pluralization for unit names at speech speed."""
    if not name:
        return name
    if name.endswith(('s', 'x', 'z')):
        return name + 'es'
    if name.endswith('ch') or name.endswith('sh'):
        return name + 'es'
    if name.endswith('f'):
        return name[:-1] + 'ves'
    if name.endswith('fe'):
        return name[:-2] + 'ves'
    if name.endswith('y') and len(name) > 1 and name[-2] not in 'aeiouAEIOU':
        return name[:-1] + 'ies'
    return name + 's'

def _clean_desc(text):
    """Strip game markup tags like [9_dark:dark] -> '9 dark' from description text."""
    def _clean_tag(m):
        content = m.group(1)
        if ':' in content:
            content = content.split(':')[0]
        return content.replace('_', ' ')
    return re.sub(r'\[([^\]]*)\]', _clean_tag, text)

def _log_line_speakable(text):
    """Combat-log line transcode with team designation (owner ruling
    2026-07-10). The game's unit color keys are exactly wizard/enemy/ally
    (log_color, Level.py:4520) and carry team information a sighted player
    reads as tint: '[Satyr:enemy]' speaks as 'enemy Satyr',
    '[Dancing Blade:ally]' as 'ally Dancing Blade'. The wizard needs no
    prefix — the name already says it. Every other style (damage-type
    tints etc.) is redundant with its label and drops, as in _clean_desc.
    Berserk quirk inherited faithfully: log_color tints a berserked ally
    'enemy' while the buff lasts, so speech calls it enemy — matching the
    tint the sighted player sees, not the underlying team."""
    def _tag(m):
        content = m.group(1)
        if ':' in content:
            label, style = content.rsplit(':', 1)
            label = label.replace('_', ' ')
            if style == 'enemy':
                return f"enemy {label}"
            if style == 'ally':
                return f"ally {label}"
            return label
        return content.replace('_', ' ')
    return re.sub(r'\[([^\]]*)\]', _tag, text)

def _split_message_for_speech(msg):
    """Split message text into buffer-navigable chunks.
    Keybinding lines and status effects become individual entries.
    Narrative paragraphs stay grouped."""
    chunks = []
    paragraphs = re.split(r'\n\s*\n', msg.strip())

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        lines = [l.strip() for l in para.split('\n') if l.strip()]

        binding_entries = []
        for line in lines:
            # Skip numpad grid visual lines (just digits and spaces)
            if re.match(r'^[\d\s]+$', line):
                continue
            # Numpad layout "4   6  ->" line: extract description
            if '->' in line and re.match(r'^\d', line):
                desc = line.split('->')[-1].strip()
                if desc:
                    binding_entries.append(f"Numpad: {desc}")
                continue
            # Multi-binding lines (3+ spaces between entries)
            parts = re.split(r'\s{3,}', line)
            if len(parts) > 1 and all(':' in p for p in parts if p.strip()):
                for p in parts:
                    p = p.strip()
                    if p:
                        binding_entries.append(p)
            else:
                binding_entries.append(line)

        # Decide: individual entries (keybinding/status block) vs one chunk
        has_colons = sum(1 for e in binding_entries if ':' in e)
        if has_colons >= 2 and len(binding_entries) > 1:
            # Keybinding or status effect block: each entry is a chunk
            chunks.extend(binding_entries)
        else:
            # Narrative paragraph: collapse to one chunk
            chunks.append(' '.join(binding_entries) if binding_entries else para)

    return chunks if len(chunks) > 1 else [msg]

# ---- Spatial Raycast & Terrain Classification ----

def _ray_length(level, x, y, dx, dy):
    """Count walkable tiles from (x,y) stepping by (dx,dy), not counting start tile."""
    length = 0
    cx, cy = x + dx, y + dy
    while 0 <= cx < level.width and 0 <= cy < level.height:
        if not level.tiles[cx][cy].can_walk:
            break
        length += 1
        cx += dx
        cy += dy
    return length

# 8 directions clockwise: label, dx, dy
_RAYCAST_DIRS = [
    ("north", 0, -1),
    ("northeast", 1, -1),
    ("east", 1, 0),
    ("southeast", 1, 1),
    ("south", 0, 1),
    ("southwest", -1, 1),
    ("west", -1, 0),
    ("northwest", -1, -1),
]

def _count_exits(level, x, y):
    """Count cardinal directions with at least 1 walkable neighbor from (x,y)."""
    count = 0
    for dx, dy in [(0, -1), (0, 1), (1, 0), (-1, 0)]:
        nx, ny = x + dx, y + dy
        if 0 <= nx < level.width and 0 <= ny < level.height and level.tiles[nx][ny].can_walk:
            count += 1
    return count

def _check_corridor_end(level, tx, ty, corridor_dx, corridor_dy):
    """Check if corridor terminal tile is a dead end, following through one bend.
    corridor_dx/dy: unit step direction from player toward this terminal tile.
    Returns True if the corridor effectively dead-ends (including via a single bend)."""
    exits = _count_exits(level, tx, ty)
    if exits == 1:
        return True  # Simple dead end
    if exits != 2:
        return False  # 3+ exits = junction, not a dead end
    # Exactly 2 exits: one back toward player, one perpendicular (a bend).
    # Follow the perpendicular direction and check if it dead-ends.
    back_dx, back_dy = -corridor_dx, -corridor_dy
    for dx, dy in [(0, -1), (0, 1), (1, 0), (-1, 0)]:
        if dx == back_dx and dy == back_dy:
            continue
        nx, ny = tx + dx, ty + dy
        if 0 <= nx < level.width and 0 <= ny < level.height and level.tiles[nx][ny].can_walk:
            ray = _ray_length(level, tx, ty, dx, dy)
            if ray < 1:
                continue
            end_x, end_y = tx + dx * ray, ty + dy * ray
            return _count_exits(level, end_x, end_y) == 1
    return False

def _corridor_is_catwalk(level, x, y, axis):
    """True if BOTH walls flanking the player's corridor tile are chasm rather than
    solid rock — i.e. a catwalk over the abyss. Purely taxonomic: it describes the
    shape (a walkway with drops to either side), and implies nothing about threat.
    Checks the immediate flanks only, so the label tracks the player's actual
    position as a mixed passage transitions between walled and chasm-flanked stretches.
    A map-edge flank or a single solid-wall flank keeps it a plain 'corridor'."""
    if axis == 'north-south':
        flanks = ((x + 1, y), (x - 1, y))
    else:  # east-west
        flanks = ((x, y + 1), (x, y - 1))
    for nx, ny in flanks:
        if not (0 <= nx < level.width and 0 <= ny < level.height):
            return False
        tile = level.tiles[nx][ny]
        # At a corridor tile both flanks are non-walkable by definition; require
        # both be chasm. can_walk guard is defensive (a branch opening here).
        if tile.can_walk or not getattr(tile, 'is_chasm', False):
            return False
    return True

def _classify_terrain(level, x, y):
    """Classify tile geometry from cardinal raycasts.
    Returns (class_name, axis_label) or (class_name, None).
    class_name: 'corridor', 'catwalk', 'junction', 'dead_end', 'bend', 'open'.
    axis_label: corridor axis + dead end terminus info, None otherwise.
    Corridor dead end detection: checks terminal tiles so player knows
    before committing whether a corridor leads nowhere."""
    n = _ray_length(level, x, y, 0, -1)
    s = _ray_length(level, x, y, 0, 1)
    e = _ray_length(level, x, y, 1, 0)
    w = _ray_length(level, x, y, -1, 0)

    # Count open cardinal directions (distance >= 1)
    exits = sum(1 for d in (n, s, e, w) if d >= 1)

    # Corridor: one axis open (combined >= 2), perpendicular both blocked
    # Check terminus tiles for dead ends so player knows before entering
    if n + s >= 2 and e == 0 and w == 0:
        dead_ends = []
        if n >= 1 and _check_corridor_end(level, x, y - n, 0, -1):
            dead_ends.append('north')
        if s >= 1 and _check_corridor_end(level, x, y + s, 0, 1):
            dead_ends.append('south')
        axis = 'north-south'
        if dead_ends:
            axis += ', dead end ' + ' and '.join(dead_ends)
        cls = 'catwalk' if _corridor_is_catwalk(level, x, y, 'north-south') else 'corridor'
        return (cls, axis)
    if e + w >= 2 and n == 0 and s == 0:
        dead_ends = []
        if e >= 1 and _check_corridor_end(level, x + e, y, 1, 0):
            dead_ends.append('east')
        if w >= 1 and _check_corridor_end(level, x - w, y, -1, 0):
            dead_ends.append('west')
        axis = 'east-west'
        if dead_ends:
            axis += ', dead end ' + ' and '.join(dead_ends)
        cls = 'catwalk' if _corridor_is_catwalk(level, x, y, 'east-west') else 'corridor'
        return (cls, axis)

    # Junction: 3+ cardinal exits AND constrained space
    if exits >= 3:
        has_narrow_axis = (e == 0 or w == 0 or n == 0 or s == 0)
        if has_narrow_axis:
            return ('junction', None)
        # Constrained crossroads: 3-4 cardinal exits but diagonal space is
        # mostly blocked (plus-shaped intersections where corridors meet)
        diag_blocked = 0
        for ddx, ddy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            nx, ny = x + ddx, y + ddy
            if not (0 <= nx < level.width and 0 <= ny < level.height and level.tiles[nx][ny].can_walk):
                diag_blocked += 1
        if diag_blocked >= 3:
            return ('junction', None)

    # Count diagonal exits — real movement options the player can take.
    # Must be counted before dead end check: a tile with 1 cardinal exit
    # but diagonal exits is a bend or junction, not a dead end.
    diag_exits = 0
    for _, dx, dy in _RAYCAST_DIRS:
        if abs(dx) == 1 and abs(dy) == 1:  # diagonal direction
            if _ray_length(level, x, y, dx, dy) >= 1:
                diag_exits += 1
    total_exits = exits + diag_exits

    # Dead end: exactly one total exit (cardinal + diagonal)
    if total_exits == 1:
        return ('dead_end', None)

    # Junction: 3+ total movement options in constrained space
    if total_exits >= 3:
        has_narrow_axis = (e == 0 or w == 0 or n == 0 or s == 0)
        if has_narrow_axis:
            return ('junction', None)

    # Bend/turn: exactly 2 total exits (L-shaped corridor turn)
    if total_exits == 2:
        return ('bend', None)

    # Open: room or unconstrained space — the default, silent
    return ('open', None)

_TERRAIN_LABELS = {
    'corridor': lambda axis: f"corridor {axis}",
    'catwalk': lambda axis: f"catwalk {axis}",
    'junction': lambda axis: "junction",
    'dead_end': lambda axis: "dead end",
    'bend': lambda axis: "turn",
}

def _scan_corridor_branches(level, px, py, axis):
    """Walk a corridor axis and find perpendicular openings.
    Distinguishes alcoves (1-tile pocket, single exit back to corridor) from
    branches (corridor continues or connects to other terrain).
    Returns list of "[alcove|branch] [perp_dir] [dist] [axis_dir]" strings.
    Ordered: axis-positive direction first (north/east), nearest to furthest,
    then axis-negative (south/west), nearest to furthest."""
    results = []

    def _classify_opening(cx, cy, dx, dy, dist, axis_dir_name):
        """Classify a perpendicular opening as alcove, nook, or branch.
        cx, cy: corridor tile position. dx, dy: perpendicular step.
        Alcove: 1 tile deep, dead end. Nook: multi-tile dead end.
        Branch: connects to other terrain."""
        perp_name = {(1, 0): 'east', (-1, 0): 'west',
                     (0, -1): 'north', (0, 1): 'south'}[(dx, dy)]
        ray = _ray_length(level, cx, cy, dx, dy)
        if ray == 1 and _count_exits(level, cx + dx, cy + dy) == 1:
            results.append(f"alcove {perp_name} {dist} {axis_dir_name}")
        else:
            # Check if the opening dead-ends (nook) or connects somewhere (branch)
            end_x, end_y = cx + dx * ray, cy + dy * ray
            if _count_exits(level, end_x, end_y) == 1:
                results.append(f"nook {perp_name} {dist} {axis_dir_name}")
            elif _check_corridor_end(level, end_x, end_y, dx, dy):
                results.append(f"nook {perp_name} {dist} {axis_dir_name}")
            else:
                results.append(f"branch {perp_name} {dist} {axis_dir_name}")

    if axis.startswith('north-south'):
        for i in range(1, _ray_length(level, px, py, 0, -1) + 1):
            ty = py - i
            if px + 1 < level.width and level.tiles[px + 1][ty].can_walk:
                _classify_opening(px, ty, 1, 0, i, 'north')
            if px - 1 >= 0 and level.tiles[px - 1][ty].can_walk:
                _classify_opening(px, ty, -1, 0, i, 'north')
        for i in range(1, _ray_length(level, px, py, 0, 1) + 1):
            ty = py + i
            if px + 1 < level.width and level.tiles[px + 1][ty].can_walk:
                _classify_opening(px, ty, 1, 0, i, 'south')
            if px - 1 >= 0 and level.tiles[px - 1][ty].can_walk:
                _classify_opening(px, ty, -1, 0, i, 'south')
    elif axis.startswith('east-west'):
        for i in range(1, _ray_length(level, px, py, 1, 0) + 1):
            tx = px + i
            if py - 1 >= 0 and level.tiles[tx][py - 1].can_walk:
                _classify_opening(tx, py, 0, -1, i, 'east')
            if py + 1 < level.height and level.tiles[tx][py + 1].can_walk:
                _classify_opening(tx, py, 0, 1, i, 'east')
        for i in range(1, _ray_length(level, px, py, -1, 0) + 1):
            tx = px - i
            if py - 1 >= 0 and level.tiles[tx][py - 1].can_walk:
                _classify_opening(tx, py, 0, -1, i, 'west')
            if py + 1 < level.height and level.tiles[tx][py + 1].can_walk:
                _classify_opening(tx, py, 0, 1, i, 'west')
    return results

# ---- Deploy Helpers ----

def _quadrant_label(x, y, width, height):
    """Fixed quadrant relative to map center. NE/SE/SW/NW.

    Center is derived from the level's own dimensions so it tracks the grid
    size — RW3 is 18x18 (center 9); RW2 was 33x33 (center 16). A hardcoded
    center collapses almost everything to one quadrant on the smaller grid."""
    cx = width // 2
    cy = height // 2
    if x >= cx:
        return "northeast" if y < cy else "southeast"
    else:
        return "northwest" if y < cy else "southwest"

def _number_deploy_dupes(items):
    """Add ordinal suffix to duplicate names in a deploy cycling list.
    Items are (entity, x, y, name) tuples. Returns new list with
    ' 1', ' 2' etc. appended to names that appear more than once."""
    base_names = [n for _, _, _, n in items]
    counts = Counter(base_names)
    if not any(c > 1 for c in counts.values()):
        return items
    seen = {}
    result = []
    for entity, x, y, n in items:
        if counts[n] > 1:
            seen[n] = seen.get(n, 0) + 1
            result.append((entity, x, y, f"{n} {seen[n]}"))
        else:
            result.append((entity, x, y, n))
    return result


# ---- Collapse-Tier Same-Shape Merging ----
# When many same-type units experience the same event in the same turn
# (e.g. 13 Ghostly Cursed Cats each heal 5), naive id-based grouping
# produces 13 one-line readouts. This merges them into one collective
# line: "13 Ghostly Cursed Cats heal 5, east."
#
# Input groups are dicts as produced by _build_target_groups in
# screen_reader.py: {'target_name', 'target_unit', 'cardinal', 'los',
# 'distance', 'direction', 'events': [evt_dict, ...]}.
# Output groups preserve the same shape; collective groups additionally
# carry '_collective_text' which the deliverer speaks verbatim.

MERGE_MIN_COUNT = 3         # under this, speak groups individually
MAJORITY_CARDINAL_RATIO = 0.6  # fraction of same cardinal needed to claim direction

def _collective_cardinal(cardinals):
    """Pick a shared cardinal from a list, or 'scattered' when mixed.
    Empty strings are ignored. Returns '' if no cardinal data at all."""
    filtered = [c for c in cardinals if c]
    if not filtered:
        return ''
    most_common, count = Counter(filtered).most_common(1)[0]
    if count / len(filtered) >= MAJORITY_CARDINAL_RATIO:
        return most_common
    return 'scattered'

def _merge_same_shape_groups(groups, min_count=MERGE_MIN_COUNT):
    """Collapse single-event groups sharing (event_type, target_name, payload)
    into collective groups. Groups with multiple events pass through unchanged.

    Merges heal, damage, and death events. Signature per type:
    - heal: (heal_amount,)
    - damage: (source, spell, amount, dtype)
    - death: (is_expired,)

    Returns a new list of group dicts, re-sorted by (not los, distance).
    """
    buckets = {}        # (event_type, target_name, sig) -> [groups]
    passthrough = []

    for group in groups:
        events = group.get('events', [])
        if len(events) != 1:
            passthrough.append(group)
            continue
        evt = events[0]
        etype = evt.get('event_type', '')
        if etype == 'heal':
            sig = ('heal', evt.get('heal_amount', 0))
        elif etype == 'damage':
            sig = ('damage', evt.get('source_name', ''),
                   evt.get('spell_name', ''),
                   evt.get('damage', 0), evt.get('damage_type', ''))
        elif etype == 'death':
            sig = ('death', evt.get('is_expired', False))
        else:
            passthrough.append(group)
            continue
        key = (etype, group.get('target_name', ''), sig)
        buckets.setdefault(key, []).append(group)

    result = list(passthrough)
    for key, bucket in buckets.items():
        if len(bucket) < min_count:
            # Not enough to warrant a collective line — speak individually.
            result.extend(bucket)
        else:
            result.append(_make_collective_group(bucket, key))

    result.sort(key=lambda g: (not g.get('los', True), g.get('distance', 0)))
    return result

def _make_collective_group(bucket, key):
    """Build a synthetic collective group dict from N same-shape single-event groups."""
    etype, target_name, sig = key
    count = len(bucket)
    cardinal = _collective_cardinal([g.get('cardinal', '') for g in bucket])
    # LoS: True if ANY in-sight (so no "Out of sight" prefix unless all are out);
    # this avoids hiding partially-visible collective events behind the prefix.
    any_los = any(g.get('los', True) for g in bucket)
    distances = [g.get('distance', 0) for g in bucket]
    mean_distance = sum(distances) / len(distances) if distances else 0

    plural = _pluralize(target_name)
    if etype == 'heal':
        heal_amount = sig[1]
        body = f"{count} {plural} heal {heal_amount}"
    elif etype == 'damage':
        _, source_name, spell_name, damage, damage_type = sig
        entry_parts = [source_name]
        show_spell = (spell_name and spell_name != source_name
                      and spell_name != "Melee Attack")
        if show_spell:
            entry_parts.append(spell_name)
        if show_spell and damage_type and damage_type.lower() in spell_name.lower():
            entry_parts.append(str(damage))
        else:
            entry_parts.append(f"{damage} {damage_type}")
        body = f"{count} {plural}, {' '.join(entry_parts)}"
    elif etype == 'death':
        is_expired = sig[1]
        body = f"{count} {plural} {'expired' if is_expired else 'killed'}"
    else:
        body = f"{count} {plural}"

    if cardinal == 'scattered':
        text = f"{body}, scattered"
    elif cardinal:
        text = f"{body}, {cardinal}"
    else:
        text = body

    return {
        'target_name': f"{count} {plural}",
        'target_unit': None,
        'direction': cardinal,
        'cardinal': cardinal,
        'distance': mean_distance,
        'los': any_los,
        'events': [],
        '_collective_text': text,
    }


# ---- Pathfinding helpers ----
# Pure helpers for pathfinding output formatting. The actual find_path() call
# lives in screen_reader.py because it needs the live Level object; these are
# the slices that can be tested in isolation.

def _point_xy(p):
    """Normalize a point-like into (x, y). Accepts (x, y) tuple or .x/.y object."""
    if isinstance(p, tuple):
        return p
    return (p.x, p.y)

def _compress_path(points, target_kind='terrain'):
    """Compress a sequence of consecutive grid points into a spoken path string.

    points: list where points[0] is the start and points[-1] is the destination,
            with each successive pair representing one grid step. Accepts (x, y)
            tuples or Point-like objects with .x/.y. Caller is responsible for
            composing this list (typically [Point(player.x, player.y), *path]).
    target_kind: 'terrain' (walkable destination, tail = 'arrive.') or
                 'unit' (path resolved to adjacent tile, tail = 'arrive adjacent.').

    Returns one string ready for TTS. Format conventions:
    - 0 or 1 input points        -> 'Already at target.'
    - 1 step single direction    -> 'Northeast, arrive.'
    - N steps single direction   -> 'East 5, arrive.'
    - Multi-segment              -> '12 steps. Northeast 4, north 3, east 5, arrive.'
    - target_kind='unit' swaps tail: 'arrive adjacent.'

    Adjacent-target short-circuiting and unreachable detection happen at the
    call site before this is invoked; this helper assumes a valid path."""
    if not points or len(points) < 2:
        return "Already at target."

    coords = [_point_xy(p) for p in points]
    diffs = []
    for i in range(len(coords) - 1):
        dx = coords[i + 1][0] - coords[i][0]
        dy = coords[i + 1][1] - coords[i][1]
        if dx == 0 and dy == 0:
            continue
        sdx = (dx > 0) - (dx < 0)
        sdy = (dy > 0) - (dy < 0)
        diffs.append((sdx, sdy))

    if not diffs:
        return "Already at target."

    dirs = [_cardinal_direction(dx, dy) for dx, dy in diffs]
    runs = []
    cur_dir = dirs[0]
    cur_count = 1
    for d in dirs[1:]:
        if d == cur_dir:
            cur_count += 1
        else:
            runs.append((cur_dir, cur_count))
            cur_dir = d
            cur_count = 1
    runs.append((cur_dir, cur_count))

    total = len(diffs)
    arrive = "arrive adjacent" if target_kind == 'unit' else "arrive"

    if len(runs) == 1:
        d, n = runs[0]
        head = d.capitalize() if n == 1 else f"{d.capitalize()} {n}"
        return f"{head}, {arrive}."

    body_parts = []
    for i, (d, n) in enumerate(runs):
        label = d.capitalize() if i == 0 else d
        body_parts.append(f"{label} {n}")
    return f"{total} steps. {', '.join(body_parts)}, {arrive}."

def _classify_unreachable(level, target_xy):
    """Decide why pathfinding failed. Returns a token, not a user-facing string,
    so call sites format consistently and tests stay simple.

    Returns:
    - 'impassable' if target tile is itself unwalkable (wall, chasm, off-map).
      Distinguishes 'destination cannot accept walkers' from 'no path right now.'
    - 'no_route' otherwise (separated regions, boxed-in player, transient blockage)."""
    tx, ty = target_xy
    if not (0 <= tx < level.width and 0 <= ty < level.height):
        return 'impassable'
    if not level.tiles[tx][ty].can_walk:
        return 'impassable'
    return 'no_route'

def _walkable_neighbors(level, target_xy):
    """Return the 8 grid neighbors of target_xy that are walkable + in bounds.
    Used for unit-target resolution: pathfind to one of these instead of onto
    the (impassable) unit tile itself. List order is N, NE, E, SE, S, SW, W, NW
    so callers see deterministic ordering. Caller picks among them by pathing
    cost or another policy."""
    tx, ty = target_xy
    neighbors = []
    for dx, dy in [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]:
        nx, ny = tx + dx, ty + dy
        if not (0 <= nx < level.width and 0 <= ny < level.height):
            continue
        if level.tiles[nx][ny].can_walk:
            neighbors.append((nx, ny))
    return neighbors


# ---- Keybind-following dispatch (cursor-tool pass, slice 1) ----
# The mirrored keys (T, L, U, I, O) speak the category the game's own held key
# draws. Dispatch consults the live key_binds table per press so the
# speak/show pairing survives player rebinds by construction; the hardcoded
# fallback only matters when the bind table is unavailable (e.g. tests, or an
# API drift that renames the bind id).

def _bound_keys(key_binds, bind_id, fallback):
    """Return the tuple of keycodes bound to bind_id in the game's key_binds
    table (None slots filtered), or (fallback,) when the table or id is
    unavailable. key_binds values are lists like [K_i, None]."""
    if bind_id is not None and key_binds is not None:
        try:
            return tuple(k for k in key_binds[bind_id] if k)
        except (KeyError, IndexError, TypeError):
            pass
    return (fallback,)

def _key_matches_bind(key_binds, bind_id, key, fallback):
    """True if a pressed keycode is bound to bind_id (see _bound_keys)."""
    return key in _bound_keys(key_binds, bind_id, fallback)


def _compress_crossed(labels):
    """Compress crossed-tile labels into one short spoken clause for the
    Shift-move landing summary (cursor-tool pass, slice 2). Duplicates group
    with digit counts in first-appearance order; plain-floor tiles were
    already dropped by the caller. ['web', 'web', 'Imp'] -> '2 webs, Imp'."""
    counts = {}
    order = []
    for label in labels:
        if label not in counts:
            order.append(label)
            counts[label] = 0
        counts[label] += 1
    parts = []
    for label in order:
        n = counts[label]
        parts.append(label if n == 1 else f"{n} {_pluralize(label)}")
    return ", ".join(parts)


# ---- Unified pin system (cursor-tool pass, slice 4) ----
# Pure ordering/focus logic for the K pin cycle. The cycle walks category
# BLOCKS with no interleaving (owner ruling), proximity-ordered within each
# block from the attention reference. Category labels here are list-internal;
# spoken forms come from PIN_BLOCK_LABELS.

PIN_BLOCK_ORDER = ('enemy', 'ally', 'landmark', 'bookmark')

PIN_BLOCK_LABELS = {
    'enemy': ('enemy', 'enemies'),
    'ally': ('ally', 'allies'),
    'landmark': ('landmark', 'landmarks'),
    'bookmark': ('bookmark', 'bookmarks'),
}


def pin_block_label(category, count):
    """Spoken label for a block: singular at 1, plural otherwise."""
    singular, plural = PIN_BLOCK_LABELS[category]
    return singular if count == 1 else plural


def order_pins_in_blocks(tagged):
    """Order tagged pins into category blocks for the K cycle.

    tagged: list of (category, distance, pin) where category is one of
    PIN_BLOCK_ORDER and distance is from the attention reference.
    Returns (ordered, block_starts): ordered is a list of (pin, category)
    in block order (PIN_BLOCK_ORDER), proximity-sorted within each block;
    block_starts is the set of indices where a new block begins (the cycle
    speaks the block label at those entries)."""
    ordered = []
    block_starts = set()
    for category in PIN_BLOCK_ORDER:
        members = [(dist, i, pin) for i, (cat, dist, pin) in enumerate(tagged)
                   if cat == category]
        if not members:
            continue
        members.sort(key=lambda m: (m[0], m[1]))
        block_starts.add(len(ordered))
        ordered.extend((pin, category) for _dist, _i, pin in members)
    return ordered, block_starts


def pin_count_header(ordered):
    """Fresh-cycle count header from order_pins_in_blocks output:
    '5 pinned. 2 enemies, 1 ally, 2 bookmarks'."""
    total = len(ordered)
    counts = {}
    for _pin, category in ordered:
        counts[category] = counts.get(category, 0) + 1
    parts = [f"{counts[c]} {pin_block_label(c, counts[c])}"
             for c in PIN_BLOCK_ORDER if c in counts]
    return f"{total} pinned. {', '.join(parts)}"


def focus_handoff(pins):
    """Next focused pin after the current one died/unpinned: the most
    recently pinned survivor (highest 'seq'), or None if the list is empty."""
    if not pins:
        return None
    return max(pins, key=lambda p: p.get('seq', 0))


def pin_matches(pin, kind, unit=None, x=None, y=None):
    """Toggle-identity predicate: unit pins match by object identity, tile
    pins (landmarks and bookmarks alike) by coordinates."""
    if pin['kind'] != kind:
        return False
    if kind == 'unit':
        return pin['unit'] is unit
    return pin['x'] == x and pin['y'] == y
