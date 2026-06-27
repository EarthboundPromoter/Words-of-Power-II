# Tests for pure helper functions in helpers.py
# Run with: cd ~ && python -m pytest "<path_to_mod>/tests/test_helpers.py" -v

import sys
import os

# Add the mod directory to Python's import path so we can find helpers.py
mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from helpers import (_cardinal_direction, _bearing_index, _direction_offset, _pluralize,
                     _clean_desc, _split_message_for_speech, _quadrant_label,
                     _number_deploy_dupes, _classify_terrain, _ray_length,
                     _count_exits, _scan_corridor_branches,
                     _collective_cardinal, _merge_same_shape_groups,
                     _make_collective_group,
                     _compress_path, _classify_unreachable, _walkable_neighbors,
                     _point_xy,
                     classify_resist_outcome)


def test_classify_resist_immune_at_100_pct():
    assert classify_resist_outcome(30, 0, target_resist_pct=100) == 'immune'


def test_classify_resist_immune_above_100_pct():
    """Game caps at 100 before applying, but the journal also caps at
    100 in payload. >= 100 is the immunity threshold."""
    assert classify_resist_outcome(30, 0, target_resist_pct=100) == 'immune'


def test_classify_resist_resisted_when_high_but_under_100():
    """75% resist with small damage rounds to 0 — render as 'resisted',
    not 'immune'. Listener distinguishes structural immunity from
    rounding-to-zero high resists."""
    assert classify_resist_outcome(2, 0, target_resist_pct=75) == 'resisted'


def test_classify_resist_partial():
    assert classify_resist_outcome(30, 15, target_resist_pct=50) == 'partial'


def test_classify_resist_vulnerable_negative_resist():
    """Negative resist amplifies damage; classifier returns 'vulnerable'."""
    assert classify_resist_outcome(30, 60, target_resist_pct=-100) == 'vulnerable'


def test_classify_resist_normal():
    assert classify_resist_outcome(30, 30, target_resist_pct=0) == 'normal'


def test_classify_resist_no_pct_falls_back_to_resisted():
    """When target_resist_pct is unknown (None), full-resist outcome
    cannot be distinguished from immunity — defaults to 'resisted' for
    safety. Listener gets the conservative phrasing."""
    assert classify_resist_outcome(2, 0, target_resist_pct=None) == 'resisted'


def test_classify_resist_handles_none_damages():
    assert classify_resist_outcome(None, None) == 'normal'


# ---- Fake level grid for terrain classification tests ----
# The functions only need level.width, level.height, and level.tiles[x][y].can_walk.

class _FakeTile:
    def __init__(self, walkable):
        self.can_walk = walkable

class _FakeLevel:
    """Minimal level object built from an ASCII grid.
    W = wall, . = walkable floor. Origin (0,0) is top-left."""
    def __init__(self, grid_str):
        rows = [line for line in grid_str.strip().split('\n')]
        self.height = len(rows)
        self.width = max(len(row) for row in rows)
        # tiles[x][y] — column-major like the game
        self.tiles = []
        for x in range(self.width):
            col = []
            for y in range(self.height):
                ch = rows[y][x] if x < len(rows[y]) else 'W'
                col.append(_FakeTile(ch == '.'))
            self.tiles.append(col)


# ---- _cardinal_direction ----

class TestCardinalDirection:

    def test_origin_returns_empty(self):
        assert _cardinal_direction(0, 0) == ""

    def test_pure_east(self):
        assert _cardinal_direction(5, 0) == "east"

    def test_pure_west(self):
        assert _cardinal_direction(-3, 0) == "west"

    def test_pure_south(self):
        # y+ = south in screen coords
        assert _cardinal_direction(0, 4) == "south"

    def test_pure_north(self):
        assert _cardinal_direction(0, -2) == "north"

    def test_southeast(self):
        assert _cardinal_direction(3, 3) == "southeast"

    def test_northwest(self):
        assert _cardinal_direction(-5, -5) == "northwest"

    def test_northeast(self):
        assert _cardinal_direction(4, -4) == "northeast"

    def test_southwest(self):
        assert _cardinal_direction(-2, 2) == "southwest"


# ---- _bearing_index ----

class TestBearingIndex:

    def test_origin_returns_none(self):
        assert _bearing_index(0, 0) is None

    def test_north_is_zero(self):
        # North = y negative
        assert _bearing_index(0, -5) == 0

    def test_east_is_two(self):
        assert _bearing_index(5, 0) == 2

    def test_south_is_four(self):
        assert _bearing_index(0, 5) == 4

    def test_west_is_six(self):
        assert _bearing_index(-5, 0) == 6

    def test_northeast_is_one(self):
        assert _bearing_index(3, -3) == 1

    def test_southwest_is_five(self):
        assert _bearing_index(-3, 3) == 5


# ---- _direction_offset ----

