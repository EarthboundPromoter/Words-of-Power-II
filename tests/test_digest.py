# Tests for pure functions in digest.py
# Run with: cd ~ && python -m pytest "<path_to_mod>/tests/test_digest.py" -v

import sys
import os

# Add the mod directory to Python's import path so we can find digest.py
mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

# Importing digest pulls journal as a side-effect. Journal's import is
# safe — it doesn't install hooks until install_hooks() is called.
from digest import (
    DIGEST_MARK,
    _claim_chain,
    _format_cast_list,
    _is_empty_autofire,
    build_record_index,
    compose_buffs_applied_section,
    compose_cast_section,
    compose_debuffs_applied_section,
    compose_digest,
    compose_killed_section,
    compose_moved_section,
    compose_shields_granted_section,
    compose_shields_stripped_section,
    compose_team_changes_section,
    compose_side_section,
    compose_spawned_section,
    compose_surviving_section,
    find_all_pending_roots,
    find_pending_root,
    gather_chain_events,
    is_player_keypress_cast,
    walk_to_keypress_root,
    _build_target_hits,
    _format_streamlined_side,
)


# ---- Helpers for constructing fake journal records ----

# Canonical wizard ID used across factories so cast_begin caster snapshots
# match _wizard_snap targets (Side section's heal aggregation matches by
# id). Production code reads id from real journal records; tests need a
# stable shared value.
_WIZARD_ID = 100


def _wizard_caster_snap():
    """Caster snapshot for the wizard — used by cast_begin factories so
    tests match journal.py's real cast_begin payload shape (caster
    field is always populated by the snapshot helper). Wizard is
    is_player_controlled=True, team=0."""
    return {
        'id': _WIZARD_ID,
        'name': 'Wizard',
        'x': 10,
        'y': 10,
        'cur_hp': 50,
        'max_hp': 50,
        'shields': 0,
        'team': 0,
        'tier': 'wizard',
        'is_player_controlled': True,
        'is_boss': False,
        'is_lair': False,
        'parent_id': None,
    }


def _enemy_caster_snap(name="Goblin", x=5, y=5):
    """Caster snapshot for an enemy unit."""
    return {
        'id': 555,
        'name': name,
        'x': x,
        'y': y,
        'cur_hp': 7,
        'max_hp': 7,
        'shields': 0,
        'team': 1,
        'tier': 'minion',
        'is_player_controlled': False,
        'is_boss': False,
        'is_lair': False,
        'parent_id': None,
    }


def _player_cast(seq, spell_name="Magic Missile"):
    """Fake cast_begin record for a real player keypress — pay_costs=True.
    Payload shape mirrors journal.py's cast_begin builder: spell info
    under `spell` (a snapshot dict), is_player + pay_costs at top,
    caster snapshot included."""
    return {
        'sequence': seq,
        'parent': None,
        'event_type': 'cast_begin',
        'payload': {
            'caster': _wizard_caster_snap(),
            'spell': {'name': spell_name, 'cur_charges': 1, 'max_charges': 1},
            'is_player': True,
            'pay_costs': True,
        },
        'marks': [],
    }


def _channel_continuation(seq, spell_name="Fan of Flames"):
    """Fake cast_begin for a channel continuation tick — synthetic record
    emitted by journal's ChannelBuff.on_advance hook. is_player=True,
    pay_costs=True (player keypress-equivalent), is_channel_continuation=True
    (verb dispatch flag)."""
    return {
        'sequence': seq,
        'parent': None,
        'event_type': 'cast_begin',
        'payload': {
            'caster': _wizard_caster_snap(),
            'spell': {'name': spell_name, 'cur_charges': 1, 'max_charges': 1},
            'is_player': True,
            'pay_costs': True,
            'is_channel_continuation': True,
        },
        'marks': [],
    }


def _player_autocast(seq, spell_name="Combust Poison"):
    """Fake cast_begin for a passive auto-cast on the wizard — caster
    is player-controlled but pay_costs=False (e.g., Explosive Spore
    Manual end-of-turn tick). Under the post-2026-05-08 policy, these
    ARE chain roots — empty ones are silenced inside compose_digest,
    effective ones still narrate. See _is_empty_autofire for the
    silence rule and feedback_capture_separate_from_render.md for the
    capture-vs-render principle."""
    return {
        'sequence': seq,
        'parent': None,
        'event_type': 'cast_begin',
        'payload': {
            'caster': _wizard_caster_snap(),
            'spell': {'name': spell_name, 'cur_charges': 3, 'max_charges': 3},
            'is_player': True,
            'pay_costs': False,
        },
        'marks': [],
    }


def _proc_cast(seq, parent_seq, spell_name="Magic Missile"):
    """Fake cast_begin record for a chain-spawned cast (Multimancy proc, etc.).
    Procs typically use pay_costs=False (free recasts) but the parent
    check filters them before pay_costs even matters."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'cast_begin',
        'payload': {
            'caster': _wizard_caster_snap(),
            'spell': {'name': spell_name, 'cur_charges': 1, 'max_charges': 1},
            # Note: is_player is True even for procs because Multimancy fires
            # via the player wizard. The journal records is_player based on
            # the casting unit, which is still the wizard for procs. The
            # distinguishing fact is that procs have a non-None parent.
            'is_player': True,
            'pay_costs': False,
        },
        'marks': [],
    }


def _enemy_cast(seq, spell_name="Dark Bolt"):
    """Fake cast_begin record for an enemy-controlled cast."""
    return {
        'sequence': seq,
        'parent': None,
        'event_type': 'cast_begin',
        'payload': {
            'caster': _enemy_caster_snap(),
            'spell': {'name': spell_name, 'cur_charges': 1, 'max_charges': 1},
            'is_player': False,
            'pay_costs': True,
        },
        'marks': [],
    }


def _damage_event(seq, parent_seq, target="Grey Gorgon", amount=65):
    """Fake EventOnDamaged record (lean variant for tests that only
    exercise causation walking — pre-enrichment payload shape)."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnDamaged',
        'payload': {
            'unit': target,
            'damage': amount,
        },
        'marks': [],
    }


# ---- Rich factories for Killed/Surviving section tests ----
# These match the post-enrichment journal payload shape with full target
# snapshots, source attribution, and resisted derivation.

def _target_snap(unit_id, name="Goblin", x=5, y=5, cur_hp=0, max_hp=7,
                 tier="minion", team=1):
    """Build a target snapshot dict matching journal.py's _snapshot_unit."""
    return {
        'id': unit_id,
        'name': name,
        'x': x,
        'y': y,
        'cur_hp': cur_hp,
        'max_hp': max_hp,
        'shields': 0,
        'team': team,
        'tier': tier,
        'is_player_controlled': tier == 'wizard',
        'is_boss': tier == 'boss',
        'is_lair': tier == 'spawner',
        'parent_id': None,
    }


def _pre_damage(seq, parent_seq, target_snap, spell="Magic Missile",
                dtype="Arcane", dmg_pre=65, dmg_post=65, resisted=None):
    """Fake EventOnPreDamaged record. Resisted defaults to (dmg_pre > dmg_post)."""
    if resisted is None:
        resisted = dmg_pre > dmg_post
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnPreDamaged',
        'payload': {
            'target': target_snap,
            'damage_pre_resist': dmg_pre,
            'damage_post_resist': dmg_post,
            'resisted': resisted,
            'damage_type': dtype,
            'source_name': spell,
            'source_unit_id': 999,
        },
        'marks': [],
    }


def _damage_full(seq, parent_seq, target_snap, spell="Magic Missile",
                 dtype="Arcane", damage=65):
    """Fake EventOnDamaged record with full target snapshot."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnDamaged',
        'payload': {
            'target': target_snap,
            'damage': damage,
            'damage_type': dtype,
            'source_name': spell,
            'source_unit_id': 999,
        },
        'marks': [],
    }


def _death(seq, parent_seq, target_snap, killing_damage=65,
           killing_dtype="Arcane", killing_source="Magic Missile"):
    """Fake EventOnDeath record."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnDeath',
        'payload': {
            'target': target_snap,
            'killing_damage': killing_damage,
            'killing_dtype': killing_dtype,
            'killing_source': killing_source,
        },
        'marks': [],
    }


def _shield_removed(seq, parent_seq, target_snap):
    """Fake EventOnShieldRemoved record."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnShieldRemoved',
        'payload': {
            'target': target_snap,
        },
        'marks': [],
    }


def _hit_then_die(seq_start, parent_seq, target_snap, spell="Magic Missile",
                  dtype="Arcane", dmg_pre=65, dmg_post=65):
    """Convenience: produce the [PreDamaged, Damaged, Death] sequence for
    a single-hit kill, returning the records and the next free sequence."""
    pre = _pre_damage(seq_start, parent_seq, target_snap, spell, dtype, dmg_pre, dmg_post)
    dmg = _damage_full(seq_start + 1, parent_seq, target_snap, spell, dtype, dmg_post)
    death = _death(seq_start + 2, parent_seq, target_snap, dmg_post, dtype, spell)
    return [pre, dmg, death], seq_start + 3


def _unit_added(seq, parent_seq, unit_snap):
    """Fake EventOnUnitAdded record."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnUnitAdded',
        'payload': {
            'unit': unit_snap,
        },
        'marks': [],
    }


def _wizard_moved(seq, parent_seq, x, y, teleport=True, is_player=True):
    """Fake EventOnMoved record for the wizard's own in-chain teleport.
    Payload shape mirrors journal._payload_moved: unit snapshot (x/y are
    the destination) plus the teleport flag."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnMoved',
        'payload': {
            'unit': {
                'id': _WIZARD_ID,
                'name': 'Wizard',
                'x': x,
                'y': y,
                'is_player_controlled': is_player,
                'tier': 'wizard' if is_player else 'minion',
            },
            'teleport': teleport,
        },
        'marks': [],
    }


# ---- compose_moved_section (wizard in-chain self-teleport) ----

def test_moved_section_renders_wizard_teleport():
    chain = [_player_cast(1, "Blink"), _wizard_moved(2, 1, 8, 12)]
    assert compose_moved_section(chain) == "Teleported to (8,12)."


def test_moved_section_empty_without_teleport():
    # A normal in-chain step (teleport=False) is noise, never rendered.
    chain = [_player_cast(1, "Lightning Bolt"),
             _wizard_moved(2, 1, 8, 12, teleport=False)]
    assert compose_moved_section(chain) == ""


def test_moved_section_empty_with_no_move():
    chain = [_player_cast(1, "Magic Missile")]
    assert compose_moved_section(chain) == ""


def test_moved_section_ignores_nonwizard_move():
    # Enemies relocated inside the wizard's chain (e.g. an Ice Vortex pull)
    # are out of scope for this section — wizard-only.
    chain = [_player_cast(1, "Ice Vortex"),
             _wizard_moved(2, 1, 3, 3, is_player=False)]
    assert compose_moved_section(chain) == ""


def test_moved_section_collapses_multistep_to_final():
    # A multi-step self-pull fires one EventOnMoved per tile; only the
    # final destination is spoken.
    chain = [
        _player_cast(1, "Lightning Form"),
        _wizard_moved(2, 1, 10, 7),
        _wizard_moved(3, 1, 8, 9),
        _wizard_moved(4, 1, 5, 12),
    ]
    assert compose_moved_section(chain) == "Teleported to (5,12)."


def test_compose_digest_appends_teleport_clause_no_damage():
    # The Lightning Form bug: a Lightning Bolt that hit nothing but
    # relocated the wizard must report BOTH the empty hit and the move.
    chain = [_player_cast(1, "Lightning Bolt"), _wizard_moved(2, 1, 13, 4)]
    out = compose_digest(chain)
    assert out == "Cast Lightning Bolt. No damage. Teleported to (13,4)."


def test_compose_digest_appends_teleport_clause_after_kills():
    # T59-style: the bolt killed a target AND teleported the wizard. The
    # relocation trails the outcome sections.
    tgt = _target_snap(901, name="Satyr", cur_hp=0, max_hp=19)
    hits, _ = _hit_then_die(2, 1, tgt, spell="Lightning Bolt",
                            dtype="Lightning", dmg_pre=12, dmg_post=12)
    chain = [_player_cast(1, "Lightning Bolt"), *hits,
             _wizard_moved(9, 1, 5, 12)]
    out = compose_digest(chain)
    assert out.startswith("Cast Lightning Bolt.")
    assert out.endswith("Teleported to (5,12).")
    assert "killed" in out


# ---- is_player_keypress_cast ----

def test_is_player_keypress_cast_yes():
    assert is_player_keypress_cast(_player_cast(1)) is True


def test_is_player_keypress_cast_no_when_enemy():
    assert is_player_keypress_cast(_enemy_cast(1)) is False


def test_is_player_keypress_cast_no_when_damage_event():
    assert is_player_keypress_cast(_damage_event(1, None)) is False


def test_is_player_keypress_cast_handles_none():
    assert is_player_keypress_cast(None) is False


def test_is_player_keypress_cast_handles_missing_payload():
    rec = {'sequence': 1, 'event_type': 'cast_begin'}  # no payload key
    assert is_player_keypress_cast(rec) is False


def test_is_player_keypress_cast_includes_autocasts():
    """Passive auto-cast on wizard (e.g., Combust Poison from Explosive
    Spore Manual at end of turn) has is_player=True and pay_costs=False.
    Under the post-2026-05-08 policy these ARE chain roots — empty ones
    are silenced inside compose_digest, effective ones still narrate.
    See feedback_capture_separate_from_render.md."""
    autocast = _player_autocast(1)
    assert is_player_keypress_cast(autocast) is True


def test_is_player_keypress_cast_defaults_pay_costs_to_true_when_missing():
    """For test fixtures that predate the pay_costs schema field,
    treat missing pay_costs as True (legacy behavior preserved)."""
    rec = {
        'sequence': 1,
        'parent': None,
        'event_type': 'cast_begin',
        'payload': {
            'spell': {'name': 'Magic Missile'},
            'is_player': True,
            # no pay_costs key
        },
        'marks': [],
    }
    assert is_player_keypress_cast(rec) is True


def test_is_player_keypress_cast_filters_walk():
    """The 'walk' spell is a player-controlled, pay_costs=True cast,
    but it's not a combat decision — autowalk fires it through act_cast
    at level end / through cleared levels. Filtered by spell name."""
    walk = _player_cast(1, spell_name="walk")
    assert is_player_keypress_cast(walk) is False


def test_is_player_keypress_cast_filters_utility_items():
    """Mana Potion, Healing Potion, Teleporter fire through act_cast as
    'spells' but aren't combat decisions worth digesting. Heal info
    comes through the legacy batcher's heal handler."""
    for name in ("Mana Potion", "Healing Potion", "Teleporter"):
        rec = _player_cast(1, spell_name=name)
        assert is_player_keypress_cast(rec) is False, (
            f"{name} should be filtered from digest"
        )


