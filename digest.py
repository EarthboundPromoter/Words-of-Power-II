"""
Direct-action digest — composer for the new data-model pipeline (phase 3).

Reads journal records belonging to one player-keypress chain (identified by
walking causation parents to a player-controlled root cast) and composes a
single speech utterance summarizing what that keypress did: cast, damage,
kills, procs, and side-effects.

Replaces per-event combat speech from the legacy batcher for events inside
player-cast chains. Out-of-chain events (enemy turns, ambient) continue
through the batcher unchanged. Crisis events (player damage taken, HP
threshold, wizard death) bypass the digest and remain on the batcher's
fast path.

Trigger: fire_if_pending() is called at the is_awaiting_input True
transition (turn boundary), right before batcher.flush() so it can claim
its events first via the mark-and-respect protocol.

Gated by settings.ini: digest_enabled (default false). When the flag is
False the composer is never invoked and the legacy speech path is unchanged.

Spec references (in mod's memory directory):
- design_digest_phrasing.md — canonical output phrasing rules
- design_digest_spatial_summary.md — coords-off rendering rules
- implementation_plan_digest.md — first-pass rollout plan
- design_rw2_data_model.md — architectural spine and journal mechanics
"""

# Note: journal is imported lazily inside fire_if_pending() rather than at
# module level. This keeps the pure helpers below importable from test code
# without dragging in the journal -> Level dependency chain (Level is the
# game module, not available outside the running game).


# Mark stamped on records this composer claims. Future composers respect
# this when deciding whether to skip an event the digest already covered.
# Versioned so the convention can evolve without breaking compatibility
# with composers that respect older marks.
DIGEST_MARK = "digest_v1"


# Priority constant for the unified emitter (pipeline.py). Lower numbers
# sort earlier in the spoken utterance. Crisis sits at 0 (highest);
# digest sits at 100 (player chain narrative); orphan sits at 200
# (ambient/enemy-turn body).
PRIORITY_STANDARD_DIGEST = 100


# ----------------------------------------------------------------------
# Journal helpers — pure functions over journal-record dicts. Kept here
# (rather than in helpers.py) until a second composer needs them, at
# which point we extract per the shared-base deferral in
# implementation_plan_digest.md.
# ----------------------------------------------------------------------


def build_record_index(records):
    """Build a sequence -> record lookup from a journal records list.
    Convenience for repeated parent walks."""
    return {r['sequence']: r for r in records if 'sequence' in r}


# Spell names that are player keypresses but not digestable as combat
# actions. The digest skips chains rooted in these casts; the legacy
# batcher continues handling them however it always did.
#
# - walk: autowalk through cleared levels goes through act_cast and would
#   otherwise produce per-step "Cast walk. No damage." digest output.
# - Mana Potion / Healing Potion / Teleporter: utility item uses fire as
#   spells through act_cast. Empty-chain rendering ("Cast Healing Potion.
#   No damage. Healed 32 HP.") reads awkwardly — heal info comes through
#   the legacy batcher's heal handler, and the player already knows they
#   used the item. Damage-dealing items (Death Dice) are NOT filtered —
#   their digest output is genuinely useful (6-target cascade summary).
_NON_DIGEST_SPELL_NAMES = frozenset({
    "walk",
    "Mana Potion",
    "Healing Potion",
    "Teleporter",
})


def is_player_keypress_cast(record):
    """True iff record is a cast_begin event the digest should treat as
    a player-action chain root — caster is player-controlled, parent is
    None (handled by the caller's parent check, not here), and the spell
    name is not in the non-digest filter.

    Includes autofires (pay_costs=False, e.g., Explosive Spore Manual
    amulet firing Combust Poison via RepeaterCast on_advance). These ARE
    chain roots from the digest's perspective: the wizard caused them by
    holding the item, the listener is entitled to know what happened.
    Empty autofires (cast with no resulting damage / death / spawn /
    buff) are silenced inside compose_digest, not by pre-filter — that
    way effective autofires ("Cast Combust Poison. 5 killed: ...") still
    speak. See user-articulated principle in feedback memory:
    feedback_capture_separate_from_render.md.

    Why the spell-name filter: some player keypresses (currently just
    "walk") aren't combat-relevant decisions. Autowalk fires walk casts
    that would render as "Cast walk. No damage." every step otherwise.
    Filtering by name keeps the schema clean (no separate event type
    needed) and is easy to extend if other noise spells appear.

    Recursive procs that use act_cast(..., pay_costs=False) (e.g.,
    Annihilate's Assured Destruction recasting itself) are not roots —
    they have a non-None parent — so they're filtered by parent checks
    in the callers, not by pay_costs here."""
    if not record:
        return False
    if record.get('event_type') != 'cast_begin':
        return False
    payload = record.get('payload') or {}
    if not payload.get('is_player'):
        return False
    spell = payload.get('spell') or {}
    if spell.get('name') in _NON_DIGEST_SPELL_NAMES:
        return False
    return True


def find_pending_root(records, last_digested_seq):
    """Find the most recent player-keypress chain root in `records` that
    hasn't been digested yet, or None if no chain is pending.

    "Pending" means a cast_begin record with payload.is_player=True and
    parent=None (the chain root, not a Multimancy proc) whose sequence
    exceeds `last_digested_seq`.

    Args:
        records: list of journal record dicts (typically journal.records).
        last_digested_seq: sequence of the most recently digested keypress
            root, or None if no digest has fired yet this session.

    Returns:
        The pending keypress root record (highest sequence among
        candidates), or None.

    Why "most recent" rather than "oldest pending": the digest fires at
    each is_awaiting_input boundary to summarize the chain that just
    completed. If multiple undigested keypress roots exist (a missed
    prior fire, or some rapid in-turn pattern we haven't anticipated),
    the chain closest to the boundary is the one the player just
    executed. Older pending roots are dropped silently — one digest per
    boundary. Telemetry should surface cases where this drops material
    content; revisit if it happens.
    """
    threshold = last_digested_seq if last_digested_seq is not None else -1
    best = None
    best_seq = threshold
    for rec in records:
        # Roots only — descended events have parent set.
        if rec.get('parent') is not None:
            continue
        # Single source of keypress definition (event_type, is_player,
        # spell name); see is_player_keypress_cast.
        if not is_player_keypress_cast(rec):
            continue
        seq = rec.get('sequence')
        if seq is None or seq <= best_seq:
            continue
        best = rec
        best_seq = seq
    return best


def find_all_pending_roots(records, last_digested_seq):
    """Return every player-keypress chain root in `records` that hasn't
    been digested yet, in sequence order (oldest first).

    The single-cast common case has length 0 or 1. Multi-root cases are
    rare — they appear when more than one keypress chain completes
    inside a single is_awaiting_input window (the original Talking Hat
    quickcast scenario flagged by the turn-resolution research). The
    composer iterates all returned roots and emits one digest per chain
    in chronological order so each cast gets its own narration.
    """
    threshold = last_digested_seq if last_digested_seq is not None else -1
    pending = []
    for rec in records:
        if rec.get('parent') is not None:
            continue
        if not is_player_keypress_cast(rec):
            continue
        seq = rec.get('sequence')
        if seq is None or seq <= threshold:
            continue
        pending.append((seq, rec))
    pending.sort(key=lambda x: x[0])
    return [r for (_, r) in pending]


def walk_to_keypress_root(record, records_by_seq):
    """Walk causation parents upward from `record` until reaching the
    chain root (a record whose parent is None). If that root is a
    player-controlled cast_begin event, return it. Otherwise return None.

    Args:
        record: a journal record dict, or None.
        records_by_seq: dict mapping sequence -> record for parent lookup
            (build via build_record_index).

    Returns:
        The player-keypress root record if the lineage roots in one,
        else None.

    Why "root only" rather than "first player cast in lineage": in RW2's
    queued-cast model, a chain begins with one `act_cast` call from player
    input (parent=None) and all proc-spawned casts (Multimancy, retaliation,
    conversion, echo) inherit it as parent. Multimancy procs *are* cast
    by the wizard so their `is_player` flag is True, but they are not
    keypress events — they have a parent. The unique distinguishing fact
    of a keypress root is parent=None.

    Why this exists: the journal's `action_chain_id` increments per
    `act_cast` call, including those proc-spawned casts. Filtering events
    by action_chain_id alone groups the wrong events. The digest needs
    to gather everything causally descending from the original player
    keypress, which requires walking `parent` links rather than equating
    chain ids."""
    if not record:
        return None
    cur = record
    seen = set()
    while cur is not None:
        seq = cur.get('sequence')
        if seq is None or seq in seen:
            # Defensive: malformed record, or cycle (shouldn't happen but
            # protects against pathological data).
            return None
        seen.add(seq)

        parent_seq = cur.get('parent')
        if parent_seq is None:
            # Reached the chain root. Is it a player keypress?
            if is_player_keypress_cast(cur):
                return cur
            # Root is something else (enemy cast, ambient event, etc.) —
            # this lineage doesn't trace to a player keypress.
            return None
        cur = records_by_seq.get(parent_seq)

    return None


def _format_cast_list(spells):
    """Run-length encode consecutive identical spell names into a
    comma-separated list with "times N" for runs.

    Examples:
        ['Magic Missile'] -> 'Magic Missile'
        ['Magic Missile', 'Magic Missile'] -> 'Magic Missile times 2'
        ['Blink', 'Disperse', 'Magic Missile', 'Magic Missile']
            -> 'Blink, Disperse, Magic Missile times 2'
        ['Magic Missile', 'Blink', 'Magic Missile']
            -> 'Magic Missile, Blink, Magic Missile'  (not consecutive)
    """
    if not spells:
        return ""
    runs = [(spells[0], 1)]
    for s in spells[1:]:
        if runs[-1][0] == s:
            runs[-1] = (s, runs[-1][1] + 1)
        else:
            runs.append((s, 1))
    return ", ".join(
        f"{name} times {n}" if n > 1 else name
        for name, n in runs
    )


