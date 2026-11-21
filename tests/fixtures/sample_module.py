"""Fixture module for test generation (plan 002). Contains one seeded bug: do not fix."""

import random
import time


def clamp(value, low, high):
    """Return value limited to the closed range [low, high]."""
    if low > high:
        raise ValueError("low must be <= high")
    return max(low, min(value, high))


def word_count(text):
    """Count whitespace-separated words."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return len(text.split())


def median(values):
    """Return the median of a non-empty list of numbers."""
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle]  # seeded bug: wrong for even-length lists (line 27)


def _private_helper():
    return 42


class Stopwatch:
    """Measure elapsed seconds with an injectable clock."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._start = None

    def start(self):
        self._start = self._clock()

    def elapsed(self):
        if self._start is None:
            raise RuntimeError("stopwatch not started")
        return self._clock() - self._start


def pick(items, rng=random):
    """Return a random element of a non-empty sequence."""
    if not items:
        raise IndexError("cannot pick from an empty sequence")
    return rng.choice(items)