def test_is_player_keypress_cast_does_not_filter_damage_items():
    """Death Dice deals damage and is genuinely useful in the digest
    (e.g., 6-target cascade summary)."""
    rec = _player_cast(1, spell_name="Death Dice")
    assert is_player_keypress_cast(rec) is True


def test_find_pending_root_skips_walk_casts():
    """A walk cast is not a pending root — find_pending_root returns
    None or the more recent real keypress, never the walk."""
    walk = _player_cast(1, spell_name="walk")
    assert find_pending_root([walk], None) is None

    real = _player_cast(3, spell_name="Magic Missile")
    # Even with walk later in the chain, it's filtered out
    walk_later = _player_cast(5, spell_name="walk")
    records = [real, walk_later]
    assert find_pending_root(records, None) is real


# ---- build_record_index ----

def test_build_record_index_simple():
    records = [
        _player_cast(1),
        _damage_event(2, 1),
        _damage_event(3, 1),
    ]
    idx = build_record_index(records)
    assert idx[1]['event_type'] == 'cast_begin'
    assert idx[2]['payload']['unit'] == 'Grey Gorgon'
    assert idx[3]['parent'] == 1


def test_build_record_index_skips_records_without_sequence():
    records = [
        _player_cast(1),
        {'event_type': 'malformed'},  # no sequence
        _damage_event(3, 1),
    ]
    idx = build_record_index(records)
    assert 1 in idx
    assert 3 in idx
    assert len(idx) == 2


# ---- walk_to_keypress_root ----

def test_walk_finds_root_from_self():
    """Walking from a player-keypress record returns itself."""
    cast = _player_cast(1)
    idx = build_record_index([cast])
    root = walk_to_keypress_root(cast, idx)
    assert root is cast


def test_walk_finds_root_from_direct_child():
    """Damage event whose parent is a player keypress finds the keypress."""
    cast = _player_cast(1)
    dmg = _damage_event(2, parent_seq=1)
    idx = build_record_index([cast, dmg])
    root = walk_to_keypress_root(dmg, idx)
    assert root is cast


def test_walk_finds_root_through_proc_cast():
    """Multimancy-style chain: player cast -> proc cast -> damage event.
    The damage's root should still be the player cast."""
    player_cast = _player_cast(1)
    proc = _proc_cast(2, parent_seq=1)
    dmg = _damage_event(3, parent_seq=2)
    idx = build_record_index([player_cast, proc, dmg])
    # Walk from damage event
    root = walk_to_keypress_root(dmg, idx)
    assert root is player_cast


def test_walk_finds_root_through_deep_chain():
    """Deeper chain: player cast -> proc -> proc -> proc -> damage."""
    player_cast = _player_cast(1)
    proc1 = _proc_cast(2, parent_seq=1)
    proc2 = _proc_cast(3, parent_seq=2)
    proc3 = _proc_cast(4, parent_seq=3)
    dmg = _damage_event(5, parent_seq=4)
    idx = build_record_index([player_cast, proc1, proc2, proc3, dmg])
    root = walk_to_keypress_root(dmg, idx)
    assert root is player_cast


def test_walk_returns_none_for_orphan():
    """Damage event with no parent returns None (no player keypress in lineage)."""
    dmg = _damage_event(1, parent_seq=None)
    idx = build_record_index([dmg])
    root = walk_to_keypress_root(dmg, idx)
    assert root is None


def test_walk_returns_none_for_enemy_chain():
    """Damage from an enemy cast returns None (no player keypress in lineage)."""
    enemy = _enemy_cast(1)
    dmg = _damage_event(2, parent_seq=1)
    idx = build_record_index([enemy, dmg])
    root = walk_to_keypress_root(dmg, idx)
    assert root is None


def test_walk_returns_none_when_parent_missing_from_index():
    """Parent reference points to a record not in the index (defensive)."""
    dmg = _damage_event(1, parent_seq=99)
    idx = build_record_index([dmg])
    root = walk_to_keypress_root(dmg, idx)
    assert root is None


def test_walk_handles_none_input():
    """Walking from None record returns None."""
    assert walk_to_keypress_root(None, {}) is None


def test_walk_protects_against_cycles():
    """Pathological cycle in parent links shouldn't infinite-loop."""
    a = {'sequence': 1, 'parent': 2, 'event_type': 'EventOnDamaged',
         'payload': {}, 'marks': []}
    b = {'sequence': 2, 'parent': 1, 'event_type': 'EventOnDamaged',
         'payload': {}, 'marks': []}
    idx = build_record_index([a, b])
    # Cycle: a.parent -> b, b.parent -> a. No player keypress reachable.
    root = walk_to_keypress_root(a, idx)
    assert root is None


def test_walk_skips_intermediate_player_casts():
    """Multimancy procs are player-controlled casts (is_player=True) but
    have a parent — they're not keypress roots. The walk continues past
    them to the actual chain root (parent=None)."""
    keypress = _player_cast(1)              # parent=None, the real root
    proc_with_player_flag = _player_cast(2) # is_player=True but has parent
    proc_with_player_flag['parent'] = 1     # makes it an intermediate cast
    dmg = _damage_event(3, parent_seq=2)
    idx = build_record_index([keypress, proc_with_player_flag, dmg])
    root = walk_to_keypress_root(dmg, idx)
    # Walk: dmg(3) -> proc(2, has parent) -> keypress(1, parent=None) -> return.
    assert root is keypress


# ---- find_pending_root ----

def test_find_pending_root_empty_records():
    assert find_pending_root([], None) is None


def test_find_pending_root_no_keypress_in_records():
    """Only enemy casts and damage events; no player keypress."""
    records = [_enemy_cast(1), _damage_event(2, parent_seq=1)]
    assert find_pending_root(records, None) is None


def test_find_pending_root_single_undigested_keypress():
    cast = _player_cast(1)
    records = [cast, _damage_event(2, parent_seq=1)]
    assert find_pending_root(records, None) is cast


def test_find_pending_root_returns_none_when_already_digested():
    """Last digested seq matches the only candidate's seq; nothing pending."""
    cast = _player_cast(1)
    records = [cast, _damage_event(2, parent_seq=1)]
    assert find_pending_root(records, 1) is None


def test_find_pending_root_picks_most_recent_when_multiple_undigested():
    older = _player_cast(1, spell_name="Magic Missile")
    newer = _player_cast(5, spell_name="Fireball")
    records = [older, _damage_event(2, parent_seq=1),
               newer, _damage_event(6, parent_seq=5)]
    assert find_pending_root(records, None) is newer


def test_find_pending_root_returns_newer_when_older_already_digested():
    older = _player_cast(1)
    newer = _player_cast(5, spell_name="Fireball")
    records = [older, newer]
    assert find_pending_root(records, 1) is newer


def test_find_pending_root_ignores_proc_casts():
    """Proc casts have is_player=True but parent is set; not keypress roots.
    The keypress is the only valid pending root even though the proc has
    a higher sequence."""
    keypress = _player_cast(1)
    proc = _proc_cast(2, parent_seq=1, spell_name="Multimancy")
    records = [keypress, proc, _damage_event(3, parent_seq=2)]
    assert find_pending_root(records, None) is keypress


def test_find_pending_root_ignores_enemy_casts():
    """Enemy cast_begin has parent=None but is_player=False."""
    enemy = _enemy_cast(1)
    records = [enemy, _damage_event(2, parent_seq=1)]
    assert find_pending_root(records, None) is None


def test_find_pending_root_skips_records_without_sequence():
    """Defensive: malformed record without a sequence is ignored."""
    bad = {'event_type': 'cast_begin', 'parent': None,
           'payload': {'is_player': True, 'pay_costs': True}, 'marks': []}  # no sequence
    good = _player_cast(3)
    records = [bad, good]
    assert find_pending_root(records, None) is good


def test_find_pending_root_picks_latest_when_autocast_follows_keypress():
    """When a real keypress is followed by a passive autocast in the
    same window, find_pending_root picks the latest by sequence (the
    autocast). Both are valid roots under the post-2026-05-08 policy;
    the empty-autofire silence inside compose_digest decides whether
    the autocast actually speaks. find_all_pending_roots returns both
    so the multi-root path narrates them in order."""
    keypress = _player_cast(3, spell_name='Fireball')
    autocast_later = _player_autocast(7, spell_name='Combust Poison')
    records = [keypress, autocast_later]
    assert find_pending_root(records, None) is autocast_later


def test_find_pending_root_returns_autocast_when_only_autocasts_pending():
    """A passive autocast is a valid pending root under the post-
    2026-05-08 policy — empty ones get silenced at compose time, not
    pre-filtered here."""
    autocast = _player_autocast(5)
    assert find_pending_root([autocast], None) is autocast


def test_walk_to_keypress_root_returns_autocast_root():
    """A damage event whose lineage roots in a passive auto-cast
    returns that autocast as the root — autocasts are valid chain
    roots under the post-2026-05-08 policy."""
    autocast = _player_autocast(1)
    dmg = _damage_event(2, parent_seq=1)
    idx = build_record_index([autocast, dmg])
    root = walk_to_keypress_root(dmg, idx)
    assert root is autocast


def test_find_pending_root_finds_channel_continuation():
    """A synthetic channel-tick cast_begin is treated as a keypress root —
    is_player=True and pay_costs=True both pass the keypress filter."""
    tick = _channel_continuation(1, spell_name='Wheel of Death')
    assert find_pending_root([tick], None) is tick


def test_walk_to_keypress_root_through_channel_continuation():
    """Damage events spawned by the channel tick walk to the synthetic
    channel-continuation cast_begin as their keypress root."""
    tick = _channel_continuation(1, spell_name='Fan of Flames')
    dmg = _damage_event(2, parent_seq=1)
    idx = build_record_index([tick, dmg])
    root = walk_to_keypress_root(dmg, idx)
    assert root is tick


def test_is_player_keypress_cast_accepts_channel_continuation():
    """Channel-continuation flag is orthogonal to keypress detection —
    is_player=True and pay_costs=True still classify it as a keypress."""
    tick = _channel_continuation(1)
    assert is_player_keypress_cast(tick) is True


# ---- gather_chain_events ----

def test_gather_chain_events_none_root():
    cast = _player_cast(1)
    assert gather_chain_events([cast], None) == []


def test_gather_chain_events_root_only():
    """Root with no descendants returns just the root."""
    cast = _player_cast(1)
    chain = gather_chain_events([cast], cast)
    assert chain == [cast]


def test_gather_chain_events_root_plus_direct_damage():
    cast = _player_cast(1)
    dmg = _damage_event(2, parent_seq=1)
    chain = gather_chain_events([cast, dmg], cast)
    assert chain == [cast, dmg]


def test_gather_chain_events_includes_proc_descendants():
    """Multimancy chain: keypress -> proc -> damage from proc."""
    keypress = _player_cast(1)
    proc = _proc_cast(2, parent_seq=1, spell_name="Multimancy")
    dmg = _damage_event(3, parent_seq=2)
    chain = gather_chain_events([keypress, proc, dmg], keypress)
    assert chain == [keypress, proc, dmg]


def test_gather_chain_events_excludes_unrelated_records():
    """Records from a different chain are not included."""
    keypress = _player_cast(1)
    own_dmg = _damage_event(2, parent_seq=1)
    enemy = _enemy_cast(3)
    enemy_dmg = _damage_event(4, parent_seq=3)
    chain = gather_chain_events([keypress, own_dmg, enemy, enemy_dmg], keypress)
    assert chain == [keypress, own_dmg]


def test_gather_chain_events_excludes_other_player_chain():
    """Two player keypresses; gathering from one excludes the other."""
    keypress_a = _player_cast(1, spell_name="Magic Missile")
    dmg_a = _damage_event(2, parent_seq=1)
    keypress_b = _player_cast(3, spell_name="Fireball")
    dmg_b = _damage_event(4, parent_seq=3)
    records = [keypress_a, dmg_a, keypress_b, dmg_b]
    chain_b = gather_chain_events(records, keypress_b)
    assert chain_b == [keypress_b, dmg_b]


def test_gather_chain_events_preserves_sequence_order():
    """Output is in the same order as the journal (monotonic sequence)."""
    keypress = _player_cast(1)
    dmg1 = _damage_event(2, parent_seq=1, target="Wolf")
    proc = _proc_cast(3, parent_seq=1, spell_name="Multimancy")
    dmg2 = _damage_event(4, parent_seq=3, target="Goblin")
    records = [keypress, dmg1, proc, dmg2]
    chain = gather_chain_events(records, keypress)
    assert [r['sequence'] for r in chain] == [1, 2, 3, 4]


def test_gather_chain_events_root_without_sequence():
    """Defensive: root missing 'sequence' returns empty list."""
    bad_root = {'event_type': 'cast_begin', 'parent': None,
                'payload': {'is_player': True}, 'marks': []}
    other = _player_cast(2)
    assert gather_chain_events([bad_root, other], bad_root) == []


def test_gather_chain_events_handles_deep_chain():
    """Deep proc-spawned-proc chain — every depth is gathered."""
    keypress = _player_cast(1)
    p1 = _proc_cast(2, parent_seq=1)
    p2 = _proc_cast(3, parent_seq=2)
    p3 = _proc_cast(4, parent_seq=3)
    dmg = _damage_event(5, parent_seq=4)
    records = [keypress, p1, p2, p3, dmg]
    chain = gather_chain_events(records, keypress)
    assert chain == [keypress, p1, p2, p3, dmg]