def compose_cast_section(chain):
    """Compose the Cast section as a single sentence.

    Returns "Cast Spell A, Spell B, Spell C times N." Adjacent same-spell
    casts collapse via run-length encoding. Returns "" if the chain has
    no cast_begin records.

    Args:
        chain: list of records from gather_chain_events, in sequence order.

    Returns:
        Single sentence ending in "." or empty string.

    Singular-event interleaving (pickups, contacts, charge-state changes
    breaking the cast cluster into multiple "Cast..." sentences per
    design_digest_phrasing.md) lands in a follow-up commit. For now,
    every cast_begin in the chain joins one comma-list under a single
    "Cast" verb regardless of any interleaved non-cast events.
    """
    spells = []
    root_is_channel_continuation = False
    for rec in chain:
        if rec.get('event_type') != 'cast_begin':
            continue
        payload = rec.get('payload') or {}
        spell = payload.get('spell') or {}
        name = spell.get('name')
        if not name:
            continue
        # The first cast_begin in the chain (sequence-ordered) is the root.
        # Its is_channel_continuation flag determines the section verb:
        # "Channeled" for channel ticks, "Cast" otherwise. Procs further
        # down the chain don't change the verb — the verb describes the
        # player's action, not each individual spell event.
        if not spells:
            root_is_channel_continuation = bool(payload.get('is_channel_continuation'))
        spells.append(name)
    if not spells:
        return ""
    verb = "Channeled" if root_is_channel_continuation else "Cast"
    return verb + " " + _format_cast_list(spells) + "."


# ----------------------------------------------------------------------
# Killed section composition
#
# Per design_digest_phrasing.md:
# - Header: count-led, "N killed:".
# - Lines: one per equivalence class, target-leading.
# - Tier 1 (boss / spawner): individuated, never grouped.
# - Tier 2 (minion): grouped by (name, hit-history signature).
# - Single-target: "Target (x,y): Spell N Dtype."
# - Multi-target:  "N TargetPlural at (x1,y1), (x2,y2): Spell M Dtype."
# - Same-spell same-dtype repeats: "Spell A plus B plus C Dtype."
# - Different spell or different dtype: comma-separated.
#
# First-pass scope (deferred to follow-up commits):
# - Buff/debuff phrasing on equivalence-class signature and rendering.
# - Reactive-event folding (shield breaks on adjacent units).
# - Coords-off rendering (uses pie-slice spatial summary).
# - "blocked by N shields" outcome (more relevant for Surviving).
# ----------------------------------------------------------------------


def _build_target_hits(chain):
    """Walk the chain in sequence order and assemble the hit history per
    target id. A "hit" is a PreDamaged → Damaged pair (non-shielded) or a
    PreDamaged → ShieldRemoved pair (shielded; damage absorbed).

    Pairing is by adjacency on a per-target queue: when a PreDamaged for
    target T fires, it stays pending until either Damaged or ShieldRemoved
    on T arrives. This matches the deal_damage flow (Level.py:4179-4271)
    where each damage attempt fires PreDamaged first, then either a
    ShieldRemoved (shielded) or Damaged (delivered).

    Returns: target_id -> list of hit dicts in sequence order.
        Each hit: {'spell', 'dtype', 'damage_post_resist', 'damage_dealt',
                   'resisted', 'shielded'}.
    """
    hits_by_target = {}
    # Per-target STACK of pending PreDamaged. deal_damage nests (a PreDamaged
    # handler can deal damage to the same target before the outer hit
    # resolves), so a Damaged/ShieldRemoved pairs with the MOST RECENT
    # pending pre — LIFO. A single slot dropped the outer hit; a FIFO queue
    # would mispair inner vs outer. Normal (non-nested) hits leave exactly
    # one item on the stack, so this matches the prior behavior exactly.
    pending_pre_by_target = {}

    for r in chain:
        et = r.get('event_type')
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        target_id = target.get('id')
        if target_id is None:
            continue
        # claim==render: wizard damage-taken is crisis's lane, never the
        # digest's. A retaliation hit on the wizard during the player's own
        # cast (Thorns, etc.) parents into this keypress chain; excluding it
        # here stops the digest re-narrating it as a Killed/Surviving line
        # alongside crisis. The mod registers a global EventOnPreDamaged
        # handler, so the wizard's in-chain hits DO produce paired records.
        if target.get('is_player_controlled'):
            continue

        if et == 'EventOnPreDamaged':
            pending_pre_by_target.setdefault(target_id, []).append(r)
        elif et == 'EventOnDamaged':
            stack = pending_pre_by_target.get(target_id)
            pre = stack.pop() if stack else None
            if pre is None:
                continue
            pre_payload = pre.get('payload') or {}
            hits_by_target.setdefault(target_id, []).append({
                'spell': pre_payload.get('source_name'),
                'dtype': pre_payload.get('damage_type'),
                'damage_pre_resist': pre_payload.get('damage_pre_resist'),
                'damage_post_resist': pre_payload.get('damage_post_resist'),
                'damage_dealt': payload.get('damage'),
                'resisted': bool(pre_payload.get('resisted')),
                'shielded': False,
            })
        elif et == 'EventOnShieldRemoved':
            stack = pending_pre_by_target.get(target_id)
            pre = stack.pop() if stack else None
            if pre is None:
                continue
            pre_payload = pre.get('payload') or {}
            hits_by_target.setdefault(target_id, []).append({
                'spell': pre_payload.get('source_name'),
                'dtype': pre_payload.get('damage_type'),
                'damage_pre_resist': pre_payload.get('damage_pre_resist'),
                'damage_post_resist': pre_payload.get('damage_post_resist'),
                'damage_dealt': 0,
                'resisted': False,
                'shielded': True,
            })

    return hits_by_target


def _displayed_damage(hit):
    """The damage number to SPEAK for a landed hit: the actually-dealt amount
    (EventOnDamaged.damage — post-resist AND clamped to the target's remaining
    HP at the killing blow), which is what the game's own combat log prints.
    Falls back to the pre-clamp post-resist value only if the dealt amount
    wasn't captured. For a survivor the two are identical (no clamp); only an
    overkill kill-line differs, and there the clamped value is what RW2 and the
    crisis/orphan producers already show. Resist/vulnerability tags are derived
    separately (pre vs post) and are unaffected."""
    dealt = hit.get('damage_dealt')
    return dealt if dealt is not None else hit.get('damage_post_resist')


def _hit_signature(hits):
    """Equivalence-class signature for a target's hit history. Two targets
    merge iff their signatures match exactly (per design_digest_phrasing.md
    'no-approximation' rule). Keyed on the DISPLAYED (clamped) damage so the
    merge agrees with what is spoken: survivors are unaffected (no HP clamp, so
    dealt == post-resist), but two overkilled units that took DIFFERENT actual
    damage (different remaining HP) no longer merge into one line with a single
    arbitrary number. Buff/debuff state will extend this signature in a
    follow-up commit.

    Shielded (blocked) hits key on the would-have-been post-resist magnitude,
    not the displayed (zero) dealt amount — now that the blocked figure is
    spoken, two survivors that blocked DIFFERENT magnitudes must not merge into
    one line carrying a single arbitrary 'blocked' number."""
    return tuple(
        (h.get('spell'), h.get('dtype'),
         h.get('damage_post_resist') if h.get('shielded') else _displayed_damage(h),
         h.get('resisted'), h.get('shielded'))
        for h in hits
    )


def _format_hits(hits):
    """Render a hit list as 'Spell A [plus B...] Dtype, Spell C D Dtype2'.
    Adjacent hits with the same (spell, dtype) collapse into a 'plus'-joined
    damage list per design_digest_phrasing.md 'Magic Missile 71 plus 17 Arcane'.

    Shielded hits are skipped here — kills via shielded hits are rare and
    the more important shield-related rendering ('blocked by N shields')
    lives on the Surviving section. If/when a kill chain includes shielded
    hits we'll revisit the rendering."""
    groups = []  # list of [(spell, dtype), [damage1, damage2, ...]]
    for hit in hits:
        if hit.get('shielded'):
            continue
        spell = hit.get('spell')
        dtype = hit.get('dtype')
        damage = _displayed_damage(hit)
        if spell is None or damage is None:
            continue
        key = (spell, dtype)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(damage)
        else:
            groups.append([key, [damage]])

    parts = []
    for (spell, dtype), damages in groups:
        damage_str = " plus ".join(str(d) for d in damages)
        if dtype:
            parts.append(f"{spell} {damage_str} {dtype}")
        else:
            parts.append(f"{spell} {damage_str}")
    return ", ".join(parts)


def _format_coord_list(members):
    """Render the coord portion of a multi-target phrase, with same-coord
    collapse where applicable.

    Three render forms keyed on the (coord -> count) distribution:
    - All members at one coord: ' at (x,y)'.
    - Multiple coords, all unique: ' at (x1,y1), (x2,y2), ...' (verbatim).
    - Multiple coords with duplicates: ', A at (x1,y1), B at (x2,y2), ...'.

    The duplicate case surfaces sequential spawn-die-spawn patterns (Bag
    of Bugs spawns 4 Fly Swarms, PoR cascade kills 3 — all 3 deaths
    recorded at the same tile because the tile keeps freeing up between
    spawns). The 'A at (x,y)' explicit count preserves the cycle
    information without the listener having to dedupe duplicate coords.

    Returns the string starting with leading space or comma, ready to
    append to '{count} {plural}'.
    """
    coords_in_order = []
    counts = {}
    for m in members:
        coord = (m.get('x'), m.get('y'))
        if coord not in counts:
            coords_in_order.append(coord)
            counts[coord] = 0
        counts[coord] += 1

    if len(coords_in_order) == 1:
        x, y = coords_in_order[0]
        return f" at ({x},{y})"

    has_dupe = any(c > 1 for c in counts.values())
    if has_dupe:
        parts = [f"{counts[c]} at ({c[0]},{c[1]})" for c in coords_in_order]
        return ", " + ", ".join(parts)
    parts = [f"({c[0]},{c[1]})" for c in coords_in_order]
    return " at " + ", ".join(parts)


