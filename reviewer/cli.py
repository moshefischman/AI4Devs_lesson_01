"""Command-line interface: `python -m reviewer review <path|->`.

Exit codes: 0 no finding at or above --fail-on, 1 such findings exist,
2 usage/configuration error, 3 Claude API error.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv

from .claude_client import DEFAULT_EFFORT, DEFAULT_MODEL, EFFORTS, ClaudeClient, ReviewClient
from .error import ReviewerError
from .model import Severity
from .render import FORMATS, render, write
from .review_service import ReviewLimit, ReviewOptions, review
from .source import Source, collect_dir, collect_file, parse_diff

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

DEFAULT_OUT_DIR = ".orchestrate/review"
SEVERITIES = [s.value for s in Severity]

ClientFactory = Callable[[argparse.Namespace], ReviewClient]


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
    rv.add_argument("--model", default=os.environ.get("REVIEWER_MODEL", DEFAULT_MODEL))
    rv.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT)
    rv.add_argument("--min-severity", choices=SEVERITIES, default="info")
    rv.add_argument("--fail-on", choices=SEVERITIES, default="high")
    rv.add_argument("--include", action="append", help="glob of files to include (repeatable)")
    rv.add_argument("--exclude", action="append", help="glob of files to exclude (repeatable)")
    rv.add_argument("--max-files", type=int, default=ReviewLimit.max_files)
    rv.add_argument("--max-tokens", type=int, default=ReviewLimit.max_tokens)
    rv.add_argument("--no-fallback", action="store_true", help="disable server-side fallback")
    rv.add_argument("--no-color", action="store_true")
    rv.add_argument("--verbose", action="store_true", help="log request ids and token usage")
    return parser


def default_client_factory(args: argparse.Namespace) -> ReviewClient:
    return ClaudeClient(model=args.model, effort=args.effort, fallback=not args.no_fallback)


def collect_sources(args: argparse.Namespace, stdin: TextIO) -> list[Source]:
    if args.target == "-":
        if not args.diff:
            raise ReviewerError("reading from stdin requires --diff")
        return parse_diff(stdin.read())
    target = Path(args.target)
    if args.diff:
        if not target.is_file():
            raise ReviewerError(f"diff file not found: {args.target}")
        return parse_diff(target.read_text(encoding="utf-8"))
    if target.is_dir():
        return collect_dir(target, include=args.include, exclude=args.exclude)
    if target.is_file():
        return [collect_file(target)]
    raise ReviewerError(f"target not found: {args.target}")


def main(
    argv: list[str] | None = None,
    client_factory: ClientFactory = default_client_factory,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
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
        sources = collect_sources(args, stdin)
        client = client_factory(args)
        options = ReviewOptions(
            min_severity=Severity(args.min_severity),
            limit=ReviewLimit(max_files=args.max_files, max_tokens=args.max_tokens),
        )
        result = review(sources, client, options)
    except ReviewerError as exc:
        request_id = getattr(exc, "request_id", None)
        suffix = f" (request id: {request_id})" if request_id else ""
        print(f"error: {exc}{suffix}", file=stderr)
        return exc.exit_code

    color = args.format == "text" and not args.no_color and stdout.isatty()
    print(render(result, args.format, color=color), file=stdout)
    if args.out:
        for path in write(result, args.out):
            print(f"wrote {path.as_posix()}", file=stderr)

    return EXIT_FINDINGS if result.has_finding_at_least(Severity(args.fail_on)) else EXIT_OK