# ---- _format_cast_list ----

def test_format_cast_list_empty():
    assert _format_cast_list([]) == ""


def test_format_cast_list_single():
    assert _format_cast_list(['Magic Missile']) == 'Magic Missile'


def test_format_cast_list_two_distinct():
    assert _format_cast_list(['Blink', 'Disperse']) == 'Blink, Disperse'


def test_format_cast_list_consecutive_run_collapses():
    assert _format_cast_list(['Magic Missile', 'Magic Missile']) == 'Magic Missile times 2'


def test_format_cast_list_burst_a_pattern():
    """Blink, Disperse, Magic Missile, Magic Missile -> standard burst form."""
    spells = ['Blink', 'Disperse', 'Magic Missile', 'Magic Missile']
    assert _format_cast_list(spells) == 'Blink, Disperse, Magic Missile times 2'


def test_format_cast_list_long_run():
    """16 consecutive Magic Missiles collapses to times 16."""
    spells = ['Magic Missile'] * 16
    assert _format_cast_list(spells) == 'Magic Missile times 16'


def test_format_cast_list_non_consecutive_repeats_dont_collapse():
    """Same spell separated by a different one stays as separate entries."""
    spells = ['Magic Missile', 'Blink', 'Magic Missile']
    assert _format_cast_list(spells) == 'Magic Missile, Blink, Magic Missile'


def test_format_cast_list_multiple_runs():
    """Multiple distinct runs each collapse independently."""
    spells = ['Magic Missile', 'Magic Missile', 'Blink', 'Fireball', 'Fireball', 'Fireball']
    assert _format_cast_list(spells) == 'Magic Missile times 2, Blink, Fireball times 3'


# ---- compose_cast_section ----

def test_compose_cast_section_empty_chain():
    assert compose_cast_section([]) == ""


def test_compose_cast_section_no_cast_records():
    """Chain with only damage events (degenerate) produces no cast string."""
    chain = [_damage_event(1, parent_seq=None)]
    assert compose_cast_section(chain) == ""


def test_compose_cast_section_single_cast():
    chain = [_player_cast(1, spell_name='Magic Missile')]
    assert compose_cast_section(chain) == 'Cast Magic Missile.'


def test_compose_cast_section_burst_a():
    """Burst A: keypress Blink + procs Disperse + 2x Magic Missile."""
    chain = [
        _player_cast(1, spell_name='Blink'),
        _proc_cast(2, parent_seq=1, spell_name='Disperse'),
        _proc_cast(3, parent_seq=1, spell_name='Magic Missile'),
        _proc_cast(4, parent_seq=1, spell_name='Magic Missile'),
    ]
    assert compose_cast_section(chain) == 'Cast Blink, Disperse, Magic Missile times 2.'


def test_compose_cast_section_ignores_non_cast_records():
    """Damage events between casts don't affect the cast list (interleaving
    of singular events lands in a follow-up commit)."""
    chain = [
        _player_cast(1, spell_name='Magic Missile'),
        _damage_event(2, parent_seq=1),
        _proc_cast(3, parent_seq=1, spell_name='Magic Missile'),
    ]
    assert compose_cast_section(chain) == 'Cast Magic Missile times 2.'


def test_compose_cast_section_skips_cast_without_spell_name():
    """Defensive: cast record with missing/empty spell name is skipped."""
    bad_cast = {
        'sequence': 2,
        'parent': 1,
        'event_type': 'cast_begin',
        'payload': {'is_player': True, 'spell': {'name': None}},
        'marks': [],
    }
    chain = [
        _player_cast(1, spell_name='Magic Missile'),
        bad_cast,
    ]
    assert compose_cast_section(chain) == 'Cast Magic Missile.'


def test_compose_cast_section_channel_continuation_uses_channeled_verb():
    """Synthetic channel-tick cast renders 'Channeled X.' instead of 'Cast X.'"""
    chain = [_channel_continuation(1, spell_name='Fan of Flames')]
    assert compose_cast_section(chain) == 'Channeled Fan of Flames.'


def test_compose_cast_section_regular_cast_still_uses_cast_verb():
    """Sanity: regular player casts continue to use 'Cast X.'"""
    chain = [_player_cast(1, spell_name='Magic Missile')]
    assert compose_cast_section(chain) == 'Cast Magic Missile.'


def test_compose_cast_section_channel_with_proc_keeps_channeled_verb():
    """If a channel continuation triggers a proc cast, the section verb
    stays 'Channeled' — the verb describes the player's action (the
    keypress that continued the channel), not each spell in the chain."""
    chain = [
        _channel_continuation(1, spell_name='Fan of Flames'),
        _proc_cast(2, parent_seq=1, spell_name='Magic Missile'),
    ]
    assert compose_cast_section(chain) == 'Channeled Fan of Flames, Magic Missile.'


def test_compose_cast_section_skips_cast_without_spell_dict():
    """Defensive: cast record with no spell key at all is skipped."""
    bad_cast = {
        'sequence': 2,
        'parent': 1,
        'event_type': 'cast_begin',
        'payload': {'is_player': True},  # no 'spell' key
        'marks': [],
    }
    chain = [
        _player_cast(1, spell_name='Magic Missile'),
        bad_cast,
    ]
    assert compose_cast_section(chain) == 'Cast Magic Missile.'


# ---- compose_killed_section ----

def test_compose_killed_section_empty_chain():
    assert compose_killed_section([]) == ""


def test_compose_killed_section_no_deaths():
    """Chain with damage but no deaths produces empty Killed section."""
    target = _target_snap(1, name="Goblin", x=5, y=5, cur_hp=2, max_hp=7)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, dmg_pre=5, dmg_post=5),
        _damage_full(3, 1, target, damage=5),
    ]
    assert compose_killed_section(chain) == ""


def test_compose_killed_section_single_minion_kill():
    """One minion dying renders 'N killed: Name (x,y): Spell N Dtype.'"""
    target = _target_snap(1, name="Goblin", x=5, y=5)
    records, _ = _hit_then_die(2, 1, target, spell="Magic Missile",
                               dtype="Arcane", dmg_pre=65, dmg_post=65)
    chain = [_player_cast(1, spell_name="Magic Missile")] + records
    expected = "1 killed: Goblin (5,5): Magic Missile 65 Arcane."
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_kill_uses_clamped_damage():
    """A killing blow's damage is clamped to the target's remaining HP in
    EventOnDamaged, and that clamped value is what the game's own combat log
    prints — so the digest reports it too (matching RW2 and the crisis/orphan
    producers). A 7-HP goblin hit by a 32-damage Fireball renders as 7, not the
    pre-clamp 32."""
    target = _target_snap(1, name="Goblin", x=5, y=5, max_hp=7)
    chain = [
        _player_cast(1, spell_name="Fireball"),
        _pre_damage(2, 1, target, spell="Fireball", dtype="Fire",
                    dmg_pre=16, dmg_post=32),
        _damage_full(3, 1, target, spell="Fireball", dtype="Fire",
                     damage=7),
        _death(4, 1, target, killing_damage=7, killing_dtype="Fire",
               killing_source="Fireball"),
    ]
    expected = "1 killed: Goblin (5,5): Fireball 7 Fire."
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_individuates_spawners():
    """Spawners are tier 1, individuated even if multiple of same name die."""
    s1 = _target_snap(1, name="Goblin Spawner", x=10, y=10, tier="spawner",
                       max_hp=40)
    s2 = _target_snap(2, name="Goblin Spawner", x=20, y=20, tier="spawner",
                       max_hp=40)
    r1, n1 = _hit_then_die(2, 1, s1, spell="Annihilate", dtype="Lightning",
                           dmg_pre=16, dmg_post=16)
    r2, _ = _hit_then_die(n1, 1, s2, spell="Annihilate", dtype="Lightning",
                          dmg_pre=16, dmg_post=16)
    chain = [_player_cast(1, spell_name="Annihilate")] + r1 + r2
    expected = (
        "2 killed: "
        "Goblin Spawner (10,10): Annihilate 16 Lightning. "
        "Goblin Spawner (20,20): Annihilate 16 Lightning."
    )
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_groups_minions_by_signature():
    """Two minions with identical hit history merge into one class."""
    g1 = _target_snap(1, name="Goblin", x=5, y=5)
    g2 = _target_snap(2, name="Goblin", x=6, y=6)
    r1, n1 = _hit_then_die(2, 1, g1, spell="Magic Missile",
                           dtype="Arcane", dmg_pre=7, dmg_post=7)
    r2, _ = _hit_then_die(n1, 1, g2, spell="Magic Missile",
                          dtype="Arcane", dmg_pre=7, dmg_post=7)
    chain = [_player_cast(1, spell_name="Magic Missile")] + r1 + r2
    expected = "2 killed: 2 Goblins at (5,5), (6,6): Magic Missile 7 Arcane."
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_minions_split_by_different_signature():
    """Two minions with same name but different hit histories DON'T merge."""
    g1 = _target_snap(1, name="Goblin", x=5, y=5)
    g2 = _target_snap(2, name="Goblin", x=6, y=6)
    g1_records, n1 = _hit_then_die(2, 1, g1, spell="Magic Missile",
                                   dtype="Arcane", dmg_pre=7, dmg_post=7)
    g2_records = [
        _pre_damage(n1, 1, g2, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=7, dmg_post=7),
        _damage_full(n1+1, 1, g2, spell="Magic Missile", dtype="Arcane",
                     damage=7),
        _pre_damage(n1+2, 1, g2, spell="Cracklevoid", dtype="Lightning",
                    dmg_pre=17, dmg_post=17),
        _damage_full(n1+3, 1, g2, spell="Cracklevoid", dtype="Lightning",
                     damage=17),
        _death(n1+4, 1, g2, killing_damage=17, killing_dtype="Lightning",
               killing_source="Cracklevoid"),
    ]
    chain = [_player_cast(1, spell_name="Magic Missile")] + g1_records + g2_records
    expected = (
        "2 killed: "
        "Goblin (5,5): Magic Missile 7 Arcane. "
        "Goblin (6,6): Magic Missile 7 Arcane, Cracklevoid 17 Lightning."
    )
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_same_spell_multi_hit_uses_plus():
    """Adjacent same-spell same-dtype hits collapse into 'plus' form."""
    target = _target_snap(1, name="Goblin", x=5, y=5)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=7, dmg_post=7),
        _damage_full(3, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=7),
        _pre_damage(4, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=17, dmg_post=17),
        _damage_full(5, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=17),
        _death(6, 1, target, killing_damage=17, killing_dtype="Arcane",
               killing_source="Magic Missile"),
    ]
    expected = "1 killed: Goblin (5,5): Magic Missile 7 plus 17 Arcane."
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_annihilate_three_dtypes():
    """Annihilate fires Fire + Lightning + Physical sequentially. Each
    is a separate (spell, dtype) — comma-separated, no 'plus'."""
    target = _target_snap(1, name="Goblin", x=5, y=5, max_hp=20)
    chain = [
        _player_cast(1, spell_name="Annihilate"),
        _pre_damage(2, 1, target, spell="Annihilate", dtype="Fire",
                    dmg_pre=16, dmg_post=16),
        _damage_full(3, 1, target, spell="Annihilate", dtype="Fire",
                     damage=16),
        _pre_damage(4, 1, target, spell="Annihilate", dtype="Lightning",
                    dmg_pre=16, dmg_post=16),
        _damage_full(5, 1, target, spell="Annihilate", dtype="Lightning",
                     damage=16),
        _pre_damage(6, 1, target, spell="Annihilate", dtype="Physical",
                    dmg_pre=16, dmg_post=16),
        _damage_full(7, 1, target, spell="Annihilate", dtype="Physical",
                     damage=16),
        _death(8, 1, target, killing_damage=16, killing_dtype="Physical",
               killing_source="Annihilate"),
    ]
    expected = (
        "1 killed: Goblin (5,5): "
        "Annihilate 16 Fire, Annihilate 16 Lightning, Annihilate 16 Physical."
    )
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_mixed_tiers():
    """Tier 1 (boss) and tier 2 (minion) classes coexist, in death order."""
    boss = _target_snap(1, name="Skull Lord", x=10, y=10, tier="boss", max_hp=100)
    g1 = _target_snap(2, name="Goblin", x=5, y=5)
    g2 = _target_snap(3, name="Goblin", x=6, y=6)
    boss_recs, n1 = _hit_then_die(2, 1, boss, spell="Annihilate",
                                  dtype="Lightning", dmg_pre=80, dmg_post=80)
    g1_recs, n2 = _hit_then_die(n1, 1, g1, spell="Annihilate",
                                dtype="Lightning", dmg_pre=80, dmg_post=80)
    g2_recs, _ = _hit_then_die(n2, 1, g2, spell="Annihilate",
                               dtype="Lightning", dmg_pre=80, dmg_post=80)
    chain = [_player_cast(1, spell_name="Annihilate")] + boss_recs + g1_recs + g2_recs
    expected = (
        "3 killed: "
        "Skull Lord (10,10): Annihilate 80 Lightning. "
        "2 Goblins at (5,5), (6,6): Annihilate 80 Lightning."
    )
    assert compose_killed_section(chain) == expected


def test_compose_killed_section_pluralizes_correctly():
    """Multi-target rendering uses _pluralize from helpers."""
    g1 = _target_snap(1, name="Witch", x=5, y=5)
    g2 = _target_snap(2, name="Witch", x=6, y=6)
    r1, n1 = _hit_then_die(2, 1, g1, spell="Fireball", dtype="Fire",
                           dmg_pre=20, dmg_post=20)
    r2, _ = _hit_then_die(n1, 1, g2, spell="Fireball", dtype="Fire",
                          dmg_pre=20, dmg_post=20)
    chain = [_player_cast(1, spell_name="Fireball")] + r1 + r2
    expected = "2 killed: 2 Witches at (5,5), (6,6): Fireball 20 Fire."
    assert compose_killed_section(chain) == expected


# ---- compose_surviving_section ----

def test_compose_surviving_section_empty_chain():
    assert compose_surviving_section([]) == ""