class TestDirectionOffset:

    def test_same_position(self):
        assert _direction_offset(0, 0) == "here"

    def test_pure_south(self):
        assert _direction_offset(0, 5) == "5 south"

    def test_pure_north(self):
        assert _direction_offset(0, -3) == "3 north"

    def test_pure_east(self):
        assert _direction_offset(4, 0) == "4 east"

    def test_pure_west(self):
        assert _direction_offset(-2, 0) == "2 west"

    def test_diagonal_equal(self):
        # Equal components = single combined direction
        assert _direction_offset(3, 3) == "3 southeast"

    def test_diagonal_equal_northwest(self):
        assert _direction_offset(-5, -5) == "5 northwest"

    def test_off_axis_south_dominant(self):
        # Larger component first
        assert _direction_offset(2, 5) == "5 south 2 east"

    def test_off_axis_east_dominant(self):
        assert _direction_offset(7, 3) == "7 east 3 south"

    def test_off_axis_north_dominant(self):
        assert _direction_offset(-1, -4) == "4 north 1 west"

    def test_single_step(self):
        assert _direction_offset(1, 0) == "1 east"
        assert _direction_offset(0, 1) == "1 south"


# ---- _pluralize ----

class TestPluralize:

    def test_regular(self):
        assert _pluralize("Goblin") == "Goblins"
        assert _pluralize("Imp") == "Imps"

    def test_sibilant_s(self):
        assert _pluralize("Glass") == "Glasses"

    def test_sibilant_x(self):
        assert _pluralize("Fox") == "Foxes"

    def test_sibilant_z(self):
        assert _pluralize("Topaz") == "Topazes"

    def test_ch_ending(self):
        assert _pluralize("Witch") == "Witches"
        assert _pluralize("Lich") == "Liches"

    def test_sh_ending(self):
        assert _pluralize("Flash") == "Flashes"

    def test_f_ending(self):
        assert _pluralize("Wolf") == "Wolves"
        assert _pluralize("Thief") == "Thieves"

    def test_fe_ending(self):
        assert _pluralize("Wife") == "Wives"

    def test_consonant_y(self):
        assert _pluralize("Fairy") == "Fairies"
        assert _pluralize("Harpy") == "Harpies"

    def test_vowel_y(self):
        # Vowel before y — just add s
        assert _pluralize("Donkey") == "Donkeys"
        assert _pluralize("Monkey") == "Monkeys"

    def test_empty_string(self):
        assert _pluralize("") == ""

    def test_single_char(self):
        assert _pluralize("y") == "ys"


# ---- _clean_desc ----

class TestCleanDesc:

    def test_no_tags(self):
        assert _clean_desc("Deals 9 damage") == "Deals 9 damage"

    def test_simple_tag(self):
        assert _clean_desc("[fire]") == "fire"

    def test_tag_with_colon(self):
        # [9_dark:dark] -> "9 dark" (take before colon, replace underscores)
        assert _clean_desc("[9_dark:dark]") == "9 dark"

    def test_underscores_replaced(self):
        assert _clean_desc("[holy_fire:holy]") == "holy fire"

    def test_mixed_text_and_tags(self):
        assert _clean_desc("Deals [9_fire:fire] damage") == "Deals 9 fire damage"

    def test_multiple_tags(self):
        assert _clean_desc("[3_ice:ice] and [5_fire:fire]") == "3 ice and 5 fire"

    def test_empty_string(self):
        assert _clean_desc("") == ""

    def test_tag_no_colon_with_underscore(self):
        assert _clean_desc("[dark_magic]") == "dark magic"


# ---- _split_message_for_speech ----

class TestSplitMessageForSpeech:

    def test_single_paragraph_returns_original(self):
        # Single chunk = return [msg] unchanged
        msg = "Welcome to the game."
        result = _split_message_for_speech(msg)
        assert result == [msg]

    def test_keybinding_block_splits_into_individual_entries(self):
        msg = "W: Move up\nS: Move down\nA: Move left\nD: Move right"
        result = _split_message_for_speech(msg)
        assert "W: Move up" in result
        assert "S: Move down" in result
        assert "A: Move left" in result
        assert "D: Move right" in result
        assert len(result) == 4

    def test_narrative_stays_grouped(self):
        msg = "The wizard stood alone.\nDarkness surrounded him."
        result = _split_message_for_speech(msg)
        # No colons = narrative, collapsed to one chunk
        assert len(result) == 1

    def test_two_paragraphs_mixed(self):
        msg = "Welcome to the dungeon.\n\nW: Move up\nA: Move left"
        result = _split_message_for_speech(msg)
        # First paragraph: narrative (one chunk). Second: keybindings (two chunks).
        assert len(result) == 3
        assert result[0] == "Welcome to the dungeon."
        assert "W: Move up" in result
        assert "A: Move left" in result

    def test_multi_binding_line_splits(self):
        # 3+ spaces between entries on same line
        msg = "W: Up   S: Down\nA: Left   D: Right"
        result = _split_message_for_speech(msg)
        assert "W: Up" in result
        assert "S: Down" in result
        assert "A: Left" in result
        assert "D: Right" in result

    def test_numpad_grid_lines_skipped(self):
        # Numpad grid alone doesn't produce enough entries to split,
        # so the whole message comes back as [msg]. Test with enough
        # keybinding context to trigger splitting.
        msg = "W: Up\nS: Down\n\n7 8 9\n4   6  -> Move diagonally\n1 2 3"
        result = _split_message_for_speech(msg)
        assert any("Numpad: Move diagonally" in chunk for chunk in result)
        # Pure digit grid lines should not appear as chunks
        assert not any(chunk.strip() == "7 8 9" for chunk in result)
        assert not any(chunk.strip() == "1 2 3" for chunk in result)

    def test_empty_string(self):
        result = _split_message_for_speech("")
        assert result == [""]


