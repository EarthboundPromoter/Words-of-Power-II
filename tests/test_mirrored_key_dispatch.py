# Tests for the mirrored-key dispatch (cursor-tool pass, slice 1).
#
# The scan keys ride the game's own highlight binds — enemy scan on Highlight
# Enemies (I), ally scan on Highlight Allies (U), landmark scan on Highlight
# Objects (O) — and T/L join the same bind-following dispatch. Rulings pinned
# here (CURSOR_TOOL_UX_PASS.md, owner-ruled 2026-07-06):
# - dispatch consults view.key_binds per press, so the speak/show pairing
#   survives player rebinds by construction (rebind simulation below);
# - an unbound game key ([None, None]) silences the paired scan too — the
#   pairing is total, mirroring the game (the remapping design's collision
#   checker owns warning about it);
# - held keys are SPEECH-SILENT: tap speaks, hold draws. The held-highlight
#   speech channel (Layer 1b) was built and CUT at the owner's ruling — a tap
#   is physically a short hold, so it double-spoke on every scan tap. No mod
#   code may hang speech off the held-key draw path;
# - the mirrored keys are never consumed: the game's held-key draw polls and
#   the mod's tap speech share the physical key by design.

import sys
import types
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
GAME = MOD.parents[1]
for p in (str(GAME), str(MOD)):
    if p not in sys.path:
        sys.path.insert(0, p)

sys.modules.setdefault('steamworks', types.ModuleType('steamworks'))
import Game  # noqa: F401  (resolves the Level<->Game import cycle)
import Level

from helpers import _bound_keys, _key_matches_bind

_src = (MOD / "screen_reader.py").read_text(encoding="utf-8")


# ---- bind-following dispatch predicate (helpers) ----

K_I, K_U, K_O, K_F5 = 105, 117, 111, 286  # keycode stand-ins; values are opaque


def test_default_bind_matches():
    key_binds = {7: [K_I, None]}
    assert _key_matches_bind(key_binds, 7, K_I, K_I)
    assert not _key_matches_bind(key_binds, 7, K_U, K_I)


def test_rebind_simulation_follows_the_table_not_the_default():
    # Player rebinds Highlight Enemies to F5: the scan must move with it.
    key_binds = {7: [K_F5, None]}
    assert _key_matches_bind(key_binds, 7, K_F5, K_I)
    assert not _key_matches_bind(key_binds, 7, K_I, K_I)


def test_secondary_bind_slot_also_matches():
    key_binds = {7: [K_I, K_F5]}
    assert _key_matches_bind(key_binds, 7, K_I, K_I)
    assert _key_matches_bind(key_binds, 7, K_F5, K_I)


def test_fallback_used_when_table_or_id_unavailable():
    assert _key_matches_bind(None, 7, K_I, K_I)          # no table
    assert _key_matches_bind({}, 7, K_I, K_I)            # id missing
    assert _key_matches_bind({7: [K_I]}, None, K_I, K_I)  # no id resolved


def test_unbound_game_key_silences_the_paired_scan():
    # [None, None] = the player unbound the game key; the game can't draw the
    # highlight, so the paired scan goes silent too (pairing is total).
    key_binds = {7: [None, None]}
    assert _bound_keys(key_binds, 7, K_I) == ()
    assert not _key_matches_bind(key_binds, 7, K_I, K_I)


def test_bound_keys_filters_none_slots():
    assert _bound_keys({7: [K_I, None]}, 7, K_U) == (K_I,)
    assert _bound_keys(None, 7, K_U) == (K_U,)


# ---- source pins: dispatch shape ----

def _hotkey_loop_src():
    start = _src.index("        try:\n            for evt in self.events:")
    end = _src.index('            log(f"[Hotkey] Error: {e}")', start)
    return _src[start:end]


def test_mirrored_keys_dispatch_by_bind_not_keycode():
    loop = _hotkey_loop_src()
    for bind_id in ('_KB_HL_ENEMIES', '_KB_HL_ALLIES', '_KB_HL_OBJECTS',
                    '_KB_LOS', '_KB_THREAT'):
        assert f"_is_bind(self, {bind_id}, evt.key" in loop, bind_id
    # The old hardcoded scan keycodes must be gone from dispatch.
    for dead in ('== pygame.K_j', '== pygame.K_y', '== pygame.K_q'):
        assert dead not in loop, dead


def test_scan_resets_follow_the_same_binds():
    start = _src.index("# Reset scan cycling on keys that aren't")
    end = _src.index("_ally_scanner.reset()", start)
    resets = _src[start:end]
    assert "_is_bind(self, _KB_HL_ENEMIES" in resets
    assert "_is_bind(self, _KB_HL_OBJECTS" in resets
    assert "_is_bind(self, _KB_HL_ALLIES" in resets


def test_mirrored_keys_are_not_consumed():
    # The game's held-key draws and the mod's tap speech share the physical
    # key; the mod consumes only its four known collision keys (Tab under
    # Shift, P = the game's pdb dev cheat, F1 and Slash = help). A fifth
    # consumption site appearing in the hotkey loop means someone consumed a
    # mirrored key — fail loudly.
    loop = _hotkey_loop_src()
    assert loop.count("self.events = [e for e in self.events") == 4


def test_held_key_draw_path_stays_speech_silent():
    # Owner ruling (2026-07-06, CURSOR_TOOL_UX_PASS.md Layer 1b): held keys
    # are speech-silent — tap speaks, hold draws. The held-highlight speech
    # channel was built and CUT because a tap is physically a short hold and
    # double-spoke on every scan tap. Pin the cut: the mod must never wrap
    # the game's highlight_examine_override.
    assert "_PyGameView.highlight_examine_override" not in _src