def test_compose_surviving_section_only_kills():
    """Chain with only deaths and no survivors produces empty Surviving."""
    target = _target_snap(1, name="Goblin", x=5, y=5)
    records, _ = _hit_then_die(2, 1, target, spell="Magic Missile",
                               dtype="Arcane", dmg_pre=65, dmg_post=65)
    chain = [_player_cast(1, spell_name="Magic Missile")] + records
    assert compose_surviving_section(chain) == ""


def test_compose_surviving_section_single_survivor():
    """One target damaged but not killed renders one line."""
    target = _target_snap(1, name="Lich", x=22, y=20, cur_hp=18, max_hp=30,
                          tier="boss")
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=12, dmg_post=12),
        _damage_full(3, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=12),
    ]
    expected = "1 surviving: Lich (22,20): Magic Missile 12 Arcane."
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_uniform_multi_hit_uses_hits_form():
    """Same-spell same-dtype repeats with uniform damage render as
    'Spell N hits, X Dtype each' per spec example."""
    target = _target_snap(1, name="Lich", x=22, y=20, cur_hp=18, max_hp=30,
                          tier="boss")
    chain = [
        _player_cast(1, spell_name="Disintegrator"),
        _pre_damage(2, 1, target, spell="Disintegrator", dtype="Physical",
                    dmg_pre=1, dmg_post=1),
        _damage_full(3, 1, target, spell="Disintegrator", dtype="Physical",
                     damage=1),
        _pre_damage(4, 1, target, spell="Disintegrator", dtype="Physical",
                    dmg_pre=1, dmg_post=1),
        _damage_full(5, 1, target, spell="Disintegrator", dtype="Physical",
                     damage=1),
    ]
    expected = "1 surviving: Lich (22,20): Disintegrator 2 hits, 1 Physical each."
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_interleaved_dtypes_collapse_per_dtype():
    """Annihilate fires F/L/P interleaved per cast. With non-adjacent
    grouping on the survivor form, a multi-cast scenario collapses to
    one 'N hits, X each' clause per damage type instead of listing
    every individual hit."""
    target = _target_snap(1, name="Displacer Beast Lich", x=20, y=13,
                          cur_hp=100, max_hp=200, tier="boss")
    # 3 Annihilate casts on the same target: 9 hits total (F/L/P × 3).
    seq = 2
    chain = [_player_cast(1, spell_name="Annihilate")]
    for cast_round in range(3):
        for dtype in ("Fire", "Lightning", "Physical"):
            chain.append(_pre_damage(seq, 1, target, spell="Annihilate",
                                     dtype=dtype, dmg_pre=16, dmg_post=16))
            chain.append(_damage_full(seq + 1, 1, target, spell="Annihilate",
                                      dtype=dtype, damage=16))
            seq += 2
    expected = (
        "1 surviving: Displacer Beast Lich (20,13): "
        "Annihilate 3 hits, 16 Fire each, "
        "Annihilate 3 hits, 16 Lightning each, "
        "Annihilate 3 hits, 16 Physical each."
    )
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_non_adjacent_same_key_collapses():
    """Hits with the same (spell, dtype) separated by other hits still
    collapse together — the bug from real-play data with Annihilate
    where F/L/P interleave."""
    target = _target_snap(1, name="Lich", x=22, y=20, cur_hp=10, max_hp=30,
                          tier="boss")
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=7, dmg_post=7),
        _damage_full(3, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=7),
        _pre_damage(4, 1, target, spell="Cracklevoid", dtype="Lightning",
                    dmg_pre=17, dmg_post=17),
        _damage_full(5, 1, target, spell="Cracklevoid", dtype="Lightning",
                     damage=17),
        _pre_damage(6, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=7, dmg_post=7),
        _damage_full(7, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=7),
    ]
    # Magic Missile appears at hits 1 and 3, Cracklevoid in between. With
    # non-adjacent grouping the two Magic Missile hits collapse together.
    expected = (
        "1 surviving: Lich (22,20): "
        "Magic Missile 2 hits, 7 Arcane each, Cracklevoid 17 Lightning."
    )
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_resisted_suffix():
    """Hit with post-resist < pre-resist tags the line '(resisted)'."""
    target = _target_snap(1, name="Lich", x=22, y=20, cur_hp=20, max_hp=30,
                          tier="boss")
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=12, dmg_post=6),
        _damage_full(3, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=6),
    ]
    expected = "1 surviving: Lich (22,20): Magic Missile 6 Arcane, (resisted)."
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_vulnerable_suffix():
    """Hit with post-resist > pre-resist tags the line '(vulnerable)'."""
    target = _target_snap(1, name="Spriggan", x=15, y=10, cur_hp=2, max_hp=9)
    chain = [
        _player_cast(1, spell_name="Fireball"),
        _pre_damage(2, 1, target, spell="Fireball", dtype="Fire",
                    dmg_pre=8, dmg_post=16),
        _damage_full(3, 1, target, spell="Fireball", dtype="Fire", damage=16),
    ]
    # Note: cur_hp goes negative or zero only if target dies; survivor here
    # would have cur_hp >= 1 in real play. Test data is illustrative.
    expected = "1 surviving: Spriggan (15,10): Fireball 16 Fire, (vulnerable)."
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_shielded_only():
    """All hits blocked — line shows 'Spell X Dtype blocked by N shields.' with
    the would-have-been magnitude (crisis parity)."""
    target = _target_snap(1, name="Boggart", x=25, y=16, cur_hp=6, max_hp=6)
    chain = [
        _player_cast(1, spell_name="Fireball"),
        _pre_damage(2, 1, target, spell="Fireball", dtype="Fire",
                    dmg_pre=9, dmg_post=9),
        _shield_removed(3, 1, target),
    ]
    expected = "1 surviving: Boggart (25,16): Fireball 9 Fire blocked by 1 shield."
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_mix_damage_and_shield():
    """Damage hit plus shield absorption combines on one line."""
    target = _target_snap(1, name="Lich", x=22, y=20, cur_hp=18, max_hp=30,
                          tier="boss")
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=12, dmg_post=12),
        _damage_full(3, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=12),
        _pre_damage(4, 1, target, spell="Cracklevoid", dtype="Lightning",
                    dmg_pre=5, dmg_post=5),
        _shield_removed(5, 1, target),
    ]
    expected = (
        "1 surviving: Lich (22,20): "
        "Magic Missile 12 Arcane, Cracklevoid 5 Lightning blocked by 1 shield."
    )
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_uniform_blocks_collapse():
    """Repeated identical blocked hits collapse to 'Spell N hits, X Dtype each
    blocked by N shields' — same grouping grammar as landed hits."""
    target = _target_snap(1, name="Ogre", x=8, y=8, cur_hp=20, max_hp=20,
                          tier="boss")
    chain = [
        _player_cast(1, spell_name="Fire Bolt"),
        _pre_damage(2, 1, target, spell="Fire Bolt", dtype="Fire",
                    dmg_pre=12, dmg_post=12),
        _shield_removed(3, 1, target),
        _pre_damage(4, 1, target, spell="Fire Bolt", dtype="Fire",
                    dmg_pre=12, dmg_post=12),
        _shield_removed(5, 1, target),
    ]
    expected = (
        "1 surviving: Ogre (8,8): "
        "Fire Bolt 2 hits, 12 Fire each blocked by 2 shields."
    )
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_block_magnitude_splits_signature():
    """Two same-name minions that blocked DIFFERENT would-have-been magnitudes
    must NOT merge — now that the blocked figure is spoken, a single arbitrary
    number on a merged line would misreport one of them."""
    t1 = _target_snap(1, name="Goblin", x=5, y=5, cur_hp=7, max_hp=7)
    t2 = _target_snap(2, name="Goblin", x=6, y=6, cur_hp=7, max_hp=7)
    chain = [
        _player_cast(1, spell_name="Fire Bolt"),
        _pre_damage(2, 1, t1, spell="Fire Bolt", dtype="Fire",
                    dmg_pre=9, dmg_post=9),
        _shield_removed(3, 1, t1),
        _pre_damage(4, 1, t2, spell="Fire Bolt", dtype="Fire",
                    dmg_pre=4, dmg_post=4),
        _shield_removed(5, 1, t2),
    ]
    out = compose_surviving_section(chain)
    assert out.startswith("2 surviving:")
    assert "Fire Bolt 9 Fire blocked by 1 shield" in out
    assert "Fire Bolt 4 Fire blocked by 1 shield" in out


def test_compose_surviving_section_groups_minions_by_signature_and_hp():
    """Two minions with identical hits AND identical post-chain HP merge."""
    g1 = _target_snap(1, name="Goblin", x=5, y=5, cur_hp=2, max_hp=7)
    g2 = _target_snap(2, name="Goblin", x=6, y=6, cur_hp=2, max_hp=7)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, g1, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=5, dmg_post=5),
        _damage_full(3, 1, g1, spell="Magic Missile", dtype="Arcane", damage=5),
        _pre_damage(4, 1, g2, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=5, dmg_post=5),
        _damage_full(5, 1, g2, spell="Magic Missile", dtype="Arcane", damage=5),
    ]
    expected = "2 surviving: 2 Goblins at (5,5), (6,6): Magic Missile 5 Arcane."
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_minions_split_by_post_hp():
    """Same hits but different post-chain HPs split into separate classes
    per the spec's 'final state' equivalence-class rule."""
    g1 = _target_snap(1, name="Goblin", x=5, y=5, cur_hp=2, max_hp=7)
    g2 = _target_snap(2, name="Goblin", x=6, y=6, cur_hp=4, max_hp=9)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, g1, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=5, dmg_post=5),
        _damage_full(3, 1, g1, spell="Magic Missile", dtype="Arcane", damage=5),
        _pre_damage(4, 1, g2, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=5, dmg_post=5),
        _damage_full(5, 1, g2, spell="Magic Missile", dtype="Arcane", damage=5),
    ]
    expected = (
        "2 surviving: "
        "Goblin (5,5): Magic Missile 5 Arcane. "
        "Goblin (6,6): Magic Missile 5 Arcane."
    )
    assert compose_surviving_section(chain) == expected


def test_compose_surviving_section_excludes_dead_targets():
    """A target that died is in Killed, not Surviving — even if it took
    damage events earlier in the chain."""
    dying = _target_snap(1, name="Goblin", x=5, y=5, cur_hp=0, max_hp=7)
    living = _target_snap(2, name="Witch", x=6, y=6, cur_hp=4, max_hp=10)
    dying_records, n1 = _hit_then_die(2, 1, dying, spell="Magic Missile",
                                      dtype="Arcane", dmg_pre=7, dmg_post=7)
    living_records = [
        _pre_damage(n1, 1, living, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=6, dmg_post=6),
        _damage_full(n1+1, 1, living, spell="Magic Missile", dtype="Arcane",
                     damage=6),
    ]
    chain = [_player_cast(1, spell_name="Magic Missile")] + dying_records + living_records
    expected = "1 surviving: Witch (6,6): Magic Missile 6 Arcane."
    assert compose_surviving_section(chain) == expected


# ---- compose_side_section ----

def _heal_event(seq, parent_seq, target_snap, source="Stoneeater Amulet", amount=2):
    """Fake EventOnHealed record with normalized positive heal amount."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnHealed',
        'payload': {
            'target': target_snap,
            'heal_amount': amount,
            'source_name': source,
        },
        'marks': [],
    }


def _buff_apply_event(seq, parent_seq, target_snap, buff_name="Arcane Frenzy",
                     turns_left=0, stack_type=2, stack_count_after=1):
    """Fake EventOnBuffApply record."""
    return {
        'sequence': seq,
        'parent': parent_seq,
        'event_type': 'EventOnBuffApply',
        'payload': {
            'target': target_snap,
            'buff': {
                'id': 12345,
                'name': buff_name,
                'turns_left': turns_left,
                'stack_type': stack_type,
            },
            'stack_count_after': stack_count_after,
        },
        'marks': [],
    }


def _wizard_snap(unit_id=100, x=10, y=10, cur_hp=50, max_hp=50):
    """Player wizard snapshot."""
    return _target_snap(unit_id, name="Wizard", x=x, y=y, cur_hp=cur_hp,
                        max_hp=max_hp, tier="wizard", team=0)


def test_compose_side_section_empty_chain():
    assert compose_side_section([]) == ""


def test_compose_side_section_no_wizard_no_side():
    """If no wizard reference is in the chain, can't determine player target."""
    chain = [_enemy_cast(1, spell_name="Dark Bolt")]
    assert compose_side_section(chain) == ""


def test_compose_side_section_single_heal():
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _heal_event(2, 1, wizard, source="Stoneeater Amulet", amount=2),
    ]
    expected = "Side. Heals: Stoneeater Amulet 2 HP."
    assert compose_side_section(chain) == expected


def test_compose_side_section_multiple_heals_same_source_aggregate():
    """Two heals from same source sum to total HP, no 'twice' wording."""
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _heal_event(2, 1, wizard, source="Stoneeater Amulet", amount=1),
        _heal_event(3, 1, wizard, source="Stoneeater Amulet", amount=1),
    ]
    expected = "Side. Heals: Stoneeater Amulet 2 HP."
    assert compose_side_section(chain) == expected


def test_compose_side_section_multiple_heals_different_sources():
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _heal_event(2, 1, wizard, source="Stoneeater Amulet", amount=2),
        _heal_event(3, 1, wizard, source="Healing Potion", amount=10),
    ]
    expected = "Side. Heals: Stoneeater Amulet 2 HP, Healing Potion 10 HP."
    assert compose_side_section(chain) == expected


def test_compose_side_section_ignores_non_wizard_heals():
    """Heals on minions or enemies don't go in Side (it's wizard-facing)."""
    wizard = _wizard_snap()
    minion = _target_snap(2, name="Treant", x=20, y=20)
    chain = [
        _player_cast(1, spell_name="Regrow"),
        _heal_event(2, 1, minion, source="Regrow", amount=12),
    ]
    assert compose_side_section(chain) == ""