# ---- _quadrant_label ----

class TestQuadrantLabel:

    def test_northeast(self):
        # x >= center, y < center
        assert _quadrant_label(20, 5) == "northeast"

    def test_southeast(self):
        # x >= center, y >= center
        assert _quadrant_label(25, 20) == "southeast"

    def test_northwest(self):
        # x < center, y < center
        assert _quadrant_label(5, 10) == "northwest"

    def test_southwest(self):
        # x < center, y >= center
        assert _quadrant_label(3, 30) == "southwest"

    def test_center_goes_southeast(self):
        # Exactly at center: x >= 16, y >= 16
        assert _quadrant_label(16, 16) == "southeast"

    def test_center_x_north(self):
        # x == center, y < center
        assert _quadrant_label(16, 0) == "northeast"


# ---- _number_deploy_dupes ----

class TestNumberDeployDupes:

    def test_no_duplicates_unchanged(self):
        items = [("a", 1, 2, "Wolf"), ("b", 3, 4, "Goblin"), ("c", 5, 6, "Imp")]
        result = _number_deploy_dupes(items)
        assert result == items  # Same list back, no modification

    def test_duplicates_get_numbered(self):
        items = [
            ("a", 1, 2, "Memory Orb"),
            ("b", 5, 6, "Memory Orb"),
            ("c", 8, 9, "Memory Orb"),
        ]
        result = _number_deploy_dupes(items)
        assert result[0][3] == "Memory Orb 1"
        assert result[1][3] == "Memory Orb 2"
        assert result[2][3] == "Memory Orb 3"

    def test_mixed_dupes_and_unique(self):
        items = [
            ("a", 0, 0, "Memory Orb"),
            ("b", 1, 1, "Scroll: Fireball"),
            ("c", 2, 2, "Memory Orb"),
        ]
        result = _number_deploy_dupes(items)
        assert result[0][3] == "Memory Orb 1"
        assert result[1][3] == "Scroll: Fireball"  # Unique — unchanged
        assert result[2][3] == "Memory Orb 2"

    def test_empty_list(self):
        assert _number_deploy_dupes([]) == []

    def test_single_item(self):
        items = [("a", 0, 0, "Wolf")]
        assert _number_deploy_dupes(items) == items

    def test_preserves_entity_and_position(self):
        items = [("obj1", 10, 20, "Orb"), ("obj2", 30, 40, "Orb")]
        result = _number_deploy_dupes(items)
        # Entity and coordinates preserved, only name changes
        assert result[0][0] == "obj1"
        assert result[0][1] == 10
        assert result[0][2] == 20
        assert result[1][0] == "obj2"


# ---- Terrain Classification ----
# Test grids are small ASCII maps based on patterns from actual RW2 levels.
# W = wall, . = walkable. Player position marked P in comments (tested at that coordinate).

