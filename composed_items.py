"""
Composed-item spine — slice 1 stage A (the review-layer seam).

Every producer's rendered output flows through the pipeline as ITEMS —
dicts carrying the rendered string plus the metadata the later slices
and the review layer are built on (SLICE1_BURST_AGGREGATION_BUILD_PLAN.md
§6; schema minimums ruled binding in REVIEW_LAYER_DESIGN.md):

    rank     — line-item rank (orphan's ordering ranks; None for
               producers with no rank structure yet — slice 3 stamps
               real salience bits into this field family)
    anchors  — unit snapshots the line is "about" (spatial ordering
               metadata; empty where not applicable)
    text     — the rendered sentence(s), exactly what the emitter joins
    row_key  — the registry row that produced the item. Stage A uses
               site-derived provisional keys ('orphan.dot',
               'digest.chain', ...); they become real registry keys at
               stage D with no migration (the field is the seam)
    seqs     — ⭐ the SOURCE RECORD SEQUENCES the item was composed
               from. Aggregation never discards refs (the
               items-are-a-product standing rule): drill-down walks
               seqs -> shared record index -> full causal tree via
               parent links / gather_descendants. May be empty only
               for poll-derived content (crisis HP threshold / agency
               countdowns — no records exist for a poll).

The (level, turn) key lives at the BUFFER entry grain: one entry per
pipeline fire, stamped from journal.level_id and the live level's
turn_no at append time. Items inside an entry share it.

The ring buffer is flag-gated (settings [Composer] review_buffer_enabled,
default false) and memory-only. It deliberately does NOT reset on level
transition — the game's own combat log walks across level boundaries,
and cross-level review depth is an open review-layer question
(REVIEW_LAYER_DESIGN.md open question 3); the cap bounds memory either
way. NB: seqs from a departed level can no longer resolve through the
shared index (the journal resets per level) — cross-level entries keep
their rendered text and row keys; per-record decompression is
current-level only until the review design rules otherwise.
"""


def make_item(rank, anchors, text, row_key=None, seqs=None):
    """The one item constructor — all producers build items through this
    (orphan._make_item delegates here) so the shape stays uniform.
    Filters falsy anchors, copies seqs."""
    return {
        'rank': rank,
        'anchors': [a for a in (anchors or []) if a],
        'text': text,
        'row_key': row_key,
        'seqs': [s for s in (seqs or []) if s is not None],
    }


class ItemBuffer:
    """Ring buffer of per-fire item batches, capped by entry count.

    One entry per pipeline fire: {'level': level_id, 'turn': turn_no,
    'items': [...]}. Multiple fires can share a (level, turn) key
    (deploy-phase boundaries, multi-fire turns) — entries are kept
    per fire; the review UI merges by key later if it wants to."""

    DEFAULT_MAX_ENTRIES = 200

    def __init__(self, max_entries=DEFAULT_MAX_ENTRIES):
        self.max_entries = max_entries
        self._entries = []

    def append(self, level_id, turn, items):
        if not items:
            return
        self._entries.append({
            'level': level_id, 'turn': turn, 'items': list(items),
        })
        overflow = len(self._entries) - self.max_entries
        if overflow > 0:
            del self._entries[:overflow]

    def entries(self):
        """Snapshot copy of the entry list (oldest first)."""
        return list(self._entries)

    def clear(self):
        self._entries = []

    def __len__(self):
        return len(self._entries)


# Module-level singleton — one buffer per session, mirroring the
# producer-singleton convention.
buffer = ItemBuffer()
