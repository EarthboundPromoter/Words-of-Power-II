# Tests for the slice-1 stage-A item spine (composed_items.py + the
# items_sink plumbing in all four producers + the pipeline's flag-gated
# ring buffer). Stage A law: items flow with correct refs, voice
# unchanged (the replay gate pins the voice; these tests pin the items).
# Run from the game root: python -m pytest mods/screen_reader/tests/test_composed_items.py -v

import sys
import os

mod_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if mod_dir not in sys.path:
    sys.path.insert(0, mod_dir)

from composed_items import ItemBuffer, make_item, buffer as global_buffer
import orphan
import digest
import crisis
import equipment


def _log(_msg):
    pass


# ---- Fixtures (record shapes mirror test_orphan.py) ----


def _wizard_snap(team=0):
    return {
        'id': 100, 'name': 'Wizard', 'x': 10, 'y': 10,
        'cur_hp': 50, 'max_hp': 50, 'team': team, 'tier': 'wizard',
        'is_player_controlled': True,
        'is_boss': False, 'is_lair': False, 'parent_id': None,
    }


def _enemy_snap(uid=200, name='Aelf', x=5, y=5, tier='minion', team=1):
    return {
        'id': uid, 'name': name, 'x': x, 'y': y,
        'cur_hp': 18, 'max_hp': 18, 'team': team, 'tier': tier,
        'is_player_controlled': False,
        'is_boss': tier == 'boss',
        'is_lair': tier == 'spawner',
        'parent_id': None,
    }


def _cast_chain(seq_start, caster, spell_name, target, damage, dtype,
                is_player=False):
    """cast_begin + EventOnPreDamaged + EventOnDamaged chain."""
    return [
        {
            'sequence': seq_start, 'parent': None,
            'event_type': 'cast_begin',
            'payload': {
                'caster': caster,
                'spell': {'name': spell_name, 'melee': False,
                          'cur_charges': 1, 'max_charges': 1},
                'is_player': is_player,
                'pay_costs': True,
            },
            'marks': [],
        },
        {
            'sequence': seq_start + 1, 'parent': seq_start,
            'event_type': 'EventOnPreDamaged',
            'payload': {
                'target': target,
                'damage_pre_resist': damage, 'damage_post_resist': damage,
                'resisted': False,
                'damage_type': dtype,
                'source_name': spell_name,
            },
            'marks': [],
        },
        {
            'sequence': seq_start + 2, 'parent': seq_start,
            'event_type': 'EventOnDamaged',
            'payload': {
                'target': target,
                'damage': damage,
                'damage_type': dtype,
                'source_name': spell_name,
            },
            'marks': [],
        },
    ]


# ---- ItemBuffer ----


def test_buffer_append_entries_and_len():
    buf = ItemBuffer(max_entries=10)
    buf.append('L1', 3, [make_item(None, [], 'x', row_key='k', seqs=[1])])
    buf.append('L1', 4, [make_item(None, [], 'y', row_key='k', seqs=[2])])
    assert len(buf) == 2
    entries = buf.entries()
    assert entries[0]['level'] == 'L1' and entries[0]['turn'] == 3
    assert entries[1]['items'][0]['text'] == 'y'


def test_buffer_cap_drops_oldest():
    buf = ItemBuffer(max_entries=3)
    for turn in range(1, 6):
        buf.append('L1', turn, [make_item(None, [], f't{turn}')])
    assert len(buf) == 3
    turns = [e['turn'] for e in buf.entries()]
    assert turns == [3, 4, 5]


def test_buffer_skips_empty_and_clears():
    buf = ItemBuffer(max_entries=3)
    buf.append('L1', 1, [])
    assert len(buf) == 0
    buf.append('L1', 2, [make_item(None, [], 'z')])
    buf.clear()
    assert len(buf) == 0


