"""Live smoke test against the real Claude API. Spends credit.

Run only with explicit approval:  pytest -m live
"""

from pathlib import Path

import pytest
from dotenv import load_dotenv

from reviewer.claude_client import ClaudeClient
from reviewer.model import Category
from reviewer.review_service import review
from reviewer.source import collect_file

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "buggy_sample.py"


@pytest.mark.live
def test_live_review_finds_the_seeded_bug():
    load_dotenv()  # same credential resolution as the CLI
    source = collect_file(FIXTURE, root=FIXTURE.parent)

    result = review([source], ClaudeClient())

    bugs = [f for f in result.findings if f.category is Category.BUG]
    assert any(f.line_start <= 8 <= f.line_end for f in bugs), result.findings
