"""Dirty-marking container wrappers — Root-1 sweep cost fix, step 1.

Drop-in twins of dict / defaultdict / list that report their own mutations:
any write on an OWNED wrapper adds the owner's id to the module dirty-set,
which the container-diff sweep drains instead of comparing every unit at
every boundary (plan: docs/DIRTY_MARKING_SWEEP_BUILD_PLAN.md, REV B).

Discipline (gate-pinned, D1):
- The vanilla superclass operation ALWAYS runs first; marking happens after
  and is exception-guarded — a wrapper bug can lose a mark, never touch
  game state. Exceptions from the vanilla op (KeyError from popitem on
  empty, etc.) propagate untouched, before any mark.
- __init__ is NOT overridden: constructor signatures stay identical to the
  vanilla types. defaultdict.copy() reconstructs via
  type(self)(default_factory, items) mid-spell (Spells.py:11390) — an
  altered signature would crash a player's cast. Ownership attaches AFTER
  construction (the `own` helper); an unowned wrapper's marks are no-ops,
  so transient copies stay inert.
- __reduce__ returns the VANILLA type: savegames never reference mod
  classes; load-without-mod is untouched. deepcopy degrades to the vanilla
  type by the same route — deliberate (plan D4).
- __missing__ is deliberately NOT overridden: its dispatch through subclass
  __setitem__ is Python-version-sensitive, and zero-normalization in the
  diff makes read-path inserts harmless either way (plan D1/D4). The nested
  bonus family is covered by the sweep's always-compare-wizard rule, not by
  recursive wrapping.
"""

from collections import defaultdict

# The module dirty-set: ids of units with at least one container write since
# the last sweep drain. container_diff drains it (step 4); reseed clears it.
dirty_ids = set()


def own(wrapper, owner_id):
    """Attach ownership after construction. owner_id is a plain int
    (id(unit)) — no reference held, no cycles."""
    wrapper._owner_id = owner_id
    return wrapper


class _DirtyMarkMixin:
    # Class-level default so unowned instances (fresh constructions,
    # defaultdict.copy() results) mark as no-ops without per-instance setup.
    _owner_id = None

    def _mark(self):
        try:
            oid = self._owner_id
            if oid is not None:
                dirty_ids.add(oid)
        except Exception:
            pass


class DirtyDict(_DirtyMarkMixin, dict):
    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        self._mark()

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self._mark()

    def pop(self, *args):
        result = dict.pop(self, *args)
        self._mark()
        return result

    def popitem(self):
        result = dict.popitem(self)
        self._mark()
        return result

    def clear(self):
        dict.clear(self)
        self._mark()

    def update(self, *args, **kwargs):
        dict.update(self, *args, **kwargs)
        self._mark()

    def setdefault(self, key, default=None):
        result = dict.setdefault(self, key, default)
        self._mark()
        return result

    def __ior__(self, other):
        result = dict.__ior__(self, other)
        self._mark()
        return result

    def __reduce__(self):
        return (dict, (dict(self),))


class DirtyDefaultDict(_DirtyMarkMixin, defaultdict):
    def __setitem__(self, key, value):
        defaultdict.__setitem__(self, key, value)
        self._mark()

    def __delitem__(self, key):
        defaultdict.__delitem__(self, key)
        self._mark()

    def pop(self, *args):
        result = defaultdict.pop(self, *args)
        self._mark()
        return result

    def popitem(self):
        result = defaultdict.popitem(self)
        self._mark()
        return result

    def clear(self):
        defaultdict.clear(self)
        self._mark()

    def update(self, *args, **kwargs):
        defaultdict.update(self, *args, **kwargs)
        self._mark()

    def setdefault(self, key, default=None):
        result = defaultdict.setdefault(self, key, default)
        self._mark()
        return result

    def __ior__(self, other):
        result = defaultdict.__ior__(self, other)
        self._mark()
        return result

    def __reduce__(self):
        return (defaultdict, (self.default_factory, dict(self)))


class DirtyList(_DirtyMarkMixin, list):
    def append(self, item):
        list.append(self, item)
        self._mark()

    def remove(self, item):
        list.remove(self, item)
        self._mark()

    def extend(self, iterable):
        list.extend(self, iterable)
        self._mark()

    def insert(self, index, item):
        list.insert(self, index, item)
        self._mark()

    def pop(self, *args):
        result = list.pop(self, *args)
        self._mark()
        return result

    def clear(self):
        list.clear(self)
        self._mark()

    def sort(self, **kwargs):
        list.sort(self, **kwargs)
        self._mark()

    def reverse(self):
        list.reverse(self)
        self._mark()

    def __setitem__(self, index, value):
        list.__setitem__(self, index, value)
        self._mark()

    def __delitem__(self, index):
        list.__delitem__(self, index)
        self._mark()

    def __iadd__(self, other):
        result = list.__iadd__(self, other)
        self._mark()
        return result

    def __imul__(self, n):
        result = list.__imul__(self, n)
        self._mark()
        return result

    def __reduce__(self):
        return (list, (list(self),))