def _format_target_phrase(target, count, members):
    """Render the target phrase for an equivalence-class line:
    Single: 'Target (x,y)'. Multi: 'N TargetPlural{coord_list}' where
    coord_list collapses same-coord entries.
    Coords-on path; coords-off pie-slice rendering lands in a follow-up."""
    name = target.get('name') or 'Unknown'
    if count <= 1:
        x, y = target.get('x'), target.get('y')
        return f"{name} ({x},{y})"

    # Lazy import keeps digest.py free of import-time dependencies on
    # helpers.py for tests that only exercise the simpler functions.
    from helpers import _pluralize
    plural = _pluralize(name)
    return f"{count} {plural}{_format_coord_list(members)}"


def compose_killed_section(chain):
    """Compose the Killed section: count-led header plus one line per
    equivalence class.

    Returns a single string ('N killed: line1. line2. ...') or empty
    string if nothing died in this chain.

    Tier 1 (boss/spawner) targets are individuated — each death is its
    own class. Tier 2 (minion) targets group by (name, hit_signature).
    Per design_digest_phrasing.md the no-range-form rule applies: variable
    hit counts split into sub-classes per exact count.
    """
    # Wizard death is crisis's ("Wizard died."), not a digest kill line.
    deaths = [
        r for r in chain
        if r.get('event_type') == 'EventOnDeath'
        and not ((r.get('payload') or {}).get('target') or {}).get(
            'is_player_controlled')
    ]
    if not deaths:
        return ""

    hits_by_target = _build_target_hits(chain)

    # Build classes preserving the order deaths appeared in the chain.
    # Tier 1 deaths each become their own class, in death order.
    # Tier 2 deaths group by (name, signature); the group anchors at the
    # first death's position in the death order.
    classes = []                  # list of class dicts in section order
    tier2_lookup = {}             # (name, sig) -> class dict reference

    for death in deaths:
        payload = death.get('payload') or {}
        target = payload.get('target') or {}
        target_id = target.get('id')
        tier = target.get('tier', 'minion')
        hits = hits_by_target.get(target_id, [])

        if tier in ('boss', 'spawner', 'wizard'):
            classes.append({
                'target': target,
                'hits': hits,
                'count': 1,
                'members': [target],
            })
        else:
            sig = _hit_signature(hits)
            key = (target.get('name'), sig)
            existing = tier2_lookup.get(key)
            if existing is None:
                cls = {
                    'target': target,
                    'hits': hits,
                    'count': 1,
                    'members': [target],
                }
                classes.append(cls)
                tier2_lookup[key] = cls
            else:
                existing['count'] += 1
                existing['members'].append(target)

    total = len(deaths)
    lines = []
    for cls in classes:
        target_phrase = _format_target_phrase(
            cls['target'], cls['count'], cls['members']
        )
        hit_str = _format_hits(cls['hits'])
        if hit_str:
            lines.append(f"{target_phrase}: {hit_str}.")
        else:
            # Defensive: target died without recorded hits in chain (rare;
            # could happen if death event is in the chain but its damage
            # event is out of chain — shouldn't normally occur).
            lines.append(f"{target_phrase}.")

    return f"{total} killed: " + " ".join(lines)


# ----------------------------------------------------------------------
# Surviving section composition
#
# Same shape as Killed but for targets that took damage and did NOT die
# in this chain. Adds outcome suffixes:
# - "(resisted)" when any hit had post-resist < pre-resist
# - "(vulnerable)" when any hit had post-resist > pre-resist
# - "blocked by N shields" reports the blocked hits + their would-have-been
#   magnitude (post-resist), counted off EventOnShieldRemoved on the target
#
# Hit-history rendering uses an additional form for survivors, per
# design_digest_phrasing.md: "Spell N hits, X Dtype each" when a single
# (spell, dtype) group has uniform damage values across multiple hits.
# Different damage values keep the "plus" form from Killed.
# ----------------------------------------------------------------------


def _render_damage_groups(group_order, groups):
    """Render (spell, dtype) -> [damages] groups as 'Spell A [plus B... | N
    hits, X each] Dtype' parts in first-seen order. Shared by the landed-hit
    and blocked-hit rendering in _format_hits_with_outcome."""
    parts = []
    for key in group_order:
        spell, dtype = key
        damages = groups[key]
        if len(damages) > 1 and len(set(damages)) == 1:
            uniform = damages[0]
            if dtype:
                parts.append(f"{spell} {len(damages)} hits, {uniform} {dtype} each")
            else:
                parts.append(f"{spell} {len(damages)} hits, {uniform} each")
        else:
            damage_str = " plus ".join(str(d) for d in damages)
            if dtype:
                parts.append(f"{spell} {damage_str} {dtype}")
            else:
                parts.append(f"{spell} {damage_str}")
    return parts


def _format_hits_with_outcome(hits):
    """Render a survivor's hit list as 'Spell A [plus B... or N hits, X each]
    Dtype' per group, with blocked hits rendered as their own '... blocked by N
    shields' clause carrying the would-have-been magnitude (crisis parity).

    Groups by (spell, dtype) across the WHOLE hit list (not just adjacent
    runs) — necessary for spells like Annihilate that fire interleaved
    F/L/P damage elements per cast. With pure adjacency-grouping a 14-cast
    Annihilate on a survivor produces 42 separate entries; with
    full grouping it collapses to three lines, one per damage type.

    Damages within a group are listed in original order, so the 'plus'
    form for varying values still preserves chronology.

    Returns (damage_part, shield_part, shield_count). damage_part renders the
    LANDED hits; shield_part renders the BLOCKED hits' would-have-been
    post-resist magnitudes (the figure crisis speaks) under the same grouping
    grammar; shield_count is the number of blocked hits. The caller composes the
    'blocked by N shields' clause and the resisted/vulnerable suffixes at the
    line level.

    Killed lines use the separate _format_hits helper which keeps strict
    adjacency-based grouping per the phrasing spec's 'Magic Missile 71
    plus 17 Arcane' example — kill phrasing is short enough that ordered
    plus form reads cleanly.
    """
    groups = {}      # (spell, dtype) -> list of damages (landed)
    group_order = [] # first-seen ordering of keys, for stable rendering
    shield_groups = {}      # (spell, dtype) -> list of would-have-been amounts
    shield_group_order = []
    shield_count = 0
    for hit in hits:
        if hit.get('shielded'):
            shield_count += 1
            spell = hit.get('spell')
            # The blocked figure is the post-resist amount that WOULD have
            # landed (deal_damage negates a shielded hit whole), not the dealt
            # amount (0). Same number the crisis block line speaks.
            amount = hit.get('damage_post_resist')
            if spell is not None and amount is not None:
                key = (spell, hit.get('dtype'))
                if key not in shield_groups:
                    shield_group_order.append(key)
                    shield_groups[key] = []
                shield_groups[key].append(amount)
            continue
        spell = hit.get('spell')
        dtype = hit.get('dtype')
        damage = _displayed_damage(hit)
        if spell is None or damage is None:
            continue
        key = (spell, dtype)
        if key not in groups:
            group_order.append(key)
            groups[key] = []
        groups[key].append(damage)

    damage_part = ", ".join(_render_damage_groups(group_order, groups))
    shield_part = ", ".join(_render_damage_groups(shield_group_order, shield_groups))
    return damage_part, shield_part, shield_count


def _block_clause(shield_part, shield_count):
    """The blocked-hits clause: 'Fire Bolt 12 Fire blocked by 1 shield', or the
    bare 'blocked by N shields' when the magnitude wasn't captured. Assumes
    shield_count > 0. 'blocked' unifies with crisis, the orphan non-wizard
    block line, and the game's own DMG_BLOCKED log."""
    shield_word = "shield" if shield_count == 1 else "shields"
    tail = f"blocked by {shield_count} {shield_word}"
    return f"{shield_part} {tail}" if shield_part else tail


def _outcome_flags(hits):
    """Return (any_resisted, any_vulnerable) over a target's non-shielded
    hits. Used to compose '(resisted)' / '(vulnerable)' suffixes."""
    any_resisted = False
    any_vulnerable = False
    for hit in hits:
        if hit.get('shielded'):
            continue
        if hit.get('resisted'):
            any_resisted = True
        pre = hit.get('damage_pre_resist')
        post = hit.get('damage_post_resist')
        if pre is not None and post is not None and post > pre:
            any_vulnerable = True
    return any_resisted, any_vulnerable


def _surviving_signature(hits, cur_hp):
    """Equivalence-class signature for survivors. Extends Killed's
    hit-history signature with post-chain HP per the spec's 'final state'
    component. Buff/debuff state will join this signature in a follow-up."""
    return (_hit_signature(hits), cur_hp)


def compose_surviving_section(chain):
    """Compose the Surviving section: count-led header plus equivalence-
    class lines for targets that took damage but didn't die in this chain.

    Returns a single string ('N surviving: line1. line2. ...') or empty
    string if nothing survived (or nothing took damage).
    """
    deaths = {
        (r.get('payload') or {}).get('target', {}).get('id')
        for r in chain if r.get('event_type') == 'EventOnDeath'
    }
    deaths.discard(None)

    hits_by_target = _build_target_hits(chain)

    # Latest snapshot per target — used for post-chain HP and coord
    # rendering. Walks chain in order so the last-seen snapshot wins.
    target_last_snap = {}
    for r in chain:
        et = r.get('event_type')
        if et not in ('EventOnPreDamaged', 'EventOnDamaged', 'EventOnShieldRemoved'):
            continue
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        tid = target.get('id')
        if tid is not None:
            target_last_snap[tid] = target

    # Survivors took damage (in hits_by_target) but didn't die.
    survivor_ids = [
        tid for tid in hits_by_target.keys()
        if tid not in deaths and tid is not None
    ]
    if not survivor_ids:
        return ""

    classes = []
    tier2_lookup = {}

    for tid in survivor_ids:
        target = target_last_snap.get(tid, {})
        tier = target.get('tier', 'minion')
        hits = hits_by_target.get(tid, [])
        cur_hp = target.get('cur_hp')

        if tier in ('boss', 'spawner', 'wizard'):
            classes.append({
                'target': target,
                'hits': hits,
                'count': 1,
                'members': [target],
            })
        else:
            sig = _surviving_signature(hits, cur_hp)
            key = (target.get('name'), sig)
            existing = tier2_lookup.get(key)
            if existing is None:
                cls = {
                    'target': target,
                    'hits': hits,
                    'count': 1,
                    'members': [target],
                }
                classes.append(cls)
                tier2_lookup[key] = cls
            else:
                existing['count'] += 1
                existing['members'].append(target)

    total = sum(cls['count'] for cls in classes)
    lines = []
    for cls in classes:
        target_phrase = _format_target_phrase(
            cls['target'], cls['count'], cls['members']
        )
        damage_part, shield_part, shield_count = _format_hits_with_outcome(cls['hits'])
        any_resisted, any_vulnerable = _outcome_flags(cls['hits'])

        clauses = []
        if damage_part:
            clauses.append(damage_part)

        suffixes = []
        if any_resisted:
            suffixes.append("(resisted)")
        if any_vulnerable:
            suffixes.append("(vulnerable)")

        # Blocked hits now carry the would-have-been magnitude + source
        # (shield_part). Shielded-only → the block leads as the line's clause;
        # mixed with landed damage → it trails after the resist/vuln tags, the
        # prior placement.
        if shield_count > 0:
            block = _block_clause(shield_part, shield_count)
            if damage_part:
                suffixes.append(block)
            else:
                clauses.append(block)

        body = ", ".join(clauses + suffixes)
        if body:
            lines.append(f"{target_phrase}: {body}.")
        else:
            lines.append(f"{target_phrase}.")

    return f"{total} surviving: " + " ".join(lines)