class TestClassifyTerrain:

    def test_north_south_corridor(self):
        # Level 1 pattern: 1-tile-wide passage between rooms
        #   01234
        # 0 WWWWW
        # 1 WW.WW
        # 2 WW.WW  <- player at (2,2)
        # 3 WW.WW
        # 4 WWWWW
        level = _FakeLevel(
            "WWWWW\n"
            "WW.WW\n"
            "WW.WW\n"
            "WW.WW\n"
            "WWWWW"
        )
        cls, axis = _classify_terrain(level, 2, 2)
        assert cls == 'corridor'
        assert 'north-south' in axis

    def test_east_west_corridor(self):
        #   01234567
        # 0 WWWWWWWW
        # 1 WW.....W
        # 2 WWWWWWWW
        level = _FakeLevel(
            "WWWWWWWW\n"
            "WW.....W\n"
            "WWWWWWWW"
        )
        cls, axis = _classify_terrain(level, 4, 1)
        assert cls == 'corridor'
        assert 'east-west' in axis

    def test_dead_end_one_exit(self):
        # Spur off a room — only one exit (north), diagonals also walled
        #   012
        # 0 W.W
        # 1 W.W  <- player at (1,1): N=1, S=wall, E=wall, W=wall, no diags → 1 total
        # 2 WWW
        level = _FakeLevel(
            "W.W\n"
            "W.W\n"
            "WWW"
        )
        cls, axis = _classify_terrain(level, 1, 1)
        assert cls == 'dead_end'

    def test_cardinal_dead_end_with_diagonal_is_bend(self):
        # One cardinal exit (east) but also a diagonal exit (northeast).
        # Player has 2 movement options — this is a bend, not a dead end.
        # Cave level pattern: irregular rock leaves diagonal gap.
        #   01234
        # 0 WW.WW  <- diagonal exit NE from (2,1) to (2,0) is open
        # 1 WW..W  <- player at (2,1): E=1 cardinal, NE diagonal open
        # 2 WWWWW
        level = _FakeLevel(
            "WW.WW\n"
            "WW..W\n"
            "WWWWW"
        )
        cls, axis = _classify_terrain(level, 2, 1)
        # 1 cardinal + 1 diagonal = 2 total exits → bend, not dead end
        assert cls == 'bend'

    def test_dead_end(self):
        # Only one exit direction
        #   01234
        # 0 WWWWW
        # 1 WW.WW  <- player at (2,1): only exit is south
        # 2 WW.WW
        # 3 WW.WW
        # 4 WWWWW
        level = _FakeLevel(
            "WWWWW\n"
            "WW.WW\n"
            "WW.WW\n"
            "WW.WW\n"
            "WWWWW"
        )
        # From (2,1): N→(2,0)=wall=0, S→(2,2)=walk, E→(3,1)=wall=0, W→(1,1)=wall=0
        # N+S: 0+2=2 and E==0 and W==0 → corridor north-south
        # Dead end check: N terminus (2,0) blocked, _check_corridor_end skipped (n=0)
        # Actually n=0, so dead_ends stays empty for north.
        # S terminus at (2,3): _count_exits(2,3) = 1 (only north). Dead end south.
        cls, axis = _classify_terrain(level, 2, 1)
        assert cls == 'corridor'
        assert 'dead end south' in axis

    def test_t_junction(self):
        # Level 1 pattern: corridor meets perpendicular corridor
        #   0123456
        # 0 WWWWWWW
        # 1 WW...WW
        # 2 WWWWWWW
        # 3 WWW.WWW
        # 4 WWW.WWW
        # Wait, that's not a junction at the intersection. Let me draw properly:
        #   0123456
        # 0 WWWWWWW
        # 1 WW.W.WW
        # 2 WW...WW  <- player at (3,2): N, E, W open; S blocked
        # 3 WWWWWWW
        level = _FakeLevel(
            "WWWWWWW\n"
            "WWW.WWW\n"
            "WW...WW\n"
            "WWWWWWW"
        )
        # From (3,2): N→(3,1)=walk=1, S→(3,3)=wall=0, E→(4,2)=walk, W→(2,2)=walk
        # exits=3, has_narrow_axis (S==0) → junction
        cls, axis = _classify_terrain(level, 3, 2)
        assert cls == 'junction'

    def test_l_bend(self):
        # Level 4 pattern: corridor turns 90 degrees
        #   01234
        # 0 WWWWW
        # 1 WW.WW
        # 2 WW..W  <- player at (2,2): N and E open, S and W blocked
        # 3 WWWWW
        level = _FakeLevel(
            "WWWWW\n"
            "WW.WW\n"
            "WW..W\n"
            "WWWWW"
        )
        cls, axis = _classify_terrain(level, 2, 2)
        assert cls == 'bend'

    def test_open_room(self):
        # Level 2 pattern: large open space
        #   0123456
        # 0 WWWWWWW
        # 1 W.....W
        # 2 W.....W
        # 3 W.....W  <- player at (3,2)
        # 4 W.....W
        # 5 WWWWWWW
        level = _FakeLevel(
            "WWWWWWW\n"
            "W.....W\n"
            "W.....W\n"
            "W.....W\n"
            "W.....W\n"
            "WWWWWWW"
        )
        cls, axis = _classify_terrain(level, 3, 2)
        assert cls == 'open'

    def test_corridor_with_dead_end_both_directions(self):
        # Short corridor with walls at both ends
        #   012
        # 0 WWW
        # 1 W.W
        # 2 W.W  <- player at (1,2)
        # 3 W.W
        # 4 WWW
        level = _FakeLevel(
            "WWW\n"
            "W.W\n"
            "W.W\n"
            "W.W\n"
            "WWW"
        )
        cls, axis = _classify_terrain(level, 1, 2)
        assert cls == 'corridor'
        # Format is "north-south, dead end north and south"
        assert 'dead end' in axis
        assert 'north' in axis and 'south' in axis

    def test_corridor_openings_alcove_nook_branch(self):
        # One corridor with all three opening types distinguished.
        # N-S corridor at x=3, player at (3,7).
        # y=3: alcove east (1 tile, dead end)
        # y=5: nook east (2 tiles, dead end — goes somewhere but terminates)
        # y=9: branch east (connects to open room — real destination)
        #   01234567890
        # 0 WWWWWWWWWWW
        # 1 WWW.WWWWWWW
        # 2 WWW.WWWWWWW
        # 3 WWW..WWWWWW  <- alcove
        # 4 WWW.WWWWWWW
        # 5 WWW...WWWWW  <- nook (2 tiles east, dead end)
        # 6 WWW.WWWWWWW
        # 7 WWW.WWWWWWW  <- player
        # 8 WWW.WWWWWWW
        # 9 WWW.....WWW  <- branch (into 3x3 room)
        # A WWW.W...WWW
        # B WWW.W...WWW
        # C WWW.WWWWWWW
        # D WWWWWWWWWWW
        level = _FakeLevel(
            "WWWWWWWWWWW\n"
            "WWW.WWWWWWW\n"
            "WWW.WWWWWWW\n"
            "WWW..WWWWWW\n"
            "WWW.WWWWWWW\n"
            "WWW...WWWWW\n"
            "WWW.WWWWWWW\n"
            "WWW.WWWWWWW\n"
            "WWW.WWWWWWW\n"
            "WWW.....WWW\n"
            "WWW.W...WWW\n"
            "WWW.W...WWW\n"
            "WWW.WWWWWWW\n"
            "WWWWWWWWWWW"
        )
        cls, axis = _classify_terrain(level, 3, 7)
        assert cls == 'corridor'
        openings = _scan_corridor_branches(level, 3, 7, axis)

        # Should find exactly 3 openings: one of each type
        alcoves = [o for o in openings if 'alcove' in o]
        nooks = [o for o in openings if 'nook' in o]
        branches = [o for o in openings if 'branch' in o]

        assert len(alcoves) == 1, f"Expected 1 alcove, got {alcoves}"
        assert len(nooks) == 1, f"Expected 1 nook, got {nooks}"
        assert len(branches) == 1, f"Expected 1 branch, got {branches}"

        # All three are east of the corridor
        assert 'east' in alcoves[0]
        assert 'east' in nooks[0]
        assert 'east' in branches[0]

    def test_plus_shaped_junction(self):
        # Plus shape: all 4 cardinal exits open, all 4 diagonals walled
        # This is a fundamental junction pattern — corridors meeting at a point
        #   012
        # 0 W.W
        # 1 ...  <- player at (1,1)
        # 2 W.W
        level = _FakeLevel(
            "W.W\n"
            "...\n"
            "W.W"
        )
        cls, axis = _classify_terrain(level, 1, 1)
        assert cls == 'junction'

    def test_open_room_not_junction(self):
        # Wide open space — diagonals walkable too, so not a junction
        #   0123456
        # 0 WWWWWWW
        # 1 W.....W
        # 2 W.....W  <- player at (3,2)
        # 3 W.....W
        # 4 WWWWWWW
        level = _FakeLevel(
            "WWWWWWW\n"
            "W.....W\n"
            "W.....W\n"
            "W.....W\n"
            "WWWWWWW"
        )
        cls, axis = _classify_terrain(level, 3, 2)
        assert cls == 'open'


