"""Step-1 tests for the dirty-marking wrappers (plan REV B, D1/D4).

Pure-Python: no game imports. Every mutating entry point must mark when
owned and stay inert when unowned; constructors must be vanilla-identical
(the defaultdict.copy() crash shape from the gate, Spells.py:11390); pickle
must produce ONLY vanilla types (savegame discipline). NB the suite runs
stdlib pickle with a module-level factory — the game runs dill with lambdas;
the in-game probe is build step 7 (plan D4 version caveat).
"""

import copy
import pickle

import pytest

import dirty_containers
from dirty_containers import DirtyDict, DirtyDefaultDict, DirtyList, own


OWNER = 12345


def _zero():
    # Module-level so stdlib pickle can serialize the factory (the game's
    # lambdas need dill, which the game itself uses).
    return 0


@pytest.fixture(autouse=True)
def _clean_dirty_set():
    dirty_containers.dirty_ids.clear()
    yield
    dirty_containers.dirty_ids.clear()


def _marked():
    return OWNER in dirty_containers.dirty_ids


# ---- mutation coverage: every entry point marks when owned ----

DICT_MUTATIONS = [
    ('setitem', lambda d: d.__setitem__('k', 1)),
    ('delitem', lambda d: d.__delitem__('a')),
    ('pop', lambda d: d.pop('a')),
    ('pop_default', lambda d: d.pop('missing', None)),
    ('popitem', lambda d: d.popitem()),
    ('clear', lambda d: d.clear()),
    ('update', lambda d: d.update({'k': 1})),
    ('update_kwargs', lambda d: d.update(k=1)),
    ('setdefault', lambda d: d.setdefault('k', 1)),
    ('ior', lambda d: d.__ior__({'k': 1})),
]


@pytest.mark.parametrize('name,mutate', DICT_MUTATIONS)
def test_dirtydict_mutation_marks(name, mutate):
    d = own(DirtyDict({'a': 1}), OWNER)
    mutate(d)
    assert _marked(), name


@pytest.mark.parametrize('name,mutate', DICT_MUTATIONS)
def test_dirtydefaultdict_mutation_marks(name, mutate):
    d = own(DirtyDefaultDict(_zero, {'a': 1}), OWNER)
    mutate(d)
    assert _marked(), name


LIST_MUTATIONS = [
    ('append', lambda l: l.append(9)),
    ('remove', lambda l: l.remove(1)),
    ('extend', lambda l: l.extend([9])),
    ('insert', lambda l: l.insert(0, 9)),
    ('pop', lambda l: l.pop()),
    ('clear', lambda l: l.clear()),
    ('sort', lambda l: l.sort()),
    ('reverse', lambda l: l.reverse()),
    ('setitem', lambda l: l.__setitem__(0, 9)),
    ('delitem', lambda l: l.__delitem__(0)),
    ('iadd', lambda l: l.__iadd__([9])),
    ('imul', lambda l: l.__imul__(2)),
]


@pytest.mark.parametrize('name,mutate', LIST_MUTATIONS)
def test_dirtylist_mutation_marks(name, mutate):
    l = own(DirtyList([1, 2]), OWNER)
    mutate(l)
    assert _marked(), name


def test_augmented_item_write_marks():
    # The game's dominant shape: unit.resists[tag] += n.
    d = own(DirtyDefaultDict(_zero), OWNER)
    d['fire'] += 50
    assert _marked()


# ---- unowned wrappers are inert ----

def test_unowned_mutations_do_not_mark():
    DirtyDict({'a': 1})['k'] = 1
    DirtyDefaultDict(_zero)['k'] = 1
    DirtyList([1]).append(2)
    assert not dirty_containers.dirty_ids


# ---- vanilla behavior parity ----

def test_equality_with_vanilla():
    assert own(DirtyDict({'a': 1}), OWNER) == {'a': 1}
    assert own(DirtyDefaultDict(_zero, {'a': 1}), OWNER) == {'a': 1}
    assert own(DirtyList([1, 2]), OWNER) == [1, 2]


def test_default_factory_read_parity():
    # Missing-key read returns factory value and inserts, exactly as
    # vanilla. NO assertion on marking: __missing__ dispatch is
    # version-dependent and the design depends on neither outcome.
    d = own(DirtyDefaultDict(_zero), OWNER)
    assert d['missing'] == 0
    assert 'missing' in d
    assert d.default_factory is _zero


def test_defaultdict_copy_shape_does_not_crash_and_is_unowned():
    # The gate's crash-grade finding: defaultdict.copy() reconstructs via
    # type(self)(default_factory, items) — Spells.py:11390 does this on a
    # live wrapped dict mid-cast. Must not raise; result keeps subclass
    # behavior but starts unowned, so its writes mark nobody.
    d = own(DirtyDefaultDict(_zero, {'a': 1}), OWNER)
    c = d.copy()
    assert c == {'a': 1}
    assert c.default_factory is _zero
    dirty_containers.dirty_ids.clear()
    c['b'] = 2
    assert not dirty_containers.dirty_ids


def test_vanilla_op_exception_propagates_before_mark():
    d = own(DirtyDict(), OWNER)
    with pytest.raises(KeyError):
        d.popitem()
    l = own(DirtyList([1]), OWNER)
    with pytest.raises(ValueError):
        l.remove(99)
    assert not dirty_containers.dirty_ids


# ---- pickle discipline (D4): saves contain ONLY vanilla types ----

def test_pickle_reduces_to_vanilla_types():
    wrappers = [
        own(DirtyDict({'a': 1}), OWNER),
        own(DirtyDefaultDict(_zero, {'a': 1}), OWNER),
        own(DirtyList([1, 2]), OWNER),
    ]
    payload = pickle.dumps(wrappers)
    assert b'Dirty' not in payload
    loaded = pickle.loads(payload)
    assert type(loaded[0]) is dict and loaded[0] == {'a': 1}
    assert type(loaded[1]) is __import__('collections').defaultdict
    assert loaded[1] == {'a': 1}
    assert loaded[1]['fresh'] == 0          # factory survives
    assert type(loaded[2]) is list and loaded[2] == [1, 2]


def test_pickle_preserves_aliasing():
    # Gate Finding 3's insurance: two references to one wrapper must come
    # back as two references to ONE object (pickle memoizes the wrapper
    # before reducing), not two divergent copies.
    d = own(DirtyDict({'a': 1}), OWNER)
    holder = {'ref1': d, 'ref2': d}
    loaded = pickle.loads(pickle.dumps(holder))
    assert loaded['ref1'] is loaded['ref2']


def test_deepcopy_degrades_to_vanilla():
    # Deliberate (plan D4): deepcopy goes through __reduce__, yielding the
    # vanilla type; a cloned unit starts unwrapped until its next seed.
    assert type(copy.deepcopy(own(DirtyDict({'a': 1}), OWNER))) is dict
    assert type(copy.deepcopy(own(DirtyList([1]), OWNER))) is list