# ----------------------------------------------------------------------
# Side section composition
#
# Per design_digest_phrasing.md:
# "Side" announces the section; sub-labels organize:
# - "Heals:" — player-targeted heal events. Cause-first form:
#   "Source N HP" with wizard as the implicit target. Multiple heals
#   from the same source aggregate (no "twice" abstractions).
# - "Buffs:" — wizard-facing buff apply / remove / fade. Each buff
#   statement is its own period-separated sentence within Buffs:.
#   Stack-type drives phrasing per the buff/debuff phrasing rules.
# - "Charges:" — DEFERRED. The game has no EventOnChargeRestored;
#   capturing charge restoration needs a separate Level patch.
#   Tracked in parked_batcher_passive_autocast_noise / digest plan.
#
# First-pass scope:
# - Heal aggregation by source.
# - Buff applications on wizard, stack-aware phrasing.
# - Buff refreshes (STACK_NONE / STACK_DURATION re-applies are silent
#   in apply_buff per Level.py:2120-2128) — DEFERRED with the journal
#   refresh-detection follow-up commit.
# - Buff expirations / fading — DEFERRED (handled when buff/debuff
#   phrasing is added to equivalence-class lines too).
# - Buff transformations (STACK_TYPE_TRANSFORM) — DEFERRED.
# ----------------------------------------------------------------------


# Stack type constants from Level.py (kept here to avoid the import; the
# composer is pure-data and shouldn't pull in the game module).
_STACK_NONE = 0
_STACK_DURATION = 1
_STACK_INTENSITY = 2
_STACK_REPLACE = 3
_STACK_TYPE_TRANSFORM = 4


def _find_wizard_id(chain):
    """Find the wizard's instance id from any record in the chain that
    references the player-controlled unit. Returns None if no wizard
    reference exists (shouldn't happen for a player keypress chain)."""
    for r in chain:
        payload = r.get('payload') or {}
        # cast_begin / EventOnSpellCast: caster snapshot
        caster = payload.get('caster')
        if caster and caster.get('is_player_controlled'):
            return caster.get('id')
        # damage / heal / buff events: target snapshot
        target = payload.get('target')
        if target and target.get('is_player_controlled'):
            return target.get('id')
        # EventOnUnitAdded / Pass: unit snapshot
        unit = payload.get('unit')
        if unit and unit.get('is_player_controlled'):
            return unit.get('id')
        # EventOnItemUsed: user snapshot
        user = payload.get('user')
        if user and user.get('is_player_controlled'):
            return user.get('id')
    return None


# ----------------------------------------------------------------------
# Spawned section composition
#
# Per design (2026-05-03): renders units added in chain that took no
# damage and didn't die. Units that died are in Killed; units that took
# damage and survived are in Surviving. Each spawn appears in exactly
# one section.
#
# Includes both ally and hostile spawns (player summons + chain-proc
# spawns + death-effect spawns). Ally spawns get an "Ally " prefix;
# hostile spawns are unmarked (the dominant case).
#
# Equivalence-class signature: (name, team). Two units with same name
# but different team membership (e.g., friendly Slimy Vampire spawning
# Blood Slimes that elsewhere would be hostile) DO NOT merge.
#
# Soul Jars are skipped — handled by a dedicated handler in
# screen_reader.py with its own UX path.
# ----------------------------------------------------------------------


def _find_wizard_team(chain):
    """Return the wizard's team value as captured in any chain record's
    snapshot, or None if no wizard reference exists. Used by the
    Spawned section to determine ally classification.

    Looks at caster / target / unit / user snapshot fields — same set
    _find_wizard_id walks. Stops at first match. The isinstance guard skips
    string payload fields (e.g. EventOnBuffAttemptApply's generic
    {'unit': 'Treant'} capture) that would otherwise blow up `.get(...)`."""
    for r in chain:
        payload = r.get('payload') or {}
        for key in ('caster', 'target', 'unit', 'user'):
            snap = payload.get(key)
            if isinstance(snap, dict) and snap.get('is_player_controlled'):
                team = snap.get('team')
                if team is not None:
                    return team
    return None


def _format_spawn_line(target, count, members, wizard_team):
    """Render a single Spawned line. Ally prefix when target's team
    matches the wizard's; hostile (default) unmarked. Multi-target uses
    the same _format_coord_list compression as Killed/Surviving."""
    name = target.get('name') or 'Unknown'
    is_ally = (
        wizard_team is not None and target.get('team') == wizard_team
    )
    prefix = "Ally " if is_ally else ""

    if count <= 1:
        x, y = target.get('x'), target.get('y')
        return f"{prefix}{name} ({x},{y})"

    from helpers import _pluralize
    plural = _pluralize(name)
    return f"{count} {prefix}{plural}{_format_coord_list(members)}"


def compose_spawned_section(chain):
    """Compose the Spawned section: count-led header plus equivalence-
    class lines for units added in chain that didn't die and didn't take
    damage. Returns 'N spawned: ...' or empty string if nothing qualifies.
    """
    # Build death and damaged-target ID sets to exclude.
    deaths = {
        (r.get('payload') or {}).get('target', {}).get('id')
        for r in chain if r.get('event_type') == 'EventOnDeath'
    }
    deaths.discard(None)

    damaged = set()
    for r in chain:
        et = r.get('event_type')
        if et in ('EventOnPreDamaged', 'EventOnDamaged', 'EventOnShieldRemoved'):
            tid = (r.get('payload') or {}).get('target', {}).get('id')
            if tid is not None:
                damaged.add(tid)

    # Wizard team for ally classification.
    wizard_team = _find_wizard_team(chain)

    # Collect untouched spawns in chronological order, dedup by id.
    spawns = []
    seen_ids = set()
    for r in chain:
        if r.get('event_type') != 'EventOnUnitAdded':
            continue
        unit = (r.get('payload') or {}).get('unit') or {}
        uid = unit.get('id')
        if uid is None or uid in seen_ids:
            continue
        if uid in deaths or uid in damaged:
            continue
        # Soul Jars are handled by a dedicated screen_reader.py path; the
        # digest stays out.
        if 'Soul Jar' in (unit.get('name') or ''):
            continue
        seen_ids.add(uid)
        spawns.append(unit)

    if not spawns:
        return ""

    # Equivalence classes. Tier 1 individuated; tier 2 grouped by
    # (name, team). Mixed-team same-name spawns split into separate
    # classes — the friendly-vampire-spawns-friendly-slimes case.
    classes = []
    tier2_lookup = {}
    for unit in spawns:
        tier = unit.get('tier', 'minion')
        if tier in ('boss', 'spawner', 'wizard'):
            classes.append({'target': unit, 'count': 1, 'members': [unit]})
        else:
            key = (unit.get('name'), unit.get('team'))
            existing = tier2_lookup.get(key)
            if existing is None:
                cls = {'target': unit, 'count': 1, 'members': [unit]}
                classes.append(cls)
                tier2_lookup[key] = cls
            else:
                existing['count'] += 1
                existing['members'].append(unit)

    total = sum(cls['count'] for cls in classes)
    lines = []
    for cls in classes:
        line = _format_spawn_line(
            cls['target'], cls['count'], cls['members'], wizard_team
        )
        lines.append(line + ".")
    return f"{total} spawned: " + " ".join(lines)


# ----------------------------------------------------------------------
# Moved section — the wizard's own in-chain self-displacement.
#
# Lightning Form (teleport on a Lightning cast), Blink, Teleport, and the
# Teleporter item all relocate the wizard via EventOnMoved(wizard,
# teleport=True) INSIDE the player's keypress chain. Crisis deliberately
# abstains on in-chain moves — its B3 guard hands them to the digest (see
# crisis.py:_positive_out_of_chain) — so this section is where ownership
# actually lives. Without it the relocation is spoken by no producer: the
# Lightning Form / Blink "silent teleport" bug.
#
# Scope is wizard-only and teleport-only by design:
# - teleport=False is a normal adjacent step (noise), never rendered.
# - a moved NON-wizard in the chain (e.g. enemies pulled by the wizard's
#   own Ice Vortex) is out of scope here — that's a separate enhancement.
# Multi-step self-pulls fire one EventOnMoved per step; the LAST record
# carries the final destination, so we collapse to it.
# ----------------------------------------------------------------------


def compose_moved_section(chain):
    """Compose 'Teleported to (x,y).' for an in-chain wizard self-teleport,
    or '' if the chain holds no wizard teleport. Collapses a multi-step
    self-pull to its final destination."""
    dest = None
    for r in chain:
        if r.get('event_type') != 'EventOnMoved':
            continue
        payload = r.get('payload') or {}
        if not payload.get('teleport'):
            continue
        unit = payload.get('unit') or {}
        if not unit.get('is_player_controlled'):
            continue
        dest = (unit.get('x'), unit.get('y'))
    if dest is None:
        return ""
    return f"Teleported to ({dest[0]},{dest[1]})."