class TestRayLength:

    def test_open_corridor(self):
        level = _FakeLevel(
            "W.....W"
        )
        # From (1,0) going east: tiles 2,3,4,5 are walkable = 4
        assert _ray_length(level, 1, 0, 1, 0) == 4

    def test_blocked_immediately(self):
        level = _FakeLevel(
            "W.W"
        )
        # From (1,0) going east: tile (2,0) is wall = 0
        assert _ray_length(level, 1, 0, 1, 0) == 0

    def test_hits_boundary(self):
        level = _FakeLevel(
            "..."
        )
        # From (0,0) going east: tiles 1,2 walkable, then edge = 2
        assert _ray_length(level, 0, 0, 1, 0) == 2


class TestCountExits:

    def test_open_center(self):
        level = _FakeLevel(
            "W.W\n"
            "...\n"
            "W.W"
        )
        assert _count_exits(level, 1, 1) == 4

    def test_dead_end(self):
        level = _FakeLevel(
            "WWW\n"
            "W.W\n"
            "W.W"
        )
        # (1,1): N=wall, E=wall, W=wall, S=walk = 1 exit
        assert _count_exits(level, 1, 1) == 1

    def test_corridor(self):
        level = _FakeLevel(
            "W.W\n"
            "W.W\n"
            "W.W"
        )
        # (1,1): N=walk, S=walk, E=wall, W=wall = 2 exits
        assert _count_exits(level, 1, 1) == 2


# ---- Collapse-tier same-shape merging ----

def _heal_group(target_name, heal_amount, cardinal='east', los=True, distance=5):
    """Build a one-event heal group dict as produced by _build_target_groups."""
    return {
        'target_name': target_name,
        'target_unit': object(),
        'cardinal': cardinal,
        'direction': cardinal,
        'los': los,
        'distance': distance,
        'events': [{'event_type': 'heal', 'heal_amount': heal_amount}],
    }

def _damage_group(target_name, damage, cardinal='east', los=True, distance=5,
                   source_name='Goblin', spell_name='Melee Attack',
                   damage_type='physical'):
    """Build a one-event damage group dict."""
    return {
        'target_name': target_name,
        'target_unit': object(),
        'cardinal': cardinal,
        'direction': cardinal,
        'los': los,
        'distance': distance,
        'events': [{'event_type': 'damage', 'damage': damage,
                    'source_name': source_name, 'spell_name': spell_name,
                    'damage_type': damage_type}],
    }

def _death_group(target_name, cardinal='east', los=True, distance=5,
                 is_expired=False):
    """Build a one-event death group dict."""
    return {
        'target_name': target_name,
        'target_unit': object(),
        'cardinal': cardinal,
        'direction': cardinal,
        'los': los,
        'distance': distance,
        'events': [{'event_type': 'death', 'is_expired': is_expired,
                    'text': f"{target_name} killed"}],
    }


