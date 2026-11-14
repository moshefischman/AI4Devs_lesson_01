"""Live smoke test for test generation (AC14). Spends credit and runs generated code.

Run only with explicit approval:  pytest -m live
"""

import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

from reviewer.claude_client import ClaudeClient
from reviewer.model import TestStatus
from reviewer.test_gen_service import generate
from reviewer.test_runner import SubprocessRunner
from reviewer.test_target import analyze

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "sample_module.py"


@pytest.mark.live
def test_live_test_gen_produces_a_passing_suite_that_raises_coverage(tmp_path):
    load_dotenv()  # same credential resolution as the CLI
    target = analyze(SAMPLE, ROOT)

    suite = generate(target, ROOT, ClaudeClient(), SubprocessRunner(ROOT), coverage_before=0.0)

    assert suite.code, suite.warnings
    assert suite.count(TestStatus.PASSED) >= 5, suite.cases
    assert suite.coverage_after is not None and suite.coverage_after > 50.0

    test_file = tmp_path / suite.test_file
    test_file.write_text(suite.code, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