# ----------------------------------------------------------------------
# Debuffs / Buffs applied sections — non-wizard buff applies in chain,
# split by buff_type so debuffs and buffs render in their own sections.
#
# Surfaces buff applications on enemies and allies that the chain's
# events caused. Includes:
# - Direct applies from the cast (Combust Poison applying Poisoned).
# - Reactive trigger applies (an equipment trigger that applies Berserk
#   when an enemy gets Poisoned). These have a non-None parent further
#   up the chain so they're descendants of the keypress root.
#
# Filters out:
# - Wizard-target buffs (those go to Side / are foregrounded by crisis).
# - Silent unit-creation activations (is_silent_activate=True from
#   patched_add_obj — pre-existing buffs on summoned units, not chain
#   effects).
# - Refreshes (is_refresh=True from patched_unit_apply_buff for
#   STACK_NONE / STACK_DURATION re-applies).
#
# Buff_type routing:
# - BUFF_TYPE_CURSE (2) → Debuffs section.
# - BUFF_TYPE_BLESS (1) → Buffs section.
# - BUFF_TYPE_PASSIVE (0), BUFF_TYPE_ITEM (3), None → skipped (rare in
#   chain context; passives are typically unit-creation activations).
#
# Equivalence-class signature: (target_name, target_tier, buff_name,
# turns_left). Matches the digest's existing collapse pattern. Tier-1
# targets (boss / spawner) individuated; tier-2 grouped.
#
# Render template (same for both sections, only header label differs):
# - Single (no header): "Goblin (3,4) poisoned, 5 turns."
# - Multi:  "3 debuffs applied: 3 Goblins at (3,4),(4,4),(5,4) poisoned, 5 turns each."
# - Multi-buffs: "2 buffs applied: Ally Wolf (3,3) strengthened, 5 turns. Ally Goatia (8,8) strengthened, 5 turns."
# - No-duration buff: drop ", 5 turns" clause.
#
# Buff name lowercased to read as adjective ("poisoned", "berserk",
# "petrified") — same convention as the crisis Wizard-prefix line for
# debuffs on wizard.
# ----------------------------------------------------------------------


# Buff-type constants matching Level.py. Listed here to avoid an import
# (the composer is pure-data; no game-module dependency).
_BUFF_TYPE_PASSIVE = 0
_BUFF_TYPE_BLESS = 1
_BUFF_TYPE_CURSE = 2
_BUFF_TYPE_ITEM = 3


def _classify_buff_applies(chain, want_buff_type, refresh_only=False):
    """Walk chain for non-wizard EventOnBuffApply records matching the
    given buff_type. Returns the same equivalence-class structure used
    by both Debuffs and Buffs renderers.

    `refresh_only` selects which half of the player-chain applies to
    collect: False (default) = fresh applies; True = refreshes (duration
    extensions, is_refresh=True from the journal, only synthesized when
    the remaining duration actually changed). The two are rendered as
    separate groups so "newly poisoned" and "poison extended" stay
    distinct (compress-don't-curate) while each still collapses.

    Filters out applies on targets that died in this same chain — once
    the target is dead, the status is irrelevant. The player only cares
    about debuffs whose effects persist beyond the turn (i.e. on
    surviving units). Same filter applies to buffs on dead allies for
    consistency: if your buffed Wolf died the same chain, the buff
    line is just noise."""
    # Build the set of target IDs that died in this chain. EventOnDeath
    # records' target snapshot carries the id we match on.
    dead_target_ids = set()
    for r in chain:
        if r.get('event_type') != 'EventOnDeath':
            continue
        target = (r.get('payload') or {}).get('target') or {}
        tid = target.get('id')
        if tid is not None:
            dead_target_ids.add(tid)

    classes = []
    tier2_lookup = {}

    for r in chain:
        if r.get('event_type') != 'EventOnBuffApply':
            continue
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        if target.get('is_player_controlled'):
            continue
        if payload.get('is_silent_activate'):
            continue
        # Route fresh applies vs refreshes to separate groups.
        if bool(payload.get('is_refresh')) != refresh_only:
            continue
        if target.get('id') in dead_target_ids:
            # Target dies in this chain — debuff/buff is irrelevant.
            continue
        buff = payload.get('buff') or {}
        buff_name = buff.get('name')
        if not buff_name:
            continue
        if buff.get('buff_type') != want_buff_type:
            continue
        turns_left = buff.get('turns_left')
        tier = target.get('tier', 'minion')
        sig = (buff_name, turns_left)

        if tier in ('boss', 'spawner', 'wizard'):
            classes.append({
                'target': target,
                'buff_name': buff_name,
                'turns_left': turns_left,
                'count': 1,
                'members': [target],
            })
        else:
            key = (target.get('name'), sig)
            existing = tier2_lookup.get(key)
            if existing is None:
                cls = {
                    'target': target,
                    'buff_name': buff_name,
                    'turns_left': turns_left,
                    'count': 1,
                    'members': [target],
                }
                classes.append(cls)
                tier2_lookup[key] = cls
            else:
                existing['count'] += 1
                existing['members'].append(target)

    return classes


def _render_debuff_class_line(cls):
    """Debuff line: target-leading, lowercase buff name as adjective.
    Works for the typical debuff vocabulary (Poisoned, Petrified,
    Stunned, Frozen, Burning, Berserk) where the buff name is already
    an adjective or reads naturally as one when lowercased."""
    target_phrase = _format_target_phrase(
        cls['target'], cls['count'], cls['members']
    )
    adj = cls['buff_name'].lower()
    turns = cls['turns_left']
    each_suffix = " each" if cls['count'] > 1 else ""
    if turns and turns > 0:
        return f"{target_phrase} {adj}, {turns} turns{each_suffix}."
    return f"{target_phrase} {adj}."


def _render_buff_class_line(cls):
    """Buff line: target-leading, 'gained {BuffName}' verb-led form. Buff
    names in RW2 are typically nouns (Strength, Haste, Cascade) which
    don't work as adjectives — the verb-led form keeps the line readable
    while preserving the buff's name in original case."""
    target_phrase = _format_target_phrase(
        cls['target'], cls['count'], cls['members']
    )
    name = cls['buff_name']
    turns = cls['turns_left']
    each_suffix = " each" if cls['count'] > 1 else ""
    if turns and turns > 0:
        return f"{target_phrase} gained {name}, {turns} turns{each_suffix}."
    return f"{target_phrase} gained {name}."


def _render_debuff_extended_line(cls):
    """Extended-debuff line: like the apply line but signalling a duration
    extension ('Goblin (3,4) poisoned, extended to 8 turns.')."""
    target_phrase = _format_target_phrase(
        cls['target'], cls['count'], cls['members']
    )
    adj = cls['buff_name'].lower()
    turns = cls['turns_left']
    each_suffix = " each" if cls['count'] > 1 else ""
    if turns and turns > 0:
        return f"{target_phrase} {adj}, extended to {turns} turns{each_suffix}."
    return f"{target_phrase} {adj}, extended."


def _render_buff_extended_line(cls):
    """Extended-buff line, verb-led ('Ally Wolf (3,3) Strength extended to
    8 turns.')."""
    target_phrase = _format_target_phrase(
        cls['target'], cls['count'], cls['members']
    )
    name = cls['buff_name']
    turns = cls['turns_left']
    each_suffix = " each" if cls['count'] > 1 else ""
    if turns and turns > 0:
        return f"{target_phrase} {name} extended to {turns} turns{each_suffix}."
    return f"{target_phrase} {name} extended."


def _render_buff_apply_section(classes, plural_label, line_renderer):
    """Render an equivalence-class list. Single application: bare line.
    Multi: 'N {plural_label} applied: line1. line2. ...'."""
    if not classes:
        return ""
    total = sum(cls['count'] for cls in classes)
    lines = [line_renderer(cls) for cls in classes]
    if total == 1:
        return lines[0]
    return f"{total} {plural_label} applied: " + " ".join(lines)


def _render_extended_section(classes, line_renderer):
    """Render the extended (refresh) group. Each line self-describes the
    extension and its target count, so no separate count header is used
    (that would double the word 'extended')."""
    if not classes:
        return ""
    return " ".join(line_renderer(cls) for cls in classes)


def _compose_two_group_section(chain, want_buff_type, plural_label,
                               apply_renderer, extended_renderer):
    """Render fresh applies and refresh-extensions as two collapsed groups,
    joined into one section string."""
    applied = _classify_buff_applies(chain, want_buff_type)
    extended = _classify_buff_applies(chain, want_buff_type, refresh_only=True)
    fresh_part = _render_buff_apply_section(applied, plural_label, apply_renderer)
    extended_part = _render_extended_section(extended, extended_renderer)
    return " ".join(p for p in (fresh_part, extended_part) if p)


def compose_debuffs_applied_section(chain):
    """Compose the Debuffs-applied section: non-wizard targets, buff_type=2.
    Adjective-form rendering ('Goblin (3,4) poisoned, 5 turns.'), with a
    separate extended group for refreshes ('... poisoned, extended to 8
    turns.')."""
    return _compose_two_group_section(
        chain, _BUFF_TYPE_CURSE, "debuffs",
        _render_debuff_class_line, _render_debuff_extended_line)


def compose_buffs_applied_section(chain):
    """Compose the Buffs-applied section: non-wizard targets, buff_type=1.
    Verb-led rendering ('Ally Wolf (3,3) gained Strength, 5 turns.'), with a
    separate extended group for refreshes ('... Strength extended to 8
    turns.')."""
    return _compose_two_group_section(
        chain, _BUFF_TYPE_BLESS, "buffs",
        _render_buff_class_line, _render_buff_extended_line)


# Back-compat alias: the previous combined section is kept for any
# external callers that may have imported it (tests reference the
# split functions; nothing else uses this name today). Returns the
# debuff section's content since that's the dominant case in player
# chains.
def compose_statuses_applied_section(chain):
    """Deprecated. Use compose_debuffs_applied_section /
    compose_buffs_applied_section. Retained as a thin wrapper that
    returns debuffs for any code that still imports the old name."""
    return compose_debuffs_applied_section(chain)