class TestCollectiveCardinal:

    def test_empty_returns_empty(self):
        assert _collective_cardinal([]) == ''

    def test_all_empty_strings_returns_empty(self):
        assert _collective_cardinal(['', '', '']) == ''

    def test_unanimous(self):
        assert _collective_cardinal(['east'] * 10) == 'east'

    def test_strong_majority_wins(self):
        # 8/10 east → 80% ≥ 60% threshold → 'east'
        assert _collective_cardinal(['east'] * 8 + ['west', 'north']) == 'east'

    def test_exact_threshold_wins(self):
        # 6/10 east → exactly 60% → 'east'
        assert _collective_cardinal(['east'] * 6 + ['west'] * 4) == 'east'

    def test_mixed_below_threshold_scattered(self):
        # 5/10 east → 50% < 60% → 'scattered'
        assert _collective_cardinal(['east'] * 5 + ['west'] * 5) == 'scattered'

    def test_ignores_empty_strings_in_denominator(self):
        # Two cardinals, both 'east' — majority is 100% of non-empty entries.
        assert _collective_cardinal(['east', 'east', '', '', '']) == 'east'


class TestMergeSameShapeGroups:

    def test_below_threshold_passes_through(self):
        groups = [_heal_group('Cat', 5) for _ in range(2)]
        result = _merge_same_shape_groups(groups)
        assert len(result) == 2
        assert all('_collective_text' not in g for g in result)

    def test_at_threshold_merges(self):
        groups = [_heal_group('Cat', 5) for _ in range(3)]
        result = _merge_same_shape_groups(groups)
        assert len(result) == 1
        assert result[0]['_collective_text'] == '3 Cats heal 5, east'

    def test_large_group_merges(self):
        groups = [_heal_group('Ghostly Cursed Cat', 5) for _ in range(13)]
        result = _merge_same_shape_groups(groups)
        assert len(result) == 1
        assert result[0]['_collective_text'] == '13 Ghostly Cursed Cats heal 5, east'

    def test_different_amounts_do_not_merge(self):
        groups = ([_heal_group('Cat', 5) for _ in range(3)] +
                  [_heal_group('Cat', 3) for _ in range(3)])
        result = _merge_same_shape_groups(groups)
        assert len(result) == 2
        texts = {g['_collective_text'] for g in result}
        assert texts == {'3 Cats heal 5, east', '3 Cats heal 3, east'}

    def test_different_target_names_do_not_merge(self):
        groups = ([_heal_group('Cat', 5) for _ in range(3)] +
                  [_heal_group('Goblin', 5) for _ in range(3)])
        result = _merge_same_shape_groups(groups)
        assert len(result) == 2
        texts = {g['_collective_text'] for g in result}
        assert texts == {'3 Cats heal 5, east', '3 Goblins heal 5, east'}

    def test_damage_events_merge(self):
        groups = [_damage_group('Cat', 2) for _ in range(5)]
        result = _merge_same_shape_groups(groups)
        assert len(result) == 1
        assert '5 Cats' in result[0]['_collective_text']

    def test_damage_different_amounts_do_not_merge(self):
        groups = [_damage_group('Cat', 2) for _ in range(3)]
        groups.append(_damage_group('Cat', 5))
        result = _merge_same_shape_groups(groups)
        assert len(result) == 2  # 3 merged + 1 passthrough

    def test_death_events_merge(self):
        groups = [_death_group('Cat') for _ in range(4)]
        result = _merge_same_shape_groups(groups)
        assert len(result) == 1
        assert result[0]['_collective_text'] == '4 Cats killed, east'

    def test_death_expired_events_merge(self):
        groups = [_death_group('Cat', is_expired=True) for _ in range(3)]
        result = _merge_same_shape_groups(groups)
        assert len(result) == 1
        assert result[0]['_collective_text'] == '3 Cats expired, east'

    def test_death_killed_and_expired_do_not_merge(self):
        groups = ([_death_group('Cat', is_expired=False) for _ in range(3)] +
                  [_death_group('Cat', is_expired=True) for _ in range(3)])
        result = _merge_same_shape_groups(groups)
        assert len(result) == 2

    def test_multi_event_groups_pass_through(self):
        # A group whose events list already has multiple entries should not be
        # touched — the existing per-target aggregation handles it.
        g = _heal_group('Cat', 5)
        g['events'].append({'event_type': 'heal', 'heal_amount': 5})
        result = _merge_same_shape_groups([g] * 3)
        assert len(result) == 3
        assert all('_collective_text' not in r for r in result)

    def test_mixed_merge_and_passthrough(self):
        # 3 cats (merge) + 1 goblin (passthrough) + 2 dragons (under threshold)
        groups = ([_heal_group('Cat', 5) for _ in range(3)] +
                  [_heal_group('Goblin', 4)] +
                  [_heal_group('Dragon', 7) for _ in range(2)])
        result = _merge_same_shape_groups(groups)
        # Expect: 1 collective (cats) + 1 individual goblin + 2 individual dragons = 4
        assert len(result) == 4
        collectives = [g for g in result if '_collective_text' in g]
        assert len(collectives) == 1
        assert collectives[0]['_collective_text'] == '3 Cats heal 5, east'

    def test_custom_min_count(self):
        groups = [_heal_group('Cat', 5) for _ in range(2)]
        result = _merge_same_shape_groups(groups, min_count=2)
        assert len(result) == 1
        assert result[0]['_collective_text'] == '2 Cats heal 5, east'

    def test_sort_in_los_before_out_of_sight(self):
        groups = ([_heal_group('Cat', 5, los=False) for _ in range(3)] +
                  [_heal_group('Goblin', 5, los=True) for _ in range(3)])
        result = _merge_same_shape_groups(groups)
        assert len(result) == 2
        # In-LoS collective sorts before out-of-LoS collective.
        assert result[0]['los'] is True
        assert result[1]['los'] is False


