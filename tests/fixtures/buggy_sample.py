"""Fixture with seeded bugs for the live smoke test. Do not fix."""


def average(values):
    total = 0
    for value in values:
        total += value
    return total / len(values)  # seeded bug: ZeroDivisionError on an empty list (line 8)


def last_items(items, count):
    return items[len(items) - count - 1 :]  # seeded bug: off-by-one returns count + 1 items