# ----------------------------------------------------------------------
# Shields granted / stripped sections — non-wizard shield changes in a
# player chain (Shield-Allies gains, Siphon-Shields strips). Wizard
# self-gains go to the Side section; outgoing BLOCKS stay on the Surviving
# section's "absorbed by N shields" (a block is not a strip). Shields are
# not buffs (no buff_type/duration), so they get their own classifier, but
# reuse _format_target_phrase for the same type-subdivided, ally-prefixed,
# Tier-1-individuated / Tier-2-collapsed grouping as the buff/debuff
# sections.
# ----------------------------------------------------------------------


def _classify_shield_changes(chain, event_type, exclude_superseded=False):
    """Equivalence classes of non-wizard shield changes (event_type =
    'shield_gained' or 'shield_stripped'), grouped exactly like the buff
    sections: Tier 1 (boss/spawner) individuated, Tier 2 (minion) grouped by
    (name, amount). Filters targets that died in the chain. For strips,
    exclude_superseded drops the block-coincident strips marked at capture, so
    only genuine direct strips (Siphon) appear — block absorptions read as
    'absorbed by N shields' on the Surviving section instead."""
    dead_target_ids = set()
    for r in chain:
        if r.get('event_type') == 'EventOnDeath':
            t = (r.get('payload') or {}).get('target') or {}
            if t.get('id') is not None:
                dead_target_ids.add(t.get('id'))

    classes = []
    tier2_lookup = {}
    for r in chain:
        if r.get('event_type') != event_type:
            continue
        if exclude_superseded and 'superseded_by_block' in (r.get('marks') or []):
            continue
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        if target.get('is_player_controlled'):
            continue
        if target.get('id') in dead_target_ids:
            continue
        amount = (payload.get('amount') if event_type == 'shield_gained'
                  else payload.get('amount_removed'))
        tier = target.get('tier', 'minion')
        if tier in ('boss', 'spawner', 'wizard'):
            classes.append({'target': target, 'amount': amount,
                            'count': 1, 'members': [target]})
        else:
            # team in the key so a charmed enemy and an ally summon of the
            # same name don't collapse under one prefix (_shield_target_phrase
            # takes the prefix from the class's first member — ally-designation
            # is mandatory).
            key = (target.get('name'), amount, target.get('team'))
            existing = tier2_lookup.get(key)
            if existing is None:
                cls = {'target': target, 'amount': amount,
                       'count': 1, 'members': [target]}
                classes.append(cls)
                tier2_lookup[key] = cls
            else:
                existing['count'] += 1
                existing['members'].append(target)
    return classes


def _shield_target_phrase(target, count, members, wizard_team):
    """Target phrase with the Spawned-section ally convention: an 'Ally '
    prefix on the name when the target shares the wizard's team (so shields
    read consistently with spawns — '3 Ally Wolves')."""
    name = target.get('name') or 'Unknown'
    is_ally = wizard_team is not None and target.get('team') == wizard_team
    prefix = "Ally " if is_ally else ""
    if count <= 1:
        x, y = target.get('x'), target.get('y')
        return f"{prefix}{name} ({x},{y})"
    from helpers import _pluralize
    plural = _pluralize(name)
    return f"{count} {prefix}{plural}{_format_coord_list(members)}"


def _render_shield_gained_line(cls, wizard_team):
    phrase = _shield_target_phrase(cls['target'], cls['count'], cls['members'],
                                   wizard_team)
    amount = cls['amount'] or 0
    sh = "shield" if amount == 1 else "shields"
    each = " each" if cls['count'] > 1 else ""
    return f"{phrase} gained {amount} {sh}{each}."


def _render_shield_stripped_line(cls, wizard_team):
    # Binary outcome — the key info is which units lost shields. (Header
    # carries the "stripped" verb so the line just names the units.)
    phrase = _shield_target_phrase(cls['target'], cls['count'], cls['members'],
                                   wizard_team)
    return f"{phrase}."


def compose_shields_granted_section(chain):
    """Non-wizard shield gains in a player chain (e.g. Shield Allies).
    Type-subdivided, ally-prefixed collapse via the shared target phrasing."""
    classes = _classify_shield_changes(chain, 'shield_gained')
    if not classes:
        return ""
    wizard_team = _find_wizard_team(chain)
    lines = [_render_shield_gained_line(c, wizard_team) for c in classes]
    return "Shields granted: " + " ".join(lines)


def compose_shields_stripped_section(chain):
    """Non-wizard shield strips in a player chain (e.g. Siphon Shields).
    Skips block-coincident strips (those read as 'absorbed by N shields')."""
    classes = _classify_shield_changes(chain, 'shield_stripped',
                                       exclude_superseded=True)
    if not classes:
        return ""
    wizard_team = _find_wizard_team(chain)
    lines = [_render_shield_stripped_line(c, wizard_team) for c in classes]
    return "Shields stripped: " + " ".join(lines)


def _classify_team_changes(chain, event_type):
    """Equivalence classes of team flips (event_type = 'team_joined' or
    'team_turned'), grouped like the shield sections: Tier 1 (boss/spawner)
    individuated, Tier 2 (minion) collapsed by name. Filters targets that died in
    the chain. No amount — a flip is categorical; the direction IS the event_type,
    so within one call every target shares the same resulting team and name alone
    is a safe collapse key."""
    dead_target_ids = set()
    for r in chain:
        if r.get('event_type') == 'EventOnDeath':
            t = (r.get('payload') or {}).get('target') or {}
            if t.get('id') is not None:
                dead_target_ids.add(t.get('id'))
    classes = []
    tier2_lookup = {}
    for r in chain:
        if r.get('event_type') != event_type:
            continue
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        if target.get('is_player_controlled'):   # the wizard never flips (defensive)
            continue
        if target.get('id') in dead_target_ids:
            continue
        tier = target.get('tier', 'minion')
        if tier in ('boss', 'spawner'):
            classes.append({'target': target, 'count': 1, 'members': [target]})
        else:
            key = (target.get('name'),)
            existing = tier2_lookup.get(key)
            if existing is None:
                cls = {'target': target, 'count': 1, 'members': [target]}
                classes.append(cls)
                tier2_lookup[key] = cls
            else:
                existing['count'] += 1
                existing['members'].append(target)
    return classes


def _team_target_phrase(target, count, members):
    """Target phrase for a flip line. No ally/enemy prefix — the disposition
    ('turned friendly/hostile') already carries the allegiance, so a prefix would
    be redundant ('Ally Ogre turned friendly')."""
    name = target.get('name') or 'Unknown'
    if count <= 1:
        x, y = target.get('x'), target.get('y')
        return f"{name} ({x},{y})"
    from helpers import _pluralize
    plural = _pluralize(name)
    return f"{count} {plural}{_format_coord_list(members)}"


def _render_team_line(cls, disposition):
    phrase = _team_target_phrase(cls['target'], cls['count'], cls['members'])
    return f"{phrase} turned {disposition}."


def compose_team_changes_section(chain):
    """Team flips in a player chain — Dominate/conversions (enemy→player, "turned
    friendly") and the rare player-losing forfeit (player→enemy, "turned
    hostile"). Bare verb-in-line form (no section header, unlike shields — the
    disposition carries the meaning): 'Ogre (3,4) turned friendly.'"""
    joined = _classify_team_changes(chain, 'team_joined')
    turned = _classify_team_changes(chain, 'team_turned')
    if not joined and not turned:
        return ""
    lines = [_render_team_line(c, 'friendly') for c in joined]
    lines += [_render_team_line(c, 'hostile') for c in turned]
    return " ".join(lines)


def compose_side_section(chain):
    """Compose the Side section: heals, buffs, wizard self-shield gains
    (charges deferred).

    Returns 'Side. Heals: ... Buffs: ... Shields: ...' or empty string if no
    sub-section has content.
    """
    wizard_id = _find_wizard_id(chain)
    if wizard_id is None:
        return ""

    # Aggregate heals by source (wizard target only).
    heals_by_source = {}
    heal_source_order = []
    for r in chain:
        if r.get('event_type') != 'EventOnHealed':
            continue
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        if target.get('id') != wizard_id:
            continue
        source = payload.get('source_name') or 'Unknown'
        amount = payload.get('heal_amount') or 0
        if source not in heals_by_source:
            heal_source_order.append(source)
            heals_by_source[source] = 0
        heals_by_source[source] += amount

    # Aggregate buff applies on wizard. Track count, latest stack count,
    # latest turns_left, and stack_type for phrasing dispatch.
    buff_applies = {}
    buff_order = []
    for r in chain:
        if r.get('event_type') != 'EventOnBuffApply':
            continue
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        if target.get('id') != wizard_id:
            continue
        buff = payload.get('buff') or {}
        name = buff.get('name') or 'Unknown'
        if name not in buff_applies:
            buff_order.append(name)
            buff_applies[name] = {'count': 0}
        info = buff_applies[name]
        info['count'] += 1
        info['stack_count'] = payload.get('stack_count_after')
        info['turns_left'] = buff.get('turns_left')
        info['stack_type'] = buff.get('stack_type')
        if payload.get('is_refresh'):
            info['saw_refresh'] = True
        else:
            info['saw_fresh'] = True

    # Aggregate wizard-target shield gains (in-chain self-shield, e.g. a
    # self-cast Ironskin). Out-of-chain wizard gains are crisis's.
    shield_gained_total = 0
    shield_after_latest = None
    for r in chain:
        if r.get('event_type') != 'shield_gained':
            continue
        payload = r.get('payload') or {}
        target = payload.get('target') or {}
        if target.get('id') != wizard_id:
            continue
        shield_gained_total += payload.get('amount') or 0
        shield_after_latest = payload.get('shields_after')

    parts = []

    if heals_by_source:
        heal_clauses = [
            f"{src} {heals_by_source[src]} HP" for src in heal_source_order
        ]
        parts.append("Heals: " + ", ".join(heal_clauses) + ".")

    if buff_applies:
        buff_sentences = []
        for name in buff_order:
            info = buff_applies[name]
            buff_sentences.append(_format_buff_apply(name, info))
        parts.append("Buffs: " + " ".join(s + "." for s in buff_sentences))

    if shield_gained_total:
        sh = "shield" if shield_gained_total == 1 else "shields"
        if shield_after_latest is not None:
            parts.append(f"Shields: gained {shield_gained_total} {sh}, "
                         f"{shield_after_latest} total.")
        else:
            parts.append(f"Shields: gained {shield_gained_total} {sh}.")

    if not parts:
        return ""
    return "Side. " + " ".join(parts)