class TestMakeCollectiveGroup:

    def test_unanimous_cardinal(self):
        bucket = [_heal_group('Cat', 5, cardinal='east') for _ in range(4)]
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '4 Cats heal 5, east'
        assert g['cardinal'] == 'east'

    def test_scattered_cardinal(self):
        bucket = ([_heal_group('Cat', 5, cardinal='east')] * 2 +
                  [_heal_group('Cat', 5, cardinal='west')] * 2)
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '4 Cats heal 5, scattered'
        assert g['cardinal'] == 'scattered'

    def test_missing_cardinal_omits_direction_clause(self):
        bucket = [_heal_group('Cat', 5, cardinal='') for _ in range(3)]
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '3 Cats heal 5'
        assert g['cardinal'] == ''

    def test_any_in_los_marks_group_los_true(self):
        bucket = ([_heal_group('Cat', 5, los=True)] +
                  [_heal_group('Cat', 5, los=False)] * 4)
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['los'] is True

    def test_all_out_of_sight_marks_group_los_false(self):
        bucket = [_heal_group('Cat', 5, los=False) for _ in range(3)]
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['los'] is False

    def test_mean_distance(self):
        bucket = [_heal_group('Cat', 5, distance=d) for d in (2, 4, 6)]
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['distance'] == 4

    def test_target_unit_is_none(self):
        bucket = [_heal_group('Cat', 5) for _ in range(3)]
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['target_unit'] is None

    def test_events_list_cleared(self):
        bucket = [_heal_group('Cat', 5) for _ in range(3)]
        key = ('heal', 'Cat', ('heal', 5))
        g = _make_collective_group(bucket, key)
        assert g['events'] == []

    def test_damage_melee_elided(self):
        bucket = [_damage_group('Cat', 3) for _ in range(4)]
        key = ('damage', 'Cat', ('damage', 'Goblin', 'Melee Attack', 3, 'physical'))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '4 Cats, Goblin 3 physical, east'

    def test_damage_spell_shown(self):
        bucket = [_damage_group('Cat', 5, source_name='Fire Imp',
                                spell_name='Fireball', damage_type='fire')
                  for _ in range(3)]
        key = ('damage', 'Cat', ('damage', 'Fire Imp', 'Fireball', 5, 'fire'))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '3 Cats, Fire Imp Fireball 5, east'

    def test_damage_dtype_shown_when_not_in_spell(self):
        bucket = [_damage_group('Cat', 5, source_name='Fire Imp',
                                spell_name='Annihilate', damage_type='fire')
                  for _ in range(3)]
        key = ('damage', 'Cat', ('damage', 'Fire Imp', 'Annihilate', 5, 'fire'))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '3 Cats, Fire Imp Annihilate 5 fire, east'

    def test_death_killed(self):
        bucket = [_death_group('Wolf') for _ in range(5)]
        key = ('death', 'Wolf', ('death', False))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '5 Wolves killed, east'

    def test_death_expired(self):
        bucket = [_death_group('Skeleton', is_expired=True) for _ in range(3)]
        key = ('death', 'Skeleton', ('death', True))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '3 Skeletons expired, east'

    def test_death_scattered(self):
        bucket = ([_death_group('Wolf', cardinal='north')] * 2 +
                  [_death_group('Wolf', cardinal='south')] * 2)
        key = ('death', 'Wolf', ('death', False))
        g = _make_collective_group(bucket, key)
        assert g['_collective_text'] == '4 Wolves killed, scattered'


# ---- _point_xy ----

class TestPointXY:

    def test_tuple_passthrough(self):
        assert _point_xy((3, 4)) == (3, 4)

    def test_object_with_xy(self):
        class P:
            def __init__(self, x, y):
                self.x = x
                self.y = y
        assert _point_xy(P(7, 9)) == (7, 9)


# ---- _compress_path ----

