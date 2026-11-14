from pathlib import Path

import pytest

from reviewer.error import InputError
from reviewer.test_target import analyze, module_name

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "sample_module.py"


def test_analyze_extracts_every_public_function_and_method():
    target = analyze(SAMPLE, ROOT)

    names = [c.qualified_name for c in target.callables]
    assert names == [
        "clamp",
        "word_count",
        "median",
        "Stopwatch.__init__",
        "Stopwatch.start",
        "Stopwatch.elapsed",
        "pick",
    ]
    assert "_private_helper" not in names


def test_analyze_reports_module_path_and_test_file_name():
    target = analyze(SAMPLE, ROOT)
    assert target.path == "tests/fixtures/sample_module.py"
    assert target.module == "tests.fixtures.sample_module"
    assert target.test_file_name == "test_sample_module.py"


def test_analyze_detects_time_and_randomness_dependencies():
    assert set(analyze(SAMPLE, ROOT).external) >= {"time", "randomness"}


def test_signatures_include_arguments():
    clamp = analyze(SAMPLE, ROOT).callables[0]
    assert clamp.signature == "def clamp(value, low, high)"
    assert clamp.doc.startswith("Return value limited")


def test_existing_tests_are_found_by_name(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add(): pass\n", encoding="utf-8")

    target = analyze(tmp_path / "calc.py", tmp_path)

    assert list(target.existing_tests) == ["tests/test_calc.py"]


def test_non_python_file_is_rejected(tmp_path):
    (tmp_path / "app.js").write_text("export const a = 1\n", encoding="utf-8")
    with pytest.raises(InputError, match="Python"):
        analyze(tmp_path / "app.js", tmp_path)


def test_file_outside_root_is_rejected(tmp_path):
    with pytest.raises(InputError, match="outside"):
        analyze(SAMPLE, tmp_path)


def test_module_without_public_callables_is_rejected(tmp_path):
    (tmp_path / "consts.py").write_text("VALUE = 1\n\ndef _hidden():\n    pass\n", encoding="utf-8")
    with pytest.raises(InputError, match="no public"):
        analyze(tmp_path / "consts.py", tmp_path)


def test_unparseable_module_is_rejected(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(InputError, match="cannot parse"):
        analyze(tmp_path / "broken.py", tmp_path)


def test_module_name_requires_identifier_parts():
    assert module_name("pkg/__init__.py") == "pkg"
    with pytest.raises(InputError):
        module_name("10-messages.py")