def test_make_item_shape_filters_falsy():
    it = make_item(2, [None, {'id': 1}], 'text',
                   row_key='orphan.dot', seqs=[5, None, 6])
    assert it['rank'] == 2
    assert it['anchors'] == [{'id': 1}]
    assert it['row_key'] == 'orphan.dot'
    assert it['seqs'] == [5, 6]


# ---- Orphan items ----


def test_orphan_action_item_carries_row_key_and_chain_seqs():
    caster = _enemy_snap(uid=201, name='Aelf')
    target = _enemy_snap(uid=301, name='Goblin', x=6, y=6)
    records = _cast_chain(10, caster, 'Lightning Bolt', target, 6, 'Lightning')
    idx = orphan._build_index(records)
    items, _claimed = orphan._render_action_section(
        records, idx, wizard_team=0, show_coords=True,
        movement_verbose=False)
    assert len(items) == 1
    it = items[0]
    assert it['row_key'] == 'orphan.action.single'
    assert sorted(it['seqs']) == [10, 11, 12]
    assert 'Lightning Bolt' in it['text']


def test_orphan_dot_item_seqs_cover_all_summed_ticks():
    # Two Bleed stacks ticking on one target: one item, both damage seqs.
    target = _enemy_snap(uid=401, name='Ogre')
    records = [
        {'sequence': 20, 'parent': None, 'event_type': 'buff_tick',
         'payload': {'buff': {'name': 'Bleed'}}, 'marks': []},
        {'sequence': 21, 'parent': 20, 'event_type': 'EventOnDamaged',
         'payload': {'target': target, 'damage': 3,
                     'damage_type': 'Physical', 'source_name': 'Bleed',
                     'source_turns_left': 2}, 'marks': []},
        {'sequence': 22, 'parent': 20, 'event_type': 'EventOnDamaged',
         'payload': {'target': target, 'damage': 3,
                     'damage_type': 'Physical', 'source_name': 'Bleed',
                     'source_turns_left': 2}, 'marks': []},
    ]
    idx = orphan._build_index(records)
    items, _claimed = orphan._render_status_ticks(
        records, idx, wizard_team=0, show_coords=True)
    dot_items = [it for it in items if it['row_key'] == 'orphan.dot']
    assert len(dot_items) == 1
    assert sorted(dot_items[0]['seqs']) == [21, 22]
    assert '6 Physical' in dot_items[0]['text']


def test_orphan_fire_exports_items_to_sink():
    caster = _enemy_snap(uid=202, name='Wolf')
    target = _enemy_snap(uid=302, name='Goblin', x=7, y=7)
    records = _cast_chain(30, caster, 'Bite', target, 4, 'Physical')
    sink = []
    producer = orphan._OrphanProducer()
    priority, text = producer.fire(
        records, True, False, _log, items_sink=sink)
    assert text
    assert sink, "fire() must export its items to the sink"
    assert all(it['text'] for it in sink)
    assert all(it['seqs'] for it in sink)
    assert sink[0]['row_key'] == 'orphan.action.single'


# ---- Digest items (coarse: one per chain) ----


def test_digest_items_sink_one_item_per_chain_with_chain_seqs():
    from journal import journal as J
    wiz = _wizard_snap()
    target = _enemy_snap(uid=501, name='Goblin')
    records = _cast_chain(40, wiz, 'Magic Missile', target, 7, 'Arcane',
                          is_player=True)
    # Death so the chain renders a killed line (any output works).
    records.append({
        'sequence': 43, 'parent': 40, 'event_type': 'EventOnDeath',
        'payload': {'target': target}, 'marks': [],
    })
    J.reset('test_composed_items_digest')
    J.records.extend(records)
    sink = []
    composer = digest._DigestComposer()
    priority, text = composer.compose_section(_log, items_sink=sink)
    J.reset('test_composed_items_digest_done')
    assert text
    assert len(sink) == 1
    it = sink[0]
    assert it['row_key'] == 'digest.chain'
    assert sorted(it['seqs']) == [40, 41, 42, 43]
    assert it['text'] == text