def test_compose_side_section_stacking_buff_single_apply():
    """STACK_INTENSITY (=2) with one apply: 'applied, now N stacks'."""
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _buff_apply_event(2, 1, wizard, buff_name="Arcane Frenzy",
                          turns_left=10, stack_type=2, stack_count_after=1),
    ]
    expected = "Side. Buffs: Arcane Frenzy applied, now 1 stack."
    assert compose_side_section(chain) == expected


def test_compose_side_section_stacking_buff_multi_apply():
    """STACK_INTENSITY with multiple applies: 'applied N times, now M stacks'."""
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _buff_apply_event(2, 1, wizard, buff_name="Arcane Frenzy",
                          stack_type=2, stack_count_after=1),
        _buff_apply_event(3, 1, wizard, buff_name="Arcane Frenzy",
                          stack_type=2, stack_count_after=2),
        _buff_apply_event(4, 1, wizard, buff_name="Arcane Frenzy",
                          stack_type=2, stack_count_after=3),
        _buff_apply_event(5, 1, wizard, buff_name="Arcane Frenzy",
                          stack_type=2, stack_count_after=4),
    ]
    expected = "Side. Buffs: Arcane Frenzy applied 4 times, now 4 stacks."
    assert compose_side_section(chain) == expected


def test_compose_side_section_non_stacking_buff_with_duration():
    """STACK_REPLACE (=3) with turns: 'applied, T turns'."""
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Clarity Spell"),
        _buff_apply_event(2, 1, wizard, buff_name="Clarity",
                          turns_left=5, stack_type=3, stack_count_after=1),
    ]
    expected = "Side. Buffs: Clarity applied, 5 turns."
    assert compose_side_section(chain) == expected


def test_compose_side_section_non_stacking_buff_no_duration():
    """STACK_NONE buff without duration (passive item-granted): 'applied'."""
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _buff_apply_event(2, 1, wizard, buff_name="Stun Immunity",
                          turns_left=0, stack_type=0, stack_count_after=1),
    ]
    expected = "Side. Buffs: Stun Immunity applied."
    assert compose_side_section(chain) == expected


def test_compose_side_section_self_buff_refresh_reads_as_extended():
    """Re-casting an already-active non-stacking self-buff reads as an
    extension, not a fresh apply."""
    wizard = _wizard_snap()
    refresh = _buff_apply_event(2, 1, wizard, buff_name="Clarity",
                                turns_left=8, stack_type=3, stack_count_after=1)
    refresh['payload']['is_refresh'] = True
    chain = [_player_cast(1, spell_name="Clarity Spell"), refresh]
    assert compose_side_section(chain) == "Side. Buffs: Clarity extended to 8 turns."


def test_compose_side_section_heals_and_buffs_both_present():
    """Side combines Heals and Buffs sub-sections, in that order."""
    wizard = _wizard_snap()
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _heal_event(2, 1, wizard, source="Stoneeater Amulet", amount=2),
        _buff_apply_event(3, 1, wizard, buff_name="Arcane Frenzy",
                          stack_type=2, stack_count_after=1),
    ]
    expected = (
        "Side. Heals: Stoneeater Amulet 2 HP. "
        "Buffs: Arcane Frenzy applied, now 1 stack."
    )
    assert compose_side_section(chain) == expected


def test_compose_side_section_ignores_non_wizard_buffs():
    """Buffs applied to enemies (Blind, Poison) don't go in Side."""
    wizard = _wizard_snap()
    enemy = _target_snap(2, name="Goblin", x=20, y=20)
    chain = [
        _player_cast(1, spell_name="Fireball"),
        _buff_apply_event(2, 1, enemy, buff_name="Blind",
                          turns_left=4, stack_type=3, stack_count_after=1),
    ]
    assert compose_side_section(chain) == ""


# ---- compose_digest (orchestrator) ----

def test_compose_digest_empty_chain():
    assert compose_digest([]) == ""


def test_compose_digest_streamlined_kill():
    """1 cast, 1 damage, 1 kill -> streamlined form 'Cast X, killed Y, N D.'"""
    target = _target_snap(1, name="Grey Gorgon", x=22, y=12, max_hp=65)
    records, _ = _hit_then_die(2, 1, target, spell="Magic Missile",
                               dtype="Arcane", dmg_pre=65, dmg_post=65)
    chain = [_player_cast(1, spell_name="Magic Missile")] + records
    expected = "Cast Magic Missile, killed Grey Gorgon (22,12), 65 Arcane."
    assert compose_digest(chain) == expected


def test_compose_digest_streamlined_survivor():
    """1 cast, 1 damage, no kill -> 'Cast X, hit Y, N D.'"""
    target = _target_snap(1, name="Grey Gorgon", x=22, y=12, cur_hp=35, max_hp=65)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=30, dmg_post=30),
        _damage_full(3, 1, target, spell="Magic Missile", dtype="Arcane",
                     damage=30),
    ]
    expected = "Cast Magic Missile, hit Grey Gorgon (22,12), 30 Arcane."
    assert compose_digest(chain) == expected


def test_compose_digest_streamlined_empty_chain():
    """1 cast, 0 damage -> 'Cast X. No damage.'"""
    chain = [_player_cast(1, spell_name="Magic Missile")]
    expected = "Cast Magic Missile. No damage."
    assert compose_digest(chain) == expected


def test_compose_digest_streamlined_with_inline_side():
    """Streamlined form appends side effects inline, no 'Side.' label."""
    target = _target_snap(1, name="Grey Gorgon", x=5, y=5, max_hp=65)
    wizard = _wizard_snap()
    records, _ = _hit_then_die(2, 1, target, spell="Magic Missile",
                               dtype="Arcane", dmg_pre=65, dmg_post=65)
    chain = [_player_cast(1, spell_name="Magic Missile")] + records + [
        _heal_event(20, 1, wizard, source="Stoneeater Amulet", amount=1),
        _buff_apply_event(21, 1, wizard, buff_name="Arcane Frenzy",
                          stack_type=2, stack_count_after=1),
    ]
    expected = (
        "Cast Magic Missile, killed Grey Gorgon (5,5), 65 Arcane. "
        "Healed 1 HP from Stoneeater Amulet. Arcane Frenzy applied, now 1 stack."
    )
    assert compose_digest(chain) == expected


def test_compose_digest_streamlined_channel_uses_channeled_verb():
    """Channel continuation triggers streamlined form with 'Channeled' verb."""
    target = _target_snap(1, name="Goblin", x=5, y=5, max_hp=7)
    chain = [
        _channel_continuation(1, spell_name="Fan of Flames"),
        _pre_damage(2, 1, target, spell="Fan of Flames", dtype="Fire",
                    dmg_pre=7, dmg_post=7),
        _damage_full(3, 1, target, spell="Fan of Flames", dtype="Fire", damage=7),
        _death(4, 1, target, killing_damage=7, killing_dtype="Fire",
               killing_source="Fan of Flames"),
    ]
    expected = "Channeled Fan of Flames, killed Goblin (5,5), 7 Fire."
    assert compose_digest(chain) == expected


def test_compose_digest_standard_form_with_proc_chain():
    """Multiple casts (proc chain) -> standard four-section form."""
    g1 = _target_snap(1, name="Goblin", x=5, y=5, max_hp=7)
    g2 = _target_snap(2, name="Goblin", x=6, y=6, max_hp=7)
    r1, n1 = _hit_then_die(3, 1, g1, spell="Magic Missile",
                           dtype="Arcane", dmg_pre=7, dmg_post=7)
    r2, _ = _hit_then_die(n1, 2, g2, spell="Magic Missile",
                          dtype="Arcane", dmg_pre=7, dmg_post=7)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _proc_cast(2, parent_seq=1, spell_name="Magic Missile"),
    ] + r1 + r2
    # 2 casts -> standard form. 2 kills, both Goblins, same hit signature
    # -> grouped into one tier-2 class.
    expected = (
        "Cast Magic Missile times 2. "
        "2 killed: 2 Goblins at (5,5), (6,6): Magic Missile 7 Arcane."
    )
    assert compose_digest(chain) == expected


def test_compose_digest_standard_form_with_no_damage_line():
    """Cast with 0 hits -> standard form with 'No damage.' line.
    (Standard form when there are 2+ casts even if no damage; 1 cast +
    0 damage takes streamlined path instead.)"""
    chain = [
        _player_cast(1, spell_name="Disperse"),
        _proc_cast(2, parent_seq=1, spell_name="Magic Missile"),
    ]
    # 2 casts, 0 damage -> standard form with 'No damage.'
    expected = "Cast Disperse, Magic Missile. No damage."
    assert compose_digest(chain) == expected


def test_compose_digest_standard_with_killed_surviving_side():
    """All four sections present in standard form."""
    boss = _target_snap(1, name="Lich", x=22, y=20, cur_hp=18, max_hp=30,
                        tier="boss")
    minion = _target_snap(2, name="Goblin", x=5, y=5, max_hp=7)
    wizard = _wizard_snap()
    minion_records, n1 = _hit_then_die(3, 1, minion, spell="Magic Missile",
                                        dtype="Arcane", dmg_pre=7, dmg_post=7)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _proc_cast(2, parent_seq=1, spell_name="Magic Missile"),
    ] + minion_records + [
        _pre_damage(n1, 2, boss, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=12, dmg_post=12),
        _damage_full(n1+1, 2, boss, spell="Magic Missile", dtype="Arcane",
                     damage=12),
        _heal_event(n1+2, 1, wizard, source="Stoneeater Amulet", amount=2),
    ]
    expected = (
        "Cast Magic Missile times 2. "
        "1 killed: Goblin (5,5): Magic Missile 7 Arcane. "
        "1 surviving: Lich (22,20): Magic Missile 12 Arcane. "
        "Side. Heals: Stoneeater Amulet 2 HP."
    )
    assert compose_digest(chain) == expected


# ---- _claim_chain (mark stamping) ----

def test_claim_chain_stamps_mark_on_every_record():
    target = _target_snap(1, name="Goblin", x=5, y=5)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, target, dmg_pre=7, dmg_post=7),
        _damage_full(3, 1, target, damage=7),
    ]
    _claim_chain(chain, DIGEST_MARK)
    for rec in chain:
        assert DIGEST_MARK in rec['marks']


def test_claim_chain_idempotent():
    """Re-claiming the same chain doesn't duplicate marks."""
    target = _target_snap(1, name="Goblin", x=5, y=5)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _damage_full(2, 1, target),
    ]
    _claim_chain(chain, DIGEST_MARK)
    _claim_chain(chain, DIGEST_MARK)
    for rec in chain:
        assert rec['marks'].count(DIGEST_MARK) == 1


def test_claim_chain_creates_marks_list_if_missing():
    """Defensive: a record without 'marks' gets one created."""
    rec = {'sequence': 1, 'event_type': 'cast_begin'}  # no 'marks' key
    _claim_chain([rec], DIGEST_MARK)
    assert rec['marks'] == [DIGEST_MARK]


def test_claim_chain_preserves_existing_marks():
    """Existing marks from other composers stay; we just add ours."""
    rec = {'sequence': 1, 'event_type': 'cast_begin', 'marks': ['other_mark']}
    _claim_chain([rec], DIGEST_MARK)
    assert rec['marks'] == ['other_mark', DIGEST_MARK]


def test_claim_chain_empty_chain_is_noop():
    _claim_chain([], DIGEST_MARK)  # should not raise


# ---- compose_spawned_section ----

def test_compose_spawned_section_empty_chain():
    assert compose_spawned_section([]) == ""


def test_compose_spawned_section_no_unit_added():
    """Chain with damage but no spawns -> empty Spawned."""
    target = _target_snap(1, name="Goblin", x=5, y=5)
    records, _ = _hit_then_die(2, 1, target, spell="Magic Missile",
                               dtype="Arcane", dmg_pre=7, dmg_post=7)
    chain = [_player_cast(1, spell_name="Magic Missile")] + records
    assert compose_spawned_section(chain) == ""


def test_compose_spawned_section_single_hostile_spawn():
    """One hostile unit spawned in chain, untouched -> rendered without
    'Ally' prefix."""
    spawn = _target_snap(2, name="Fly Swarm", x=5, y=5, cur_hp=3, max_hp=3,
                         tier="minion", team=1)
    chain = [
        _player_cast(1, spell_name="Annihilate"),
        _unit_added(2, 1, spawn),
    ]
    expected = "1 spawned: Fly Swarm (5,5)."
    assert compose_spawned_section(chain) == expected


def test_compose_spawned_section_single_ally_spawn():
    """Ally summon by player gets 'Ally' prefix when team matches wizard."""
    spawn = _target_snap(2, name="Wolf", x=3, y=3, cur_hp=8, max_hp=8,
                         tier="minion", team=0)
    chain = [
        _player_cast(1, spell_name="Summon Wolf"),
        _unit_added(2, 1, spawn),
    ]
    expected = "1 spawned: Ally Wolf (3,3)."
    assert compose_spawned_section(chain) == expected


def test_compose_spawned_section_grouped_hostile_minions():
    """4 Fly Swarms spawned (game displaces to distinct tiles) merge
    into one tier-2 equivalence class with comma-separated coords."""
    spawns = [
        _target_snap(2, name="Fly Swarm", x=5, y=5, team=1),
        _target_snap(3, name="Fly Swarm", x=6, y=5, team=1),
        _target_snap(4, name="Fly Swarm", x=5, y=6, team=1),
        _target_snap(5, name="Fly Swarm", x=6, y=6, team=1),
    ]
    chain = [_player_cast(1, spell_name="Annihilate")]
    for i, spawn in enumerate(spawns):
        chain.append(_unit_added(2 + i, 1, spawn))
    expected = (
        "4 spawned: 4 Fly Swarms at (5,5), (6,5), (5,6), (6,6)."
    )
    assert compose_spawned_section(chain) == expected