class TestCompressPath:

    def test_empty_returns_already_at_target(self):
        assert _compress_path([]) == "Already at target."

    def test_none_returns_already_at_target(self):
        assert _compress_path(None) == "Already at target."

    def test_single_point_returns_already_at_target(self):
        assert _compress_path([(0, 0)]) == "Already at target."

    def test_single_step_east(self):
        assert _compress_path([(0, 0), (1, 0)]) == "East, arrive."

    def test_single_step_northeast(self):
        # y- = north in screen coords
        assert _compress_path([(5, 5), (6, 4)]) == "Northeast, arrive."

    def test_single_step_north(self):
        assert _compress_path([(5, 5), (5, 4)]) == "North, arrive."

    def test_run_length_single_direction_east(self):
        # 5 east steps
        path = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0)]
        assert _compress_path(path) == "East 5, arrive."

    def test_run_length_single_direction_southwest(self):
        path = [(5, 5), (4, 6), (3, 7), (2, 8)]
        assert _compress_path(path) == "Southwest 3, arrive."

    def test_multi_segment_leads_with_total(self):
        # 4 NE + 3 N + 5 E = 12 steps
        path = [(0, 10)]
        x, y = 0, 10
        for _ in range(4):
            x += 1; y -= 1
            path.append((x, y))
        for _ in range(3):
            y -= 1
            path.append((x, y))
        for _ in range(5):
            x += 1
            path.append((x, y))
        assert _compress_path(path) == (
            "12 steps. Northeast 4, north 3, east 5, arrive."
        )

    def test_multi_segment_two_runs(self):
        # 2 east, then 3 south
        path = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (2, 3)]
        assert _compress_path(path) == "5 steps. East 2, south 3, arrive."

    def test_unit_target_tail_changes(self):
        path = [(0, 0), (1, 0), (2, 0), (3, 0)]
        assert _compress_path(path, target_kind='unit') == "East 3, arrive adjacent."

    def test_unit_target_single_step(self):
        path = [(0, 0), (1, -1)]
        assert _compress_path(path, target_kind='unit') == "Northeast, arrive adjacent."

    def test_unit_target_multi_segment(self):
        path = [(0, 10)]
        x, y = 0, 10
        for _ in range(4):
            x += 1; y -= 1
            path.append((x, y))
        for _ in range(3):
            y -= 1
            path.append((x, y))
        assert _compress_path(path, target_kind='unit') == (
            "7 steps. Northeast 4, north 3, arrive adjacent."
        )

    def test_zero_delta_steps_skipped(self):
        # Defensive: if for any reason consecutive identical points appear,
        # skip them rather than crashing or emitting empty direction names.
        path = [(0, 0), (0, 0), (1, 0)]
        assert _compress_path(path) == "East, arrive."

    def test_all_zero_deltas_returns_already_at_target(self):
        path = [(3, 3), (3, 3), (3, 3)]
        assert _compress_path(path) == "Already at target."

    def test_accepts_point_like_objects(self):
        class P:
            def __init__(self, x, y):
                self.x = x
                self.y = y
        path = [P(0, 0), P(1, 0), P(2, 0)]
        assert _compress_path(path) == "East 2, arrive."

    def test_diagonal_direction_compress(self):
        # 6 northeast steps
        path = [(0, 6), (1, 5), (2, 4), (3, 3), (4, 2), (5, 1), (6, 0)]
        assert _compress_path(path) == "Northeast 6, arrive."


# ---- _classify_unreachable ----

class TestClassifyUnreachable:

    def test_walkable_target_is_no_route(self):
        # Open 5x5 floor — target tile is walkable. find_path could fail for
        # other reasons (boxed-in pather, separated regions); we still return
        # 'no_route' because the destination itself accepts walkers.
        level = _FakeLevel("""
.....
.....
.....
.....
.....
""")
        assert _classify_unreachable(level, (2, 2)) == 'no_route'

    def test_wall_target_is_impassable(self):
        level = _FakeLevel("""
.....
.WWW.
.W.W.
.WWW.
.....
""")
        assert _classify_unreachable(level, (1, 1)) == 'impassable'

    def test_out_of_bounds_negative_is_impassable(self):
        level = _FakeLevel(".....\n.....\n.....")
        assert _classify_unreachable(level, (-1, 0)) == 'impassable'

    def test_out_of_bounds_too_large_is_impassable(self):
        level = _FakeLevel(".....\n.....\n.....")
        assert _classify_unreachable(level, (10, 10)) == 'impassable'

    def test_target_at_origin_walkable(self):
        level = _FakeLevel(".....\n.....\n.....")
        assert _classify_unreachable(level, (0, 0)) == 'no_route'


# ---- _walkable_neighbors ----

class TestWalkableNeighbors:

    def test_open_room_returns_eight(self):
        level = _FakeLevel("""
.....
.....
.....
.....
.....
""")
        result = _walkable_neighbors(level, (2, 2))
        assert len(result) == 8
        # Center tile (2,2) itself should NOT appear
        assert (2, 2) not in result

    def test_wall_neighbor_excluded(self):
        # Cross shape: only N/S/E/W are walkable around (2, 2)
        level = _FakeLevel("""
WW.WW
WW.WW
.....
WW.WW
WW.WW
""")
        result = _walkable_neighbors(level, (2, 2))
        # Diagonals (1,1),(3,1),(1,3),(3,3) all walls. Cardinals (2,1),(1,2),(3,2),(2,3) walkable.
        assert sorted(result) == sorted([(2, 1), (3, 2), (2, 3), (1, 2)])

    def test_corner_target_excludes_oob(self):
        level = _FakeLevel("""
.....
.....
.....
""")
        result = _walkable_neighbors(level, (0, 0))
        # Only 3 in-bounds neighbors: (1,0),(0,1),(1,1). The other 5 are off-map.
        assert sorted(result) == sorted([(1, 0), (0, 1), (1, 1)])

    def test_completely_walled_target_returns_empty(self):
        level = _FakeLevel("""
WWW
W.W
WWW
""")
        result = _walkable_neighbors(level, (1, 1))
        assert result == []

    def test_returns_deterministic_order(self):
        # Order should be N, NE, E, SE, S, SW, W, NW (clockwise from north).
        level = _FakeLevel("""
.....
.....
.....
.....
.....
""")
        result = _walkable_neighbors(level, (2, 2))
        expected = [(2, 1), (3, 1), (3, 2), (3, 3), (2, 3), (1, 3), (1, 2), (1, 1)]
        assert result == expected
