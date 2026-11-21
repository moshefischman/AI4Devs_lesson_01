"""Pack sources into token-bounded chunks without ever dropping a line."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .source import Source

TokenEstimator = Callable[[str], int]

DEFAULT_CHUNK_TOKENS = 60_000
DEFAULT_OVERLAP_LINES = 20


def estimate_tokens(text: str) -> int:
    """Conservative local estimate (~3 characters per token) used for packing only."""
    return math.ceil(len(text) / 3)


def render_source(source: Source) -> str:
    first = source.lines[0][0] if source.lines else 0
    last = source.lines[-1][0] if source.lines else 0
    attrs = f'path="{source.path}" lines="{first}-{last}"'
    if source.changed_lines:
        attrs += f' changed="{_ranges(source.changed_lines)}"'
    body = "\n".join(f"{number:>5} | {text}" for number, text in source.lines)
    return f"<file {attrs}>\n{body}\n</file>"


def _ranges(numbers: frozenset[int]) -> str:
    ordered = sorted(numbers)
    parts: list[str] = []
    start = prev = ordered[0]
    for number in ordered[1:]:
        if number != prev + 1:
            parts.append(f"{start}-{prev}" if start != prev else str(start))
            start = number
        prev = number
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(parts)


@dataclass(frozen=True)
class Chunk:
    sources: tuple[Source, ...]

    def render(self) -> str:
        return "\n\n".join(render_source(source) for source in self.sources)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(source.path for source in self.sources))


def split_source(
    source: Source,
    budget: int,
    estimate: TokenEstimator = estimate_tokens,
    overlap: int = DEFAULT_OVERLAP_LINES,
) -> list[Source]:
    """Split one source into slices that each fit `budget`, overlapping by `overlap` lines."""
    if estimate(render_source(source)) <= budget or len(source.lines) <= 1:
        return [source]

    # Per-line costs plus the full source's wrapper cost: an upper bound for any slice,
    # so slices stay within budget without re-rendering on every step.
    wrapper = estimate(render_source(Source(source.path, (), source.changed_lines))) + 2
    line_costs = [estimate(f"{number:>5} | {text}\n") for number, text in source.lines]

    slices: list[Source] = []
    start = 0
    total = len(source.lines)
    while start < total:
        end = start + 1
        used = wrapper + line_costs[start]
        while end < total and used + line_costs[end] <= budget:
            used += line_costs[end]
            end += 1
        slices.append(_slice(source, start, end))
        if end >= total:
            break
        start = max(end - overlap, start + 1)
    return slices


def _slice(source: Source, start: int, end: int) -> Source:
    lines = source.lines[start:end]
    numbers = {number for number, _ in lines}
    return Source(
        path=source.path,
        lines=lines,
        changed_lines=frozenset(source.changed_lines & numbers),
    )


def pack(
    sources: list[Source],
    budget: int = DEFAULT_CHUNK_TOKENS,
    estimate: TokenEstimator = estimate_tokens,
) -> list[Chunk]:
    """Greedy packing in input order; oversized sources are split first."""
    chunks: list[Chunk] = []
    current: list[Source] = []
    used = 0
    for source in sources:
        for part in split_source(source, budget, estimate):
            size = estimate(render_source(part))
            if current and used + size > budget:
                chunks.append(Chunk(tuple(current)))
                current, used = [], 0
            current.append(part)
            used += size
    if current:
        chunks.append(Chunk(tuple(current)))
    return chunks


def halve(chunk: Chunk, estimate: TokenEstimator = estimate_tokens) -> list[Chunk]:
    """Split a chunk into two smaller ones; used when a response hits max_tokens."""
    if len(chunk.sources) > 1:
        middle = len(chunk.sources) // 2
        return [Chunk(chunk.sources[:middle]), Chunk(chunk.sources[middle:])]
    source = chunk.sources[0]
    if len(source.lines) <= 1:
        return [chunk]
    half_budget = max(1, estimate(render_source(source)) // 2)
    return [Chunk((part,)) for part in split_source(source, half_budget, estimate, overlap=0)]