def test_compose_spawned_section_excludes_killed_spawn():
    """Spawned unit that died in same chain goes to Killed, not Spawned."""
    bag = _target_snap(1, name="Bag of Bugs", x=5, y=5, max_hp=16)
    fly1 = _target_snap(20, name="Fly Swarm", x=5, y=5, cur_hp=0, max_hp=3, team=1)
    fly2 = _target_snap(21, name="Fly Swarm", x=6, y=5, cur_hp=3, max_hp=3, team=1)
    bag_records, n1 = _hit_then_die(2, 1, bag, spell="Annihilate",
                                    dtype="Fire", dmg_pre=16, dmg_post=32)
    chain = [
        _player_cast(1, spell_name="Annihilate"),
    ] + bag_records + [
        _unit_added(n1, 1, fly1),       # spawned, then killed
        _unit_added(n1+1, 1, fly2),     # spawned, untouched
        _pre_damage(n1+2, 1, fly1, spell="Prince of Ruin", dtype="Fire",
                    dmg_pre=13, dmg_post=13),
        _damage_full(n1+3, 1, fly1, spell="Prince of Ruin", dtype="Fire",
                     damage=3),
        _death(n1+4, 1, fly1, killing_damage=3, killing_dtype="Fire",
               killing_source="Prince of Ruin"),
    ]
    # fly1 died, fly2 untouched; only fly2 in Spawned
    expected = "1 spawned: Fly Swarm (6,5)."
    assert compose_spawned_section(chain) == expected


def test_compose_spawned_section_excludes_damaged_spawn():
    """Spawned unit that took damage and survived goes to Surviving, not Spawned."""
    spawn = _target_snap(2, name="Wolf", x=3, y=3, cur_hp=2, max_hp=8, team=0)
    chain = [
        _player_cast(1, spell_name="Summon Wolf"),
        _unit_added(2, 1, spawn),
        _pre_damage(3, 1, spawn, spell="Self-Damage", dtype="Physical",
                    dmg_pre=6, dmg_post=6),
        _damage_full(4, 1, spawn, spell="Self-Damage", dtype="Physical",
                     damage=6),
    ]
    assert compose_spawned_section(chain) == ""


def test_compose_spawned_section_skips_soul_jars():
    """Soul Jars have a dedicated handler; digest stays out."""
    sj = _target_snap(2, name="Soul Jar", x=5, y=5, team=1)
    chain = [
        _player_cast(1, spell_name="Annihilate"),
        _unit_added(2, 1, sj),
    ]
    assert compose_spawned_section(chain) == ""


def test_compose_spawned_section_mixed_ally_and_hostile():
    """Same section can contain both ally and hostile spawns. Allies
    flagged; hostiles unmarked."""
    wolf = _target_snap(2, name="Wolf", x=3, y=3, team=0)
    g1 = _target_snap(3, name="Goblin", x=5, y=5, team=1)
    g2 = _target_snap(4, name="Goblin", x=6, y=6, team=1)
    chain = [
        _player_cast(1, spell_name="Mixed Spell"),
        _unit_added(2, 1, wolf),
        _unit_added(3, 1, g1),
        _unit_added(4, 1, g2),
    ]
    expected = (
        "3 spawned: Ally Wolf (3,3). 2 Goblins at (5,5), (6,6)."
    )
    assert compose_spawned_section(chain) == expected


def test_compose_spawned_section_same_name_different_team_split():
    """Friendly Slimy-Vampire-spawned Blood Slimes that would normally
    be hostile DON'T merge with hostile Blood Slimes — equivalence-class
    key is (name, team)."""
    ally_slime = _target_snap(2, name="Blood Slime", x=5, y=14, team=0)
    hostile_slime = _target_snap(3, name="Blood Slime", x=10, y=10, team=1)
    chain = [
        _player_cast(1, spell_name="Some Player Action"),
        _unit_added(2, 1, ally_slime),
        _unit_added(3, 1, hostile_slime),
    ]
    expected = (
        "2 spawned: Ally Blood Slime (5,14). Blood Slime (10,10)."
    )
    assert compose_spawned_section(chain) == expected


def test_compose_spawned_section_tier1_individuated():
    """Spawners and bosses are tier 1, individuated even when same-name."""
    s1 = _target_snap(2, name="Goblin Spawner", x=10, y=10, tier="spawner", team=1)
    s2 = _target_snap(3, name="Goblin Spawner", x=20, y=20, tier="spawner", team=1)
    chain = [
        _player_cast(1, spell_name="Some Action"),
        _unit_added(2, 1, s1),
        _unit_added(3, 1, s2),
    ]
    expected = (
        "2 spawned: Goblin Spawner (10,10). Goblin Spawner (20,20)."
    )
    assert compose_spawned_section(chain) == expected


# ---- compose_digest with Spawned ----

def test_compose_digest_summon_only_omits_no_damage():
    """Cast Summon Wolf with no enemies: spawn-only chain. The Spawned
    section makes the chain non-empty, so 'No damage.' must NOT fire."""
    wolf = _target_snap(2, name="Wolf", x=3, y=3, team=0)
    chain = [
        _player_cast(1, spell_name="Summon Wolf"),
        _unit_added(2, 1, wolf),
    ]
    expected = "Cast Summon Wolf. 1 spawned: Ally Wolf (3,3)."
    assert compose_digest(chain) == expected


def test_compose_digest_pure_empty_chain_still_says_no_damage():
    """1 cast, 0 damage, 0 spawns: 'Cast X. No damage.' as before."""
    chain = [_player_cast(1, spell_name="Magic Missile")]
    assert compose_digest(chain) == "Cast Magic Missile. No damage."


def test_compose_digest_section_ordering_with_spawn():
    """Spawned slot in: Cast -> Killed -> Surviving -> Spawned -> Side."""
    g_dead = _target_snap(1, name="Goblin", x=5, y=5, max_hp=7)
    lich = _target_snap(2, name="Lich", x=10, y=10, cur_hp=18, max_hp=30,
                        tier="boss")
    wolf = _target_snap(3, name="Wolf", x=3, y=3, team=0)
    wizard = _wizard_snap()
    g_records, n1 = _hit_then_die(2, 1, g_dead, spell="Magic Missile",
                                   dtype="Arcane", dmg_pre=7, dmg_post=7)
    chain = [_player_cast(1, spell_name="Mixed")] + g_records + [
        _pre_damage(n1, 1, lich, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=12, dmg_post=12),
        _damage_full(n1+1, 1, lich, spell="Magic Missile", dtype="Arcane",
                     damage=12),
        _unit_added(n1+2, 1, wolf),
        _heal_event(n1+3, 1, wizard, source="Stoneeater Amulet", amount=2),
    ]
    expected = (
        "Cast Mixed. "
        "1 killed: Goblin (5,5): Magic Missile 7 Arcane. "
        "1 surviving: Lich (10,10): Magic Missile 12 Arcane. "
        "1 spawned: Ally Wolf (3,3). "
        "Side. Heals: Stoneeater Amulet 2 HP."
    )
    assert compose_digest(chain) == expected


def test_compose_digest_routes_to_standard_when_spawn_present():
    """A 1-cast 1-damage chain that ALSO has spawns routes to standard
    form, not streamlined. Death Bolt raise-skeleton case."""
    goblin = _target_snap(1, name="Goblin", x=5, y=5, max_hp=7)
    skeleton = _target_snap(2, name="Skeleton", x=5, y=5, team=0)
    g_records, n1 = _hit_then_die(2, 1, goblin, spell="Death Bolt",
                                  dtype="Dark", dmg_pre=9, dmg_post=9)
    chain = [_player_cast(1, spell_name="Death Bolt")] + g_records + [
        _unit_added(n1, 1, skeleton),
    ]
    # Streamlined would have produced 'Cast Death Bolt, killed Goblin
    # (5,5), 9 Dark.' Standard form correctly renders the spawn too.
    expected = (
        "Cast Death Bolt. "
        "1 killed: Goblin (5,5): Death Bolt 9 Dark. "
        "1 spawned: Ally Skeleton (5,5)."
    )
    assert compose_digest(chain) == expected


# ---- same-coord compression in target phrasing ----

def test_killed_section_all_same_coord_collapses_to_single_at():
    """4 Fly Swarms killed all at one tile -> 'N at (x,y)' single form,
    not 'N at (x,y), (x,y), (x,y), (x,y)'. The same-tile pattern
    surfaces sequential spawn-die-spawn cycles."""
    flies = [
        _target_snap(i, name="Fly Swarm", x=14, y=14, max_hp=6)
        for i in range(2, 6)
    ]
    chain = [_player_cast(1, spell_name="Fireball")]
    seq = 2
    for fly in flies:
        records, seq = _hit_then_die(seq, 1, fly, spell="Fireball",
                                     dtype="Fire", dmg_pre=10, dmg_post=10)
        chain += records
    expected = (
        "4 killed: 4 Fly Swarms at (14,14): Fireball 10 Fire."
    )
    assert compose_killed_section(chain) == expected


def test_killed_section_mixed_dupes_uses_count_per_coord_form():
    """3 Fly Swarms at (14,14) and 1 at (15,14) — the Bag-of-Bugs
    cascade pattern. Renders as 'N plural, A at (x,y), B at (x,y)'."""
    flies_at_14 = [
        _target_snap(i, name="Fly Swarm", x=14, y=14, max_hp=6)
        for i in range(2, 5)
    ]
    fly_at_15 = _target_snap(5, name="Fly Swarm", x=15, y=14, max_hp=6)
    chain = [_player_cast(1, spell_name="Fireball")]
    seq = 2
    for fly in flies_at_14:
        records, seq = _hit_then_die(seq, 1, fly, spell="Prince of Ruin",
                                     dtype="Fire", dmg_pre=13, dmg_post=13)
        chain += records
    records, seq = _hit_then_die(seq, 1, fly_at_15, spell="Prince of Ruin",
                                 dtype="Fire", dmg_pre=13, dmg_post=13)
    chain += records
    expected = (
        "4 killed: 4 Fly Swarms, 3 at (14,14), 1 at (15,14): "
        "Prince of Ruin 13 Fire."
    )
    assert compose_killed_section(chain) == expected


def test_killed_section_all_unique_coords_keeps_verbatim_form():
    """Backward compat: when all coords are unique, the existing 'at
    (x1,y1), (x2,y2), ...' form is preserved (not changed by the
    compression)."""
    g1 = _target_snap(1, name="Goblin", x=5, y=5)
    g2 = _target_snap(2, name="Goblin", x=6, y=6)
    r1, n1 = _hit_then_die(2, 1, g1, spell="Magic Missile",
                           dtype="Arcane", dmg_pre=7, dmg_post=7)
    r2, _ = _hit_then_die(n1, 1, g2, spell="Magic Missile",
                          dtype="Arcane", dmg_pre=7, dmg_post=7)
    chain = [_player_cast(1, spell_name="Magic Missile")] + r1 + r2
    expected = "2 killed: 2 Goblins at (5,5), (6,6): Magic Missile 7 Arcane."
    assert compose_killed_section(chain) == expected


def test_spawned_section_same_coord_collapses():
    """Spawned section uses the same compression. 4 spawns all at one
    tile -> '4 plural at (x,y)' form."""
    spawns = [
        _target_snap(i, name="Fly Swarm", x=14, y=14, team=1)
        for i in range(2, 6)
    ]
    chain = [_player_cast(1, spell_name="Annihilate")]
    for i, spawn in enumerate(spawns):
        chain.append(_unit_added(2 + i, 1, spawn))
    expected = "4 spawned: 4 Fly Swarms at (14,14)."
    assert compose_spawned_section(chain) == expected


def test_spawned_section_mixed_dupes_with_ally_prefix():
    """Compression applies through the ally prefix. 2 Ally Wolves at
    same tile, 1 at another."""
    w1 = _target_snap(2, name="Wolf", x=3, y=3, team=0)
    w2 = _target_snap(3, name="Wolf", x=3, y=3, team=0)
    w3 = _target_snap(4, name="Wolf", x=4, y=4, team=0)
    chain = [
        _player_cast(1, spell_name="Pack Summon"),
        _unit_added(2, 1, w1),
        _unit_added(3, 1, w2),
        _unit_added(4, 1, w3),
    ]
    expected = "3 spawned: 3 Ally Wolves, 2 at (3,3), 1 at (4,4)."
    assert compose_spawned_section(chain) == expected


def test_surviving_section_all_same_coord_collapses():
    """Surviving section also uses the compression for cohort survivors
    at a single tile (rare but possible — minions clustered, AoE-ish hit)."""
    g1 = _target_snap(1, name="Goblin", x=5, y=5, cur_hp=3, max_hp=7)
    g2 = _target_snap(2, name="Goblin", x=5, y=5, cur_hp=3, max_hp=7)
    chain = [
        _player_cast(1, spell_name="Magic Missile"),
        _pre_damage(2, 1, g1, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=4, dmg_post=4),
        _damage_full(3, 1, g1, spell="Magic Missile", dtype="Arcane", damage=4),
        _pre_damage(4, 1, g2, spell="Magic Missile", dtype="Arcane",
                    dmg_pre=4, dmg_post=4),
        _damage_full(5, 1, g2, spell="Magic Missile", dtype="Arcane", damage=4),
    ]
    expected = "2 surviving: 2 Goblins at (5,5): Magic Missile 4 Arcane."
    assert compose_surviving_section(chain) == expected


# ============================================================================
# Empty-autofire silence rule (post-2026-05-08 policy)
# ============================================================================
# Autofires (RepeaterCast, Explosive Spore Manual, etc.) ARE chain roots
# under the new policy — they're rendered when they produce effects,
# silenced when they don't. _is_empty_autofire is the gating predicate;
# compose_digest returns "" for chains where it returns True.


def test_is_empty_autofire_true_for_pay_costs_false_with_no_effects():
    """Autofire chain with cast_begin only — no damage, no death, no
    spawn, no buff — is empty and should be silenced."""
    chain = [_player_autocast(1, spell_name="Combust Poison")]
    assert _is_empty_autofire(chain) is True


def test_is_empty_autofire_false_for_pay_costs_false_with_damage():
    """Autofire that hit something is not empty — render normally."""
    target = _target_snap(2, name="Goblin", x=5, y=5, cur_hp=3, max_hp=7)
    chain = [
        _player_autocast(1, spell_name="Combust Poison"),
        _pre_damage(2, 1, target, spell="Combust Poison",
                    dtype="Poison", dmg_pre=4, dmg_post=4),
        _damage_full(3, 1, target, spell="Combust Poison",
                     dtype="Poison", damage=4),
    ]
    assert _is_empty_autofire(chain) is False