def _format_buff_apply(name, info):
    """Render a single buff-apply statement per stack_type rules.

    Stacking (STACK_INTENSITY): "Buff applied N times, now M stacks"
    when N > 1, or "Buff applied, now M stacks" for a single apply.

    Non-stacking (STACK_NONE, STACK_DURATION, STACK_REPLACE):
    "Buff applied, T turns" if turns_left > 0, else "Buff applied". A
    refresh with no fresh apply this chain (the wizard re-cast a buff they
    already had) renders "Buff extended to T turns" instead.

    STACK_TYPE_TRANSFORM is deferred — not handled here.
    """
    count = info.get('count', 1)
    stack_count = info.get('stack_count')
    turns_left = info.get('turns_left')
    stack_type = info.get('stack_type')

    if stack_type == _STACK_INTENSITY:
        if stack_count is None:
            stack_count = count
        stack_word = "stack" if stack_count == 1 else "stacks"
        if count > 1:
            return f"{name} applied {count} times, now {stack_count} {stack_word}"
        return f"{name} applied, now {stack_count} {stack_word}"

    # Non-stacking: report duration when present. A pure refresh (re-cast
    # of an already-active buff) reads as an extension.
    if turns_left and turns_left > 0:
        if info.get('saw_refresh') and not info.get('saw_fresh'):
            return f"{name} extended to {turns_left} turns"
        return f"{name} applied, {turns_left} turns"
    return f"{name} applied"


# ----------------------------------------------------------------------
# Top-level orchestrator
#
# compose_digest(chain) picks between the standard four-section form
# and the streamlined trivial-case bypass, then assembles the final
# output string for emission.
#
# Streamlined trigger (per design_digest_phrasing.md): exactly one cast
# in the chain (no procs) AND ≤ 1 EventOnDamaged total. Side effects
# append inline (no "Side." label, no "Heals:" / "Buffs:" sub-labels).
# ----------------------------------------------------------------------


def _is_empty_autofire(chain):
    """True iff the chain root is an autofire (pay_costs=False or
    is_echo) AND the chain has no effects beyond cast_begin records.

    Autofires are equipment / buff per-turn passives that fire spells
    on the wizard's behalf without player input — RepeaterCast, Explosive
    Spore Manual amulet, similar items. They DO go through act_cast (so
    they form chain roots) but most ticks miss every target and produce
    nothing. Per user direction (2026-05-08), silence these specifically
    when empty so effective autofires still render. See feedback memory:
    feedback_capture_separate_from_render.md.
    """
    root = next(
        (r for r in chain
         if r.get('event_type') == 'cast_begin' and r.get('parent') is None),
        None,
    )
    if root is None:
        return False
    payload = root.get('payload') or {}
    is_autofire = (
        payload.get('pay_costs') is False
        or bool(payload.get('is_echo'))
    )
    if not is_autofire:
        return False
    non_cast_count = sum(
        1 for r in chain if r.get('event_type') != 'cast_begin'
    )
    return non_cast_count == 0


def compose_digest(chain):
    """Compose the full digest output string for one keypress chain.

    Returns the assembled output ('Cast X. ... Side. ...' or the
    streamlined 'Cast X, killed Y, Z Dtype.' form) ready for emission
    via tts.speak. Empty string if the chain has no content worth
    rendering (no cast and no effects), or if the chain root is an
    empty autofire (see _is_empty_autofire).

    Streamlined form is bypassed when the chain has spawn content —
    the Spawned section needs the standard form's structure, and a
    spawn-only chain (Cast Summon Wolf with no enemies) reads more
    cleanly as 'Cast Summon Wolf. 1 spawned: Ally Wolf (3,3).' than
    as a verbed streamlined line."""
    if not chain:
        return ""

    if _is_empty_autofire(chain):
        return ""

    cast_count = sum(1 for r in chain if r.get('event_type') == 'cast_begin')
    damage_count = sum(1 for r in chain if r.get('event_type') == 'EventOnDamaged')
    has_spawns = bool(compose_spawned_section(chain))
    has_debuffs = bool(compose_debuffs_applied_section(chain))
    has_buffs = bool(compose_buffs_applied_section(chain))
    has_move = bool(compose_moved_section(chain))
    has_shield_grants = bool(compose_shields_granted_section(chain))
    has_shield_strips = bool(compose_shields_stripped_section(chain))
    has_team_changes = bool(compose_team_changes_section(chain))

    if (cast_count == 1 and damage_count <= 1
            and not has_spawns and not has_debuffs and not has_buffs
            and not has_move and not has_shield_grants and not has_shield_strips
            and not has_team_changes):
        return _compose_streamlined(chain)
    return _compose_standard(chain)


def _compose_standard(chain):
    """Standard digest with seven sections. Sections separated by space;
    each self-contained ending in period. Empty
    Killed/Surviving/Debuffs/Buffs/Spawned sections omit; if all five
    are empty (no damage landed AND nothing spawned AND no statuses
    applied) emit 'No damage.' between Cast and Side."""
    cast = compose_cast_section(chain)
    killed = compose_killed_section(chain)
    surviving = compose_surviving_section(chain)
    debuffs = compose_debuffs_applied_section(chain)
    shields_stripped = compose_shields_stripped_section(chain)
    buffs = compose_buffs_applied_section(chain)
    shields_granted = compose_shields_granted_section(chain)
    team_changes = compose_team_changes_section(chain)
    spawned = compose_spawned_section(chain)
    moved = compose_moved_section(chain)
    side = compose_side_section(chain)

    parts = []
    if cast:
        parts.append(cast)

    # Empty-chain handling: only emit "No damage." when all
    # outcome-bearing sections are empty (shield grants/strips count as
    # outcomes — a Siphon that lands no damage still did something).
    if (cast and not killed and not surviving and not debuffs
            and not buffs and not spawned and not shields_stripped
            and not shields_granted and not team_changes):
        parts.append("No damage.")
    else:
        if killed:
            parts.append(killed)
        if surviving:
            parts.append(surviving)
        if debuffs:
            parts.append(debuffs)
        if shields_stripped:
            parts.append(shields_stripped)
        if buffs:
            parts.append(buffs)
        if shields_granted:
            parts.append(shields_granted)
        if spawned:
            parts.append(spawned)
        if team_changes:
            parts.append(team_changes)

    # The wizard's own relocation trails the outcome sections: cast →
    # effects → "and you ended up here". Kept independent of the No-damage
    # guard above so a teleport that landed no hits still reports both
    # ("Cast Lightning Bolt. No damage. Teleported to (13,4).").
    if moved:
        parts.append(moved)

    if side:
        parts.append(side)

    return " ".join(parts)


def _compose_streamlined(chain):
    """Trivial-case streamlined form: 'Cast Spell, [verb] Target (x,y),
    N Dtype.' with side effects appended inline. Verb is 'killed' for
    kills, 'hit' for survivors. 'Cast Spell. No damage.' for empty hits."""
    cast_rec = next(
        (r for r in chain if r.get('event_type') == 'cast_begin'), None
    )
    if cast_rec is None:
        return ""

    payload = cast_rec.get('payload') or {}
    spell_obj = payload.get('spell') or {}
    spell_name = spell_obj.get('name') or 'Unknown'
    cast_verb = "Channeled" if payload.get('is_channel_continuation') else "Cast"

    hits_by_target = _build_target_hits(chain)
    deaths = {
        (r.get('payload') or {}).get('target', {}).get('id')
        for r in chain if r.get('event_type') == 'EventOnDeath'
    }
    deaths.discard(None)

    side_inline = _format_streamlined_side(chain)

    # Find the (single) damaged target if any. Streamlined form has
    # ≤ 1 EventOnDamaged total, so at most one target took damage.
    target_id = None
    target_snap = None
    for r in chain:
        if r.get('event_type') != 'EventOnDamaged':
            continue
        p = r.get('payload') or {}
        t = p.get('target') or {}
        target_id = t.get('id')
        target_snap = t
        break

    if target_id is None or not hits_by_target.get(target_id):
        # No damage landed in this chain — empty form.
        line = f"{cast_verb} {spell_name}. No damage."
        if side_inline:
            return f"{line} {side_inline}"
        return line

    hit = hits_by_target[target_id][0]
    target_name = target_snap.get('name') or 'Unknown'
    x, y = target_snap.get('x'), target_snap.get('y')
    is_kill = target_id in deaths
    verb = "killed" if is_kill else "hit"
    damage = _displayed_damage(hit)
    dtype = hit.get('dtype')
    dtype_str = f" {dtype}" if dtype else ""
    line = f"{cast_verb} {spell_name}, {verb} {target_name} ({x},{y}), {damage}{dtype_str}."

    if side_inline:
        return f"{line} {side_inline}"
    return line


def _format_streamlined_side(chain):
    """Render side effects as inline period-separated sentences without
    'Side.' label or 'Heals:' / 'Buffs:' sub-labels.

    Heals use the verb-led short form 'Healed N HP from {source}', split
    per source so each heal is attributed the way the game's combat log
    attributes it (a summed source-blind total would drop that signal).
    Buffs keep their standard phrasing.
    """
    wizard_id = _find_wizard_id(chain)
    if wizard_id is None:
        return ""

    sentences = []

    # Heals — per source (the game attributes each heal to its source).
    heals_by_source = {}
    heal_order = []
    for r in chain:
        if r.get('event_type') != 'EventOnHealed':
            continue
        p = r.get('payload') or {}
        if (p.get('target') or {}).get('id') != wizard_id:
            continue
        amount = p.get('heal_amount') or 0
        if amount <= 0:
            continue
        source = p.get('source_name') or 'Unknown'
        if source not in heals_by_source:
            heal_order.append(source)
            heals_by_source[source] = 0
        heals_by_source[source] += amount
    for source in heal_order:
        amt = heals_by_source[source]
        if source and source != 'Unknown':
            sentences.append(f"Healed {amt} HP from {source}")
        else:
            sentences.append(f"Healed {amt} HP")

    # Buffs — same phrasing as standard form, no sub-label.
    buff_applies = {}
    buff_order = []
    for r in chain:
        if r.get('event_type') != 'EventOnBuffApply':
            continue
        p = r.get('payload') or {}
        if (p.get('target') or {}).get('id') != wizard_id:
            continue
        buff = p.get('buff') or {}
        name = buff.get('name') or 'Unknown'
        if name not in buff_applies:
            buff_order.append(name)
            buff_applies[name] = {'count': 0}
        info = buff_applies[name]
        info['count'] += 1
        info['stack_count'] = p.get('stack_count_after')
        info['turns_left'] = buff.get('turns_left')
        info['stack_type'] = buff.get('stack_type')

    for name in buff_order:
        sentences.append(_format_buff_apply(name, buff_applies[name]))

    if not sentences:
        return ""
    return " ".join(s + "." for s in sentences)


