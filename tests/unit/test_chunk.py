from reviewer.chunk import Chunk, estimate_tokens, halve, pack, render_source, split_source
from tests.factory import make_source


def _covered(parts):
    return {number for part in parts for number, _ in part.lines}


def test_estimate_tokens_is_conservative():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 2


def test_render_source_numbers_lines_and_lists_changed_ranges():
    text = render_source(make_source("a.py", 8, changed={2, 3, 7}))
    assert text.startswith('<file path="a.py" lines="1-8" changed="2-3,7">')
    assert "    2 | line_2 = 2" in text
    assert text.endswith("</file>")


def test_small_sources_share_one_chunk():
    chunks = pack([make_source("a.py"), make_source("b.py")], budget=10_000)
    assert len(chunks) == 1
    assert chunks[0].paths == ("a.py", "b.py")


def test_no_chunk_exceeds_budget():
    sources = [make_source(f"f{i}.py", 30) for i in range(10)]
    budget = 600
    chunks = pack(sources, budget=budget)
    assert len(chunks) > 1
    assert all(estimate_tokens(chunk.render()) <= budget for chunk in chunks)


def test_every_line_is_sent_at_least_once():
    sources = [make_source("big.py", 500), make_source("small.py", 3)]
    chunks = pack(sources, budget=800)
    for source in sources:
        parts = [part for chunk in chunks for part in chunk.sources if part.path == source.path]
        assert _covered(parts) == set(range(1, len(source.lines) + 1))


def test_split_source_slices_fit_budget_overlap_and_keep_numbers():
    source = make_source("big.py", 400, changed={150, 399})
    budget = 1_000
    slices = split_source(source, budget, overlap=10)

    assert len(slices) > 1
    assert all(estimate_tokens(render_source(part)) <= budget for part in slices)
    assert _covered(slices) == set(range(1, 401))
    first_end = slices[0].lines[-1][0]
    second_start = slices[1].lines[0][0]
    assert second_start == first_end - 10 + 1
    assert frozenset().union(*(part.changed_lines for part in slices)) == {150, 399}


def test_split_source_returns_small_source_unchanged():
    source = make_source("a.py", 3)
    assert split_source(source, 10_000) == [source]


def test_halve_splits_multi_source_chunk_in_two():
    chunk = Chunk((make_source("a.py"), make_source("b.py"), make_source("c.py")))
    left, right = halve(chunk)
    assert left.paths == ("a.py",)
    assert right.paths == ("b.py", "c.py")


def test_halve_splits_single_source_by_lines_without_losing_any():
    chunk = Chunk((make_source("a.py", 100),))
    parts = halve(chunk)
    assert len(parts) >= 2
    assert _covered([p.sources[0] for p in parts]) == set(range(1, 101))


def test_halve_cannot_split_a_single_line():
    chunk = Chunk((make_source("a.py", 1),))
    assert halve(chunk) == [chunk]
