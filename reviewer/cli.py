"""Command-line interface.

    python -m reviewer review <path|->      code review (plan 001)
    python -m reviewer test-gen <file.py>   generate a pytest suite (plan 002)
    python -m reviewer report <path>        quality report (plan 002)

Exit codes: 0 success, 1 findings at or above --fail-on (review) or verdict at or
above --fail-on-verdict (report), 2 usage/configuration error, 3 Claude API error,
4 test runner error.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from dotenv import load_dotenv

from .claude_client import DEFAULT_EFFORT, DEFAULT_MODEL, EFFORTS, ClaudeClient
from .error import InputError, ReviewerError
from .model import Severity, TestSuite, Verdict
from .render import (
    FORMATS,
    render,
    render_report,
    suite_to_text,
    write,
    write_report,
    write_suite,
)
from .report_service import ReportOptions, build_report
from .review_service import ReviewLimit, ReviewOptions, review
from .source import Source, collect_dir, collect_file, parse_diff
from .test_gen_service import DEFAULT_MAX_ITERATION, generate
from .test_runner import DEFAULT_TIMEOUT, SubprocessRunner, TestRunner
from .test_target import analyze

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

DEFAULT_OUT_DIR = ".orchestrate/review"
DEFAULT_TEST_GEN_DIR = ".orchestrate/test-gen"
DEFAULT_REPORT_DIR = ".orchestrate/quality-report"
APPLY_DIR = Path("tests") / "unit"
SEVERITIES = [s.value for s in Severity]

ClientFactory = Callable[[argparse.Namespace], Any]
RunnerFactory = Callable[[argparse.Namespace], TestRunner]


def _add_claude_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=os.environ.get("REVIEWER_MODEL", DEFAULT_MODEL))
    parser.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT)
    parser.add_argument("--no-fallback", action="store_true", help="disable server-side fallback")
    parser.add_argument("--verbose", action="store_true", help="log request ids and token usage")


def _add_source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--include", action="append", help="glob of files to include (repeatable)")
    parser.add_argument("--exclude", action="append", help="glob of files to exclude (repeatable)")
    parser.add_argument("--max-files", type=int, default=ReviewLimit.max_files)
    parser.add_argument("--max-tokens", type=int, default=ReviewLimit.max_tokens)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reviewer", description="AI Code Reviewer powered by Claude"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rv = sub.add_parser("review", help="review a file, directory, or diff")
    rv.add_argument("target", help="file, directory, diff file (with --diff), or - for stdin")
    rv.add_argument("--diff", action="store_true", help="treat the target as a unified diff")
    rv.add_argument("--format", choices=FORMATS, default="text")
    rv.add_argument(
        "--out",
        nargs="?",
        const=DEFAULT_OUT_DIR,
        help=f"also write review-<timestamp>.json/.md (default dir: {DEFAULT_OUT_DIR})",
    )
    rv.add_argument("--min-severity", choices=SEVERITIES, default="info")
    rv.add_argument("--fail-on", choices=SEVERITIES, default="high")
    rv.add_argument("--no-color", action="store_true")
    _add_source_options(rv)
    _add_claude_options(rv)

    tg = sub.add_parser("test-gen", help="generate a pytest suite for one Python module")
    tg.add_argument("target", help="Python file inside the project root")
    tg.add_argument("--root", default=".", help="project root used for imports (default: .)")
    tg.add_argument("--out", default=DEFAULT_TEST_GEN_DIR)
    tg.add_argument(
        "--apply",
        action="store_true",
        help=f"also copy the suite to <root>/{APPLY_DIR.as_posix()}/ (never overwrites)",
    )
    tg.add_argument("--max-iteration", type=int, default=DEFAULT_MAX_ITERATION)
    tg.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per test run")
    _add_claude_options(tg)

    rp = sub.add_parser("report", help="quality report: review, tests, coverage, metrics")
    rp.add_argument("target", help="file or directory")
    rp.add_argument("--root", default=".", help="project root (default: .)")
    rp.add_argument("--tests", default="tests", help="existing test path, relative to root")
    rp.add_argument("--with-test-gen", action="store_true", help="also generate tests")
    rp.add_argument("--max-test-gen-files", type=int, default=10)
    rp.add_argument("--max-iteration", type=int, default=DEFAULT_MAX_ITERATION)
    rp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per test run")
    rp.add_argument("--format", choices=FORMATS, default="text")
    rp.add_argument("--out", default=DEFAULT_REPORT_DIR)
    rp.add_argument("--fail-on-verdict", choices=["warn", "fail"], default="fail")
    _add_source_options(rp)
    _add_claude_options(rp)
    return parser


def default_client_factory(args: argparse.Namespace) -> ClaudeClient:
    return ClaudeClient(model=args.model, effort=args.effort, fallback=not args.no_fallback)


def default_runner_factory(args: argparse.Namespace) -> TestRunner:
    return SubprocessRunner(root=Path(args.root), timeout=args.timeout)


def collect_sources(
    args: argparse.Namespace, stdin: TextIO, root: Path | None = None
) -> list[Source]:
    if args.target == "-":
        if not getattr(args, "diff", False):
            raise ReviewerError("reading from stdin requires --diff")
        return parse_diff(stdin.read())
    target = Path(args.target)
    if getattr(args, "diff", False):
        if not target.is_file():
            raise ReviewerError(f"diff file not found: {args.target}")
        return parse_diff(target.read_text(encoding="utf-8"))
    if target.is_dir():
        return collect_dir(target, root=root, include=args.include, exclude=args.exclude)
    if target.is_file():
        return [collect_file(target, root=root)]
    raise ReviewerError(f"target not found: {args.target}")


def _review_options(args: argparse.Namespace) -> ReviewOptions:
    return ReviewOptions(
        min_severity=Severity(getattr(args, "min_severity", "info")),
        limit=ReviewLimit(max_files=args.max_files, max_tokens=args.max_tokens),
    )


def _run_review(args, client_factory, runner_factory, stdin, stdout, stderr) -> int:
    sources = collect_sources(args, stdin)
    result = review(sources, client_factory(args), _review_options(args))
    color = args.format == "text" and not args.no_color and stdout.isatty()
    print(render(result, args.format, color=color), file=stdout)
    if args.out:
        for path in write(result, args.out):
            print(f"wrote {path.as_posix()}", file=stderr)
    return EXIT_FINDINGS if result.has_finding_at_least(Severity(args.fail_on)) else EXIT_OK


def apply_suite(suite: TestSuite, root: Path) -> Path:
    if not suite.code:
        raise InputError("no generated test survived; nothing to apply")
    destination = root / APPLY_DIR / suite.test_file
    if destination.exists():
        raise InputError(f"{destination.as_posix()} already exists; not overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:  # "x": fail rather than overwrite
        handle.write(suite.code)
    return destination


def _run_test_gen(args, client_factory, runner_factory, stdin, stdout, stderr) -> int:
    root = Path(args.root)
    target = analyze(args.target, root)
    runner = runner_factory(args)
    coverage_before = 0.0
    if target.existing_tests:
        existing = [root / path for path in target.existing_tests]
        run = runner.run_existing(existing, [root / target.path])
        coverage_before = run.coverage.get((root / target.path).resolve().as_posix(), 0.0)
    suite = generate(
        target,
        root,
        client_factory(args),
        runner,
        max_iteration=args.max_iteration,
        coverage_before=coverage_before,
    )
    print(suite_to_text(suite), file=stdout)
    paths = write_suite(suite, args.out, datetime.now(timezone.utc))
    for path in paths:
        print(f"wrote {path.as_posix()}", file=stderr)
    if args.apply:
        print(f"applied {apply_suite(suite, root).as_posix()}", file=stderr)
    return EXIT_OK


def _run_report(args, client_factory, runner_factory, stdin, stdout, stderr) -> int:
    root = Path(args.root)
    sources = collect_sources(args, stdin, root=root)
    tests_path = root / args.tests
    options = ReportOptions(
        review=_review_options(args),
        with_test_gen=args.with_test_gen,
        tests_path=tests_path if tests_path.exists() else None,
        max_iteration=args.max_iteration,
        max_test_gen_files=args.max_test_gen_files,
    )
    report = build_report(
        sources, root, client_factory(args), runner_factory(args), options, target=args.target
    )
    print(render_report(report, args.format), file=stdout)
    for path in write_report(report, args.out):
        print(f"wrote {path.as_posix()}", file=stderr)
    threshold = Verdict(args.fail_on_verdict)
    return EXIT_FINDINGS if report.verdict.rank >= threshold.rank else EXIT_OK


_COMMANDS = {"review": _run_review, "test-gen": _run_test_gen, "report": _run_report}


def main(
    argv: list[str] | None = None,
    client_factory: ClientFactory = default_client_factory,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    runner_factory: RunnerFactory = default_runner_factory,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    load_dotenv()

    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage or help
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=stderr,
        force=True,
    )
    # Keep third-party HTTP debug logs out: they can echo request headers.
    for noisy in ("anthropic", "httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        return _COMMANDS[args.command](args, client_factory, runner_factory, stdin, stdout, stderr)
    except ReviewerError as exc:
        request_id = getattr(exc, "request_id", None)
        suffix = f" (request id: {request_id})" if request_id else ""
        print(f"error: {exc}{suffix}", file=stderr)
        return exc.exit_code