def test_is_empty_autofire_false_for_real_keypress_with_no_effects():
    """A real keypress that misses everything is NOT silenced — the
    player wants to know their cast did nothing. 'Cast X. No damage.'
    is a useful line for keypresses, just not for autofires."""
    chain = [_player_cast(1, spell_name="Magic Missile")]
    assert _is_empty_autofire(chain) is False


def test_compose_digest_silences_empty_autofire():
    """End-to-end: compose_digest returns "" for an empty autofire."""
    chain = [_player_autocast(1, spell_name="Combust Poison")]
    assert compose_digest(chain) == ""


def test_compose_digest_renders_effective_autofire():
    """End-to-end: compose_digest renders an autofire that DID something.
    Streamlined form is fine — it's a single cast with one damage event."""
    target = _target_snap(2, name="Goblin", x=5, y=5, cur_hp=0, max_hp=7,
                          tier="minion")
    chain = [
        _player_autocast(1, spell_name="Combust Poison"),
        _pre_damage(2, 1, target, spell="Combust Poison",
                    dtype="Poison", dmg_pre=7, dmg_post=7),
        _damage_full(3, 1, target, spell="Combust Poison",
                     dtype="Poison", damage=7),
        _death(4, 1, target),
    ]
    out = compose_digest(chain)
    assert out  # non-empty
    assert "Combust Poison" in out
    assert "Goblin" in out


# ============================================================================
# Multi-root iteration via find_all_pending_roots
# ============================================================================
# Quick-cast equipment and certain spell-mid-spell patterns can produce
# more than one keypress chain root inside a single is_awaiting_input
# window. find_all_pending_roots returns all of them in chronological
# order so each chain narrates separately.


def test_find_all_pending_roots_empty_returns_empty_list():
    assert find_all_pending_roots([], None) == []


def test_find_all_pending_roots_single_root_returns_one_element():
    keypress = _player_cast(3)
    assert find_all_pending_roots([keypress], None) == [keypress]


def test_find_all_pending_roots_multiple_in_chronological_order():
    """When multiple keypress chains complete in one window, all are
    returned in sequence order (oldest first) so the composer can
    iterate and emit one digest per cast in the order they happened."""
    first = _player_cast(3, spell_name="Fireball")
    second = _player_cast(5, spell_name="Magic Missile")
    third = _player_autocast(7, spell_name="Combust Poison")
    records = [third, first, second]  # input order is irrelevant
    result = find_all_pending_roots(records, None)
    assert result == [first, second, third]


def test_find_all_pending_roots_respects_threshold():
    """Roots whose sequence is at or below last_digested_seq are skipped
    even if they qualify as keypress casts."""
    already_done = _player_cast(2, spell_name="Old Cast")
    pending = _player_cast(5, spell_name="New Cast")
    result = find_all_pending_roots([already_done, pending], 3)
    assert result == [pending]


def test_find_all_pending_roots_skips_proc_casts_with_parent():
    """Proc casts with non-None parent are not roots and must be
    excluded even if they pass the keypress predicate otherwise."""
    keypress = _player_cast(3)
    proc = _proc_cast(4, parent_seq=3, spell_name="Multimancy proc")
    result = find_all_pending_roots([keypress, proc], None)
    assert result == [keypress]


def test_find_all_pending_roots_skips_enemy_casts():
    """Enemy casts are not player keypress roots."""
    keypress = _player_cast(3)
    enemy = _enemy_cast(5)
    result = find_all_pending_roots([keypress, enemy], None)
    assert result == [keypress]


# ====================================================================
# compose_debuffs_applied_section / compose_buffs_applied_section —
# non-wizard buff applies in chain, split by buff_type.
# ====================================================================


def _buff_apply(seq, parent_seq, target_snap, buff_name="Poisoned",
                turns_left=5, buff_type=2, **flags):
    """Build an EventOnBuffApply record on a non-wizard target.

    buff_type defaults to 2 (curse) since the dominant chain case is
    player applying debuffs to enemies. Tests covering positive buffs
    on allies override to buff_type=1 (bless)."""
    payload = {
        'target': target_snap,
        'buff': {
            'id': 9999, 'name': buff_name,
            'turns_left': turns_left, 'stack_type': 0,
            'buff_type': buff_type,
        },
        'stack_count_after': 1,
    }
    payload.update(flags)
    return {
        'sequence': seq, 'parent': parent_seq,
        'event_type': 'EventOnBuffApply',
        'payload': payload, 'marks': [],
    }


def test_debuffs_section_empty_chain():
    assert compose_debuffs_applied_section([]) == ""


def test_debuffs_section_no_buff_applies():
    chain = [_player_cast(1)]
    assert compose_debuffs_applied_section(chain) == ""


def test_debuffs_section_single_target_single_buff():
    """Single application drops the count-led header — line carries
    enough structure to identify itself."""
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, target, buff_name='Poisoned', turns_left=5,
                    buff_type=2),
    ]
    out = compose_debuffs_applied_section(chain)
    assert out == "Goblin (3,4) poisoned, 5 turns."


def test_debuffs_section_multi_target_collapse():
    """Three Goblins each got Poisoned for 5 turns — collapse. Multi
    case uses 'debuffs' header."""
    chain = [_player_cast(1)]
    for i in range(3):
        target = _target_snap(unit_id=200 + i, name='Goblin', x=3 + i, y=4)
        chain.append(_buff_apply(2 + i, 1, target,
                                  buff_name='Poisoned', turns_left=5,
                                  buff_type=2))
    out = compose_debuffs_applied_section(chain)
    assert out == ("3 debuffs applied: 3 Goblins at (3,4), (4,4), (5,4)"
                   " poisoned, 5 turns each.")


def test_debuffs_section_skips_wizard_target():
    wizard = _target_snap(unit_id=_WIZARD_ID, name='Wizard', x=10, y=10)
    wizard['is_player_controlled'] = True
    wizard['team'] = 0
    wizard['tier'] = 'wizard'
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, wizard, buff_name='Petrified', turns_left=5,
                    buff_type=2),
    ]
    assert compose_debuffs_applied_section(chain) == ""


def test_debuffs_section_skips_silent_activate():
    target = _target_snap(unit_id=200, name='Wolf', x=3, y=4)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, target, buff_name='Pack Tactics', turns_left=0,
                    buff_type=2, is_silent_activate=True),
    ]
    assert compose_debuffs_applied_section(chain) == ""


def test_debuffs_section_refresh_renders_extended_line():
    """A refresh (duration extension) on an enemy renders as an extended
    line, not silently dropped."""
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, target, buff_name='Poisoned', turns_left=8,
                    buff_type=2, is_refresh=True),
    ]
    assert (compose_debuffs_applied_section(chain)
            == "Goblin (3,4) poisoned, extended to 8 turns.")


def test_debuffs_section_extended_group_collapses():
    """Multiple same-debuff extensions collapse into one extended line."""
    chain = [_player_cast(1)]
    for i in range(3):
        t = _target_snap(unit_id=200 + i, name='Goblin', x=3 + i, y=4)
        chain.append(_buff_apply(2 + i, 1, t, buff_name='Poisoned',
                                 turns_left=8, buff_type=2, is_refresh=True))
    assert (compose_debuffs_applied_section(chain)
            == "3 Goblins at (3,4), (4,4), (5,4) poisoned,"
               " extended to 8 turns each.")


def test_debuffs_section_two_groups_new_and_extended():
    """A mixed cast — some newly poisoned, some extended — renders two
    distinct collapsed groups."""
    chain = [_player_cast(1)]
    for i in range(2):
        t = _target_snap(unit_id=200 + i, name='Goblin', x=3 + i, y=4)
        chain.append(_buff_apply(2 + i, 1, t, buff_name='Poisoned',
                                 turns_left=5, buff_type=2))
    for i in range(2):
        t = _target_snap(unit_id=210 + i, name='Goblin', x=5 + i, y=4)
        chain.append(_buff_apply(10 + i, 1, t, buff_name='Poisoned',
                                 turns_left=8, buff_type=2, is_refresh=True))
    assert (compose_debuffs_applied_section(chain)
            == "2 debuffs applied: 2 Goblins at (3,4), (4,4) poisoned,"
               " 5 turns each. 2 Goblins at (5,4), (6,4) poisoned,"
               " extended to 8 turns each.")


def test_buffs_section_refresh_renders_extended_line():
    """A buff refresh on an ally renders as a verb-led extended line."""
    ally = _target_snap(unit_id=200, name='Goatia', x=8, y=8)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, ally, buff_name='Strength', turns_left=8,
                    buff_type=1, is_refresh=True),
    ]
    assert (compose_buffs_applied_section(chain)
            == "Goatia (8,8) Strength extended to 8 turns.")


def test_debuffs_section_no_duration():
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, target, buff_name='Berserk', turns_left=None,
                    buff_type=2),
    ]
    out = compose_debuffs_applied_section(chain)
    assert out == "Goblin (3,4) berserk."


def test_debuffs_section_berserk_proc_on_poisoned():
    """User's reported case: trigger applies Berserk on enemies that just
    got Poisoned. Both surface in the Debuffs section since Berserk and
    Poisoned are both buff_type=2 (curse)."""
    target = _target_snap(unit_id=200, name='Aelf', x=5, y=5)
    chain = [
        _player_cast(1, spell_name='Combust Poison'),
        _buff_apply(2, 1, target, buff_name='Poisoned', turns_left=5,
                    buff_type=2),
        _buff_apply(3, 2, target, buff_name='Berserk', turns_left=8,
                    buff_type=2),
    ]
    out = compose_debuffs_applied_section(chain)
    assert "Aelf (5,5) poisoned, 5 turns." in out
    assert "Aelf (5,5) berserk, 8 turns." in out
    assert out.startswith("2 debuffs applied:")


def test_debuffs_section_skips_buff_type_bless():
    """A bless (buff_type=1) on a non-wizard should NOT appear in
    Debuffs — it goes to Buffs."""
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, target, buff_name='Strength', turns_left=5,
                    buff_type=1),
    ]
    assert compose_debuffs_applied_section(chain) == ""


def test_buffs_section_single_ally():
    """Buff applied to ally — surfaces in Buffs section. Buff line uses
    'gained {Name}' verb-led form rather than adjective form because
    buff names like 'Strength' don't work as adjectives."""
    ally = _target_snap(unit_id=200, name='Goatia', x=8, y=8)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, ally, buff_name='Strength', turns_left=5,
                    buff_type=1),
    ]
    out = compose_buffs_applied_section(chain)
    # Bare line (single application — no count header). Buff name kept
    # in original case after the 'gained' verb.
    assert out == "Goatia (8,8) gained Strength, 5 turns."


def test_buffs_section_multi_collapse():
    """Three Wolves all got Haste for 5 turns — collapsed line uses
    'N buffs applied:' header and 'gained {Name}' per line, with
    'each' suffix on the duration."""
    chain = [_player_cast(1)]
    for i in range(3):
        ally = _target_snap(unit_id=200 + i, name='Wolf', x=3 + i, y=4)
        chain.append(_buff_apply(2 + i, 1, ally,
                                  buff_name='Haste', turns_left=5,
                                  buff_type=1))
    out = compose_buffs_applied_section(chain)
    assert out.startswith("3 buffs applied:")
    assert "gained Haste, 5 turns each." in out


def test_buffs_section_skips_buff_type_curse():
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, target, buff_name='Poisoned', turns_left=5,
                    buff_type=2),
    ]
    assert compose_buffs_applied_section(chain) == ""


def test_buffs_section_skips_passive_buff_type():
    """buff_type=0 (passive) typically appears via unit-creation
    activation; chain-time applies are rare and skipped (neither
    Debuffs nor Buffs claims them)."""
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, target, buff_name='Pack Tactics', turns_left=0,
                    buff_type=0),
    ]
    assert compose_buffs_applied_section(chain) == ""
    assert compose_debuffs_applied_section(chain) == ""


def test_debuffs_section_skips_dead_targets():
    """Debuffs on enemies that died in the same chain are filtered out —
    once the target is dead, the status is irrelevant. Matches the
    'silence debuffs on enemies whose ultimate fate is death' rule."""
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4,
                           cur_hp=0, max_hp=10)
    chain = [
        _player_cast(1, spell_name='Combust Poison'),
        _buff_apply(2, 1, target, buff_name='Poisoned', turns_left=5,
                    buff_type=2),
        # Target dies later in the chain.
        _death(3, 1, target),
    ]
    assert compose_debuffs_applied_section(chain) == ""


def test_debuffs_section_keeps_surviving_targets():
    """A surviving target's debuff still surfaces — only dead-target
    debuffs are filtered."""
    survivor = _target_snap(unit_id=200, name='Goblin', x=3, y=4,
                             cur_hp=5, max_hp=10)
    dead = _target_snap(unit_id=201, name='Goblin', x=4, y=4,
                         cur_hp=0, max_hp=10)
    chain = [
        _player_cast(1, spell_name='Combust Poison'),
        _buff_apply(2, 1, survivor, buff_name='Poisoned', turns_left=5,
                    buff_type=2),
        _buff_apply(3, 1, dead, buff_name='Poisoned', turns_left=5,
                    buff_type=2),
        _death(4, 1, dead),
    ]
    out = compose_debuffs_applied_section(chain)
    # Only the survivor's debuff line — single application form.
    assert out == "Goblin (3,4) poisoned, 5 turns."


def test_buffs_section_skips_dead_targets():
    """Same filter applies to buffs: a buffed ally that died this chain
    has its buff line dropped (irrelevant once the unit is gone)."""
    dead_ally = _target_snap(unit_id=200, name='Wolf', x=3, y=4,
                              cur_hp=0, max_hp=20)
    chain = [
        _player_cast(1),
        _buff_apply(2, 1, dead_ally, buff_name='Strength', turns_left=5,
                    buff_type=1),
        _death(3, 1, dead_ally),
    ]
    assert compose_buffs_applied_section(chain) == ""