# ---- Crisis item (coarse: one per fire, claimed seqs) ----


def test_crisis_item_collects_claimed_seqs():
    wiz = _wizard_snap()
    records = [{
        'sequence': 50, 'parent': None, 'event_type': 'EventOnDamaged',
        'payload': {'target': wiz, 'damage': 5, 'damage_type': 'Fire',
                    'source_name': 'Fire Bolt'},
        'marks': [],
    }]
    sink = []
    producer = crisis._CrisisProducer()
    priority, text = producer.fire(records, None, _log, items_sink=sink)
    assert 'Wizard took 5' in text
    assert len(sink) == 1
    it = sink[0]
    assert it['row_key'] == 'crisis.fire'
    assert it['seqs'] == [50]
    assert it['text'] == text
    # Collector disarmed after the fire.
    assert crisis._fire_claim_seqs is None


# ---- Equipment item (coarse: one per rendered chain) ----


def test_equipment_item_per_chain():
    target = _enemy_snap(uid=601, name='Imp')
    records = [
        {'sequence': 60, 'parent': None, 'event_type': 'equipment_tick',
         'payload': {'buff': {'name': 'Stone Mask'}}, 'marks': []},
        {'sequence': 61, 'parent': 60, 'event_type': 'EventOnDamaged',
         'payload': {'target': target, 'damage': 4,
                     'damage_type': 'Physical',
                     'source_name': 'Stone Mask'}, 'marks': []},
    ]
    sink = []
    producer = equipment._EquipmentProducer()
    priority, text = producer.fire(records, True, _log, items_sink=sink)
    assert text
    assert len(sink) == 1
    it = sink[0]
    assert it['row_key'] == 'equipment.chain'
    assert sorted(it['seqs']) == [60, 61]
    assert it['text'] == text


# ---- Pipeline buffer gating ----


def _fresh_producers():
    crisis.producer._last_processed_seq = -1
    orphan.producer._last_processed_seq = -1
    equipment.producer._last_processed_seq = -1
    digest.composer._last_digested_root_seq = None


class _Tts:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class _PipeCfg:
    crisis_enabled = True
    digest_enabled = True
    equipment_enabled = True
    orphan_enabled = True
    show_coordinates = True
    movement_verbose = False
    review_buffer_enabled = False


def _pipeline_fire_with(records, review_flag):
    import pipeline
    from journal import journal as J
    J.reset('test_composed_items_pipeline')
    J.records.extend(records)
    _fresh_producers()
    global_buffer.clear()
    cfg = _PipeCfg()
    cfg.review_buffer_enabled = review_flag
    tts = _Tts()
    pipeline.fire_pipeline(tts, _log, cfg, None)
    J.reset('test_composed_items_pipeline_done')
    return tts


def test_pipeline_buffer_off_by_default():
    caster = _enemy_snap(uid=701, name='Bat')
    target = _enemy_snap(uid=702, name='Goblin')
    records = _cast_chain(70, caster, 'Screech', target, 2, 'Physical')
    tts = _pipeline_fire_with(records, review_flag=False)
    assert tts.spoken, "pipeline must still speak"
    assert len(global_buffer) == 0


def test_pipeline_buffer_on_retains_fire_items():
    caster = _enemy_snap(uid=801, name='Bat')
    target = _enemy_snap(uid=802, name='Goblin')
    records = _cast_chain(80, caster, 'Screech', target, 2, 'Physical')
    tts = _pipeline_fire_with(records, review_flag=True)
    assert tts.spoken
    assert len(global_buffer) == 1
    entry = global_buffer.entries()[0]
    assert entry['items']
    assert all(it['seqs'] for it in entry['items'])
    # The retained text reassembles to what was spoken (orphan-only fire:
    # the utterance is exactly the joined item texts).
    joined = " ".join(it['text'] for it in entry['items'])
    assert joined == tts.spoken[0]
    global_buffer.clear()