def gather_chain_events(records, root):
    """Collect every record causally descended from `root` (including
    `root` itself), preserving sequence order.

    Args:
        records: full journal record list to scan.
        root: the pending keypress root record (from find_pending_root).

    Returns:
        List of records whose causation lineage roots in the same record
        as `root`, in the order they appear in `records` (which the
        journal maintains in monotonic sequence order). Empty list if
        `root` is None or has no sequence.

    Why walk-from-each rather than collect-via-parent: walking from each
    record to its root reuses walk_to_keypress_root's correctness
    guarantees (cycle protection, missing-parent handling, intermediate-
    proc traversal). For typical chains (≤100 records, depth ≤5 even
    with deep proc stacks) the redundant walks are cheap. If profiling
    later shows hot-path cost, switch to a one-pass parent-link traversal.

    Sequence comparison rather than identity: if any caller passes a
    `root` that's a copy of a record rather than the live reference (the
    journal currently stores references but a future caller might
    re-fetch via index lookup), comparing by sequence still matches.
    """
    if root is None:
        return []
    root_seq = root.get('sequence')
    if root_seq is None:
        return []
    idx = build_record_index(records)
    chain = []
    for rec in records:
        walked = walk_to_keypress_root(rec, idx)
        if walked is not None and walked.get('sequence') == root_seq:
            chain.append(rec)
    return chain


# Event types the composer has rendering branches for. Anything in a chain
# that doesn't appear here is unmodeled — surfaced via digest_unmodeled
# telemetry so phrasing-spec extensions can be driven by real journal data.
# Update this set when adding new render branches; otherwise unmodeled noise
# will fire for known-handled types.
_COMPOSER_KNOWN_EVENT_TYPES = frozenset({
    'cast_begin',
    'EventOnPreDamaged', 'EventOnDamaged',
    'EventOnDeath',
    'EventOnHealed',
    'EventOnBuffApply', 'EventOnBuffRemove',
    'EventOnShieldRemoved',
    'EventOnUnitAdded',
    'EventOnUnfrozen',
    # Paired-notification event types — always co-occur with a primary
    # event the composer already renders. Listed here to silence the
    # digest_unmodeled telemetry noise without adding render branches.
    # EventOnSpellCast pairs with cast_begin. EventOnUnitPreAdded pairs
    # with EventOnUnitAdded. EventOnItemUsed pairs with the cast_begin
    # of the item-use cast. EventOnSpendHP fires for player sacrifice
    # mechanics (Goatia Offering) — the spend is implicit from the cast.
    'EventOnSpellCast',
    'EventOnUnitPreAdded',
    'EventOnItemUsed',
    'EventOnSpendHP',
    # Synthetic event types from journal capture-gap fixes (2026-05-08).
    # Captured but not rendered by the chain digest — handled (or
    # parked) by the future orphan-window composer.
    'equipment_initialized',
    # Shield-change capture (R3). Rendered by the Side section (wizard gain),
    # the mass shield sections (ally gain / enemy strip), the Surviving
    # section's block clause, and orphan — all handled, so they must not trip
    # the unmodeled-telemetry noise.
    'shield_gained', 'shield_stripped', 'shield_blocked',
    # Team-flip capture (R2). Rendered by compose_team_changes_section (in-chain
    # Dominate/conversions) and orphan (ambient); both handled, so not unmodeled.
    'team_joined', 'team_turned',
    # Silent HP capture (R5), universal via the interceptor. Wizard lethal-saves
    # are voiced by crisis; other silent heals + all max_hp changes are captured
    # ground truth staged for Track B. Known-set so they don't trip unmodeled
    # telemetry when they land in a chain.
    'silent_heal', 'max_hp_change',
    # Unit 4 capture-only kinds (G-G/G-F/G-M), staged for the composer phase:
    # hp_loss = silent cur_hp decreases (a Word-of-Undeath cast lands one per
    # affected unit in its own chain — without this entry every such cast would
    # flood digest_unmodeled); xp_change = SP gains/spends; EventOnAwakened =
    # sleep-end at parity with EventOnUnfrozen (was tripping unmodeled via the
    # generic payload fallback even before Unit 4 — this also closes that).
    'hp_loss', 'xp_change', 'EventOnAwakened',
})


def _maybe_emit_unmodeled(tel, root_seq, chain, output):
    """Emit digest_unmodeled telemetry when a chain didn't render cleanly.

    Two heuristics, non-exclusive — both can fire on the same chain:
    - empty_output_nontrivial_chain: the chain has more than just cast
      records but the composer produced no output.
    - unknown_event_types: at least one record has an event_type the
      composer doesn't know how to handle.

    Either fires a single event with the full chain dump for later
    analysis. The marks field on chain records reflects post-claim state
    — i.e., digest_v1 will be present even though we didn't render the
    record cleanly. Interpret with that in mind."""
    seen_types = {r.get('event_type') for r in chain}
    unknown = seen_types - _COMPOSER_KNOWN_EVENT_TYPES
    non_cast_count = sum(
        1 for r in chain if r.get('event_type') != 'cast_begin'
    )
    empty_with_events = (not output) and non_cast_count > 0

    if not unknown and not empty_with_events:
        return

    reasons = []
    if empty_with_events:
        reasons.append('empty_output_nontrivial_chain')
    if unknown:
        reasons.append('unknown_event_types')

    tel.emit(
        'digest_unmodeled',
        root_seq=root_seq,
        reasons=reasons,
        unknown_types=sorted(t for t in unknown if t is not None),
        chain_len=len(chain),
        chain=chain,
    )


def _claim_chain(chain, mark):
    """Stamp `mark` on the marks list of every record in `chain`.

    Idempotent — re-claiming the same chain doesn't duplicate marks.
    Future composers (sub-step 10's per-handler batcher suppression,
    or downstream summary composers) will respect these marks to avoid
    re-narrating events the digest has already covered. Per phase-1
    decision #4 in design_rw2_data_model.md: marks are advisory and
    additive; no central coordinator.

    claim==render: a wizard-facing record inside the player's keypress
    chain (e.g. retaliation damage taken mid-cast) is claimed by crisis,
    which runs first and renders it in the foregrounded lane. The digest
    does NOT render wizard damage-taken, so it must not re-mark a
    crisis-owned record — that would trip the double-claim watchdog.
    """
    for rec in chain:
        if 'crisis_v1' in (rec.get('marks') or []):
            continue
        marks = rec.setdefault('marks', [])
        if mark not in marks:
            marks.append(mark)


class _DigestComposer:
    """Composer for the direct-action digest.

    Stateful across calls within a session: tracks the last player-keypress
    chain root we've already composed, so multiple invocations within one
    turn boundary don't produce duplicate utterances."""

    def __init__(self):
        # Sequence number of the last player-keypress root we digested.
        # None if no digest has been emitted yet this session.
        self._last_digested_root_seq = None

    def compose_section(self, log_fn, telemetry=None):
        """Compose the digest section for the unified emitter.

        Returns a (priority, text) tuple suitable for the pipeline's
        sort+join. Text is empty string if no chain pending. Multi-root
        case: all chains' outputs are joined with single space into one
        section text — the pipeline emits one TTS call covering all of
        them, preserving chronological narration order without
        introducing inter-utterance pauses.

        Stamps DIGEST_MARK on all chain records claimed. Per-chain
        digest_emit / digest_unmodeled telemetry still fires (one event
        per chain processed)."""
        from journal import journal
        _tel = telemetry

        pending = find_all_pending_roots(
            journal.records, self._last_digested_root_seq
        )
        if not pending:
            return (PRIORITY_STANDARD_DIGEST, "")

        chain_outputs = []

        for root in pending:
            chain = gather_chain_events(journal.records, root)
            digest_output = compose_digest(chain)

            root_seq = root.get('sequence')
            self._last_digested_root_seq = root_seq
            _claim_chain(chain, DIGEST_MARK)

            payload = root.get('payload') or {}
            spell = payload.get('spell') or {}
            spell_name = spell.get('name')
            log_fn(
                f"[Digest] fired: "
                f"seq={root_seq} spell={spell_name} "
                f"events={len(chain)}"
            )
            if _tel is not None:
                try:
                    _tel.emit('digest_emit',
                              root_seq=root_seq,
                              spell=spell_name,
                              events=len(chain),
                              marks_stamped=len(chain),
                              empty=not bool(digest_output),
                              output=digest_output or '')
                except Exception:
                    pass
                try:
                    _maybe_emit_unmodeled(_tel, root_seq, chain, digest_output)
                except Exception:
                    pass

            if digest_output:
                chain_outputs.append(digest_output)

        text = " ".join(chain_outputs).strip()
        if text:
            log_fn(f"[Digest] composed: {text}")
        return (PRIORITY_STANDARD_DIGEST, text)

    def fire_if_pending(self, tts, log_fn, telemetry=None):
        """Backward-compat path: compose the digest section and emit it
        directly via tts.speak. Used by callers that haven't migrated to
        the unified pipeline (pipeline.py is the new path). Safe to call
        when no chain has occurred — returns silently."""
        priority, text = self.compose_section(log_fn, telemetry=telemetry)
        if not text:
            return None
        try:
            tts.speak(text)
        except Exception as e:
            log_fn(f"[Digest] speak failed: {e!r}")
        return None


# Module-level singleton — there is exactly one digest composer per session.
composer = _DigestComposer()