def test_compose_digest_includes_debuffs_in_full_output():
    """Integration: compose_digest's standard form includes the
    Debuffs section when present. Single-application form drops the
    count header."""
    target = _target_snap(unit_id=200, name='Goblin', x=3, y=4,
                           cur_hp=5, max_hp=10)
    chain = [
        _player_cast(1, spell_name='Combust Poison'),
        _pre_damage(2, 1, target, spell='Combust Poison',
                     dtype='Fire', dmg_pre=3, dmg_post=3),
        _damage_full(3, 1, target, spell='Combust Poison',
                      dtype='Fire', damage=3),
        _buff_apply(4, 1, target, buff_name='Poisoned', turns_left=5,
                    buff_type=2),
    ]
    out = compose_digest(chain)
    assert "Goblin (3,4) poisoned, 5 turns." in out
    assert "1 surviving" in out


# ---- §4.4(a) streamlined-side heals split per source ----


def test_streamlined_side_splits_heals_per_source():
    w = _wizard_caster_snap()
    chain = [
        _heal_event(2, 1, w, source='Regeneration', amount=3),
        _heal_event(3, 1, w, source='Stone Mask', amount=5),
        _heal_event(4, 1, w, source='Regeneration', amount=2),
    ]
    out = _format_streamlined_side(chain)
    assert "Healed 5 HP from Regeneration" in out  # 3 + 2 summed per source
    assert "Healed 5 HP from Stone Mask" in out


def test_streamlined_side_heal_unknown_source_fallback():
    w = _wizard_caster_snap()
    out = _format_streamlined_side([_heal_event(2, 1, w, source=None, amount=4)])
    assert "Healed 4 HP" in out
    assert "from" not in out


# ---- §4.4(b) _build_target_hits LIFO pairing (nested deal_damage) ----


def _pre(seq, tgt, source, dtype, dmg):
    return {'sequence': seq, 'parent': None,
            'event_type': 'EventOnPreDamaged',
            'payload': {'target': tgt, 'source_name': source,
                        'damage_type': dtype, 'damage_pre_resist': dmg,
                        'damage_post_resist': dmg, 'resisted': False},
            'marks': []}


def _dmg(seq, tgt, amount):
    return {'sequence': seq, 'parent': None, 'event_type': 'EventOnDamaged',
            'payload': {'target': tgt, 'damage': amount}, 'marks': []}


def test_build_target_hits_lifo_nested_pairing():
    # Nested deal_damage on one target: Pre(outer), Pre(inner), Dmg(inner),
    # Dmg(outer). LIFO pairs each Damaged with the most-recent pre, so no
    # hit is dropped and attribution stays correct.
    tgt = {'id': 777, 'name': 'Ogre'}
    chain = [
        _pre(1, tgt, 'Fireball', 'Fire', 10),
        _pre(2, tgt, 'Thorns', 'Physical', 3),
        _dmg(3, tgt, 3),    # inner -> Thorns
        _dmg(4, tgt, 10),   # outer -> Fireball
    ]
    hits = _build_target_hits(chain)[777]
    assert len(hits) == 2  # neither hit dropped
    assert hits[0]['spell'] == 'Thorns' and hits[0]['dtype'] == 'Physical'
    assert hits[1]['spell'] == 'Fireball' and hits[1]['dtype'] == 'Fire'


def test_build_target_hits_simple_pair_unchanged():
    tgt = {'id': 778, 'name': 'Imp'}
    chain = [_pre(1, tgt, 'Fireball', 'Fire', 8), _dmg(2, tgt, 8)]
    hits = _build_target_hits(chain)[778]
    assert len(hits) == 1
    assert hits[0]['spell'] == 'Fireball'
    assert hits[0]['damage_dealt'] == 8


def test_build_target_hits_excludes_wizard():
    """REGRESSION: a retaliation hit on the wizard inside the player's own
    cast chain (Thorns) must NOT enter the digest's per-target hits — crisis
    owns wizard damage-taken. Otherwise the digest re-narrates it as a
    Surviving/Killed line alongside crisis (double narration)."""
    wiz = {'id': 100, 'name': 'Wizard', 'is_player_controlled': True,
           'tier': 'wizard'}
    enemy = {'id': 778, 'name': 'Ogre', 'is_player_controlled': False}
    chain = [
        _pre(1, enemy, 'Magic Missile', 'Arcane', 30), _dmg(2, enemy, 30),
        _pre(3, wiz, 'Thorns', 'Physical', 3), _dmg(4, wiz, 3),
    ]
    hits = _build_target_hits(chain)
    assert 100 not in hits          # wizard excluded
    assert 778 in hits              # enemy still present


def test_killed_section_excludes_wizard_death():
    """REGRESSION: wizard death in a player chain is crisis's ('Wizard
    died.'), not a digest 'N killed: Wizard' line."""
    wiz = _target_snap(100, name='Wizard', tier='wizard')
    enemy = _target_snap(778, name='Ogre', tier='minion')
    chain = [
        _pre_damage(1, None, enemy, spell='Magic Missile', dmg_pre=65, dmg_post=65),
        _damage_full(2, None, enemy, spell='Magic Missile', damage=65),
        _death(3, None, enemy),
        # Wizard dies to retaliation in the same chain.
        _pre_damage(4, None, wiz, spell='Thorns', dtype='Physical', dmg_pre=99, dmg_post=99),
        _damage_full(5, None, wiz, spell='Thorns', dtype='Physical', damage=99),
        _death(6, None, wiz, killing_source='Thorns'),
    ]
    out = compose_killed_section(chain)
    assert 'Wizard' not in out
    assert '1 killed' in out        # only the Ogre counts
    assert 'Ogre' in out


def test_surviving_section_excludes_wizard():
    """REGRESSION: a non-fatal retaliation hit on the wizard is crisis's, not
    a digest Surviving line."""
    wiz = _target_snap(100, name='Wizard', tier='wizard', cur_hp=20, max_hp=50)
    chain = [
        _pre_damage(1, None, wiz, spell='Thorns', dtype='Physical', dmg_pre=3, dmg_post=3),
        _damage_full(2, None, wiz, spell='Thorns', dtype='Physical', damage=3),
    ]
    out = compose_surviving_section(chain)
    assert 'Wizard' not in out


# ---- Shield sections (R3): granted / stripped / wizard self-gain ----


def _u(name, team=1, tier='minion', pid=None, x=3, y=4, player=False):
    return {'id': pid, 'name': name, 'team': team, 'tier': tier,
            'is_player_controlled': player, 'x': x, 'y': y}


def _sg(target, amount=3, after=3):
    return {'sequence': 0, 'parent': None, 'event_type': 'shield_gained',
            'payload': {'target': target, 'amount': amount, 'shields_after': after},
            'marks': []}


def _ss(target, removed=2, marks=None):
    return {'sequence': 0, 'parent': None, 'event_type': 'shield_stripped',
            'payload': {'target': target, 'amount_removed': removed},
            'marks': marks or []}


def _wiz_cast(spell="Shield Allies"):
    # supplies wizard team for ally classification via _find_wizard_team
    return _player_cast(1, spell)


def test_shields_granted_single_ally():
    chain = [_wiz_cast(), _sg(_u('Wolf', team=0, pid=1))]
    assert compose_shields_granted_section(chain) == \
        "Shields granted: Ally Wolf (3,4) gained 3 shields."


def test_shields_granted_collapses_by_type_with_each():
    chain = [_wiz_cast(),
             _sg(_u('Wolf', team=0, pid=1, x=3, y=4)),
             _sg(_u('Wolf', team=0, pid=2, x=4, y=4)),
             _sg(_u('Wolf', team=0, pid=3, x=5, y=4))]
    assert compose_shields_granted_section(chain) == \
        "Shields granted: 3 Ally Wolves at (3,4), (4,4), (5,4) gained 3 shields each."


def test_shields_granted_subdivides_by_unit_type():
    chain = [_wiz_cast(),
             _sg(_u('Wolf', team=0, pid=1)),
             _sg(_u('Wolf', team=0, pid=2)),
             _sg(_u('Spider', team=0, pid=3), amount=2, after=2)]
    out = compose_shields_granted_section(chain)
    assert '2 Ally Wolves' in out and 'gained 3 shields each' in out
    assert 'Ally Spider' in out and 'gained 2 shields' in out


# ---- team-flip section (R2): "turned friendly" / "turned hostile", no prefix ----


def _tj(target):
    # enemy -> player (Dominate/conversion)
    return {'sequence': 0, 'parent': None, 'event_type': 'team_joined',
            'payload': {'target': target, 'team_before': 1, 'team_after': 0},
            'marks': []}


def _tt(target):
    # player -> enemy (forfeit/betrayal)
    return {'sequence': 0, 'parent': None, 'event_type': 'team_turned',
            'payload': {'target': target, 'team_before': 0, 'team_after': 1},
            'marks': []}


def test_team_joined_single_no_prefix():
    chain = [_wiz_cast("Dominate"), _tj(_u('Ogre', team=0, pid=1))]
    assert compose_team_changes_section(chain) == "Ogre (3,4) turned friendly."


def test_team_joined_collapses_by_name():
    chain = [_wiz_cast("Dominate"),
             _tj(_u('Ogre', team=0, pid=1, x=3, y=4)),
             _tj(_u('Ogre', team=0, pid=2, x=4, y=4)),
             _tj(_u('Ogre', team=0, pid=3, x=5, y=4))]
    assert compose_team_changes_section(chain) == \
        "3 Ogres at (3,4), (4,4), (5,4) turned friendly."


def test_team_turned_single():
    chain = [_wiz_cast("Ritual Bound"), _tt(_u('Wolf', team=1, pid=1))]
    assert compose_team_changes_section(chain) == "Wolf (3,4) turned hostile."


def test_team_mixed_directions_both_render():
    chain = [_wiz_cast("Chaos"),
             _tj(_u('Ogre', team=0, pid=1)),
             _tt(_u('Wolf', team=1, pid=2))]
    out = compose_team_changes_section(chain)
    assert "Ogre (3,4) turned friendly." in out
    assert "Wolf (3,4) turned hostile." in out


def test_team_flip_of_dead_target_dropped():
    ogre = _u('Ogre', team=0, pid=7)
    chain = [_wiz_cast("Dominate"), _tj(ogre),
             {'sequence': 0, 'parent': None, 'event_type': 'EventOnDeath',
              'payload': {'target': ogre}, 'marks': []}]
    assert compose_team_changes_section(chain) == ""


def test_shields_granted_same_name_ally_enemy_split_by_team():
    # A charmed enemy 'Wolf' (ally) and a hostile 'Wolf' gaining the same
    # amount must not collapse under one prefix — ally-designation is mandatory.
    chain = [_wiz_cast(),
             _sg(_u('Wolf', team=0, pid=1, x=3, y=4)),
             _sg(_u('Wolf', team=1, pid=2, x=4, y=4))]
    out = compose_shields_granted_section(chain)
    assert 'Ally Wolf (3,4) gained 3 shields' in out
    assert 'Wolf (4,4) gained 3 shields' in out


def test_shields_granted_empty_when_none():
    assert compose_shields_granted_section([_wiz_cast()]) == ""


def test_shields_stripped_collapses_enemies_no_ally_prefix():
    chain = [_player_cast(1, "Siphon Shields"),
             _ss(_u('Goblin', pid=1, x=7, y=8)),
             _ss(_u('Goblin', pid=2, x=8, y=8))]
    out = compose_shields_stripped_section(chain)
    assert out == "Shields stripped: 2 Goblins at (7,8), (8,8)."
    assert 'Ally' not in out


def test_shields_stripped_skips_block_superseded():
    chain = [_player_cast(1, "Fireball"),
             _ss(_u('Goblin', pid=1), removed=1, marks=['superseded_by_block'])]
    # the only strip is a block's coincident strip -> section is empty
    assert compose_shields_stripped_section(chain) == ""


def test_shields_excludes_dead_targets():
    target = _u('Goblin', pid=1)
    chain = [_player_cast(1, "Siphon Shields"), _ss(target),
             {'sequence': 2, 'parent': None, 'event_type': 'EventOnDeath',
              'payload': {'target': target}, 'marks': []}]
    assert compose_shields_stripped_section(chain) == ""


def test_side_section_wizard_self_gain():
    wiz = _u('Wizard', team=0, tier='wizard', pid=_WIZARD_ID, player=True)
    chain = [_player_cast(1, "Ironskin"), _sg(wiz, amount=3, after=3)]
    out = compose_side_section(chain)
    assert "Shields: gained 3 shields, 3 total." in out


def test_side_section_singular_self_gain():
    wiz = _u('Wizard', team=0, tier='wizard', pid=_WIZARD_ID, player=True)
    chain = [_player_cast(1, "Ironskin"), _sg(wiz, amount=1, after=1)]
    assert "Shields: gained 1 shield, 1 total." in compose_side_section(chain)


def test_self_gain_not_in_granted_section():
    # wizard self-gain belongs to Side, not the granted (others) section
    wiz = _u('Wizard', team=0, tier='wizard', pid=_WIZARD_ID, player=True)
    chain = [_player_cast(1, "Ironskin"), _sg(wiz)]
    assert compose_shields_granted_section(chain) == ""


# ---- Unit 4 (D6): new capture-only kinds are known to digest telemetry ----


def test_unit4_capture_only_kinds_known_to_digest_telemetry():
    # hp_loss / xp_change / EventOnAwakened are capture-only (Unit 4); a chain
    # carrying them (a Word-of-Undeath cast carries one hp_loss per affected
    # unit) must not fire digest_unmodeled unknown_event_types.
    import digest as digest_mod

    for kind in ("hp_loss", "xp_change", "EventOnAwakened", "game_log"):
        assert kind in digest_mod._COMPOSER_KNOWN_EVENT_TYPES

    class _Tel:
        def __init__(self):
            self.calls = []

        def emit(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    tel = _Tel()
    chain = [
        {"event_type": "cast_begin", "payload": {}},
        {"event_type": "hp_loss", "payload": {}},
        {"event_type": "xp_change", "payload": {}},
        {"event_type": "EventOnAwakened", "payload": {}},
        {"event_type": "game_log", "payload": {"template": "{unit} takes ..."}},
    ]
    digest_mod._maybe_emit_unmodeled(tel, 1, chain, ["Cast Fireball."])
    assert tel.calls == []
