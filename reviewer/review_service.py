"""Orchestrate a review: limits → chunks → Claude → verify → merge → sort."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources

from pydantic import ValidationError

from .chunk import DEFAULT_CHUNK_TOKENS, Chunk, halve, pack
from .claude_client import RawFinding, ReviewClient
from .error import InputError, InvalidResponseError, OutputLimitError, RefusalError
from .model import Finding, Review, Severity, SkippedSource, Usage
from .source import Source

log = logging.getLogger(__name__)

MAX_SPLIT_DEPTH = 3


def load_system_prompt() -> str:
    return resources.files("reviewer.prompt").joinpath("review_system.md").read_text("utf-8")


@dataclass(frozen=True)
class ReviewLimit:
    max_files: int = 200
    max_tokens: int = 400_000


@dataclass
class ReviewOptions:
    min_severity: Severity = Severity.INFO
    limit: ReviewLimit = field(default_factory=ReviewLimit)
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS


@dataclass
class _ChunkOutcome:
    findings: list[Finding] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    skipped: list[SkippedSource] = field(default_factory=list)
    dropped: int = 0

    def extend(self, other: _ChunkOutcome) -> None:
        self.findings.extend(other.findings)
        self.summaries.extend(other.summaries)
        self.usage = self.usage + other.usage
        self.skipped.extend(other.skipped)
        self.dropped += other.dropped


def check_limits(sources: list[Source], client: ReviewClient, system: str, limit: ReviewLimit):
    if not sources:
        raise InputError("no reviewable source found")
    if len(sources) > limit.max_files:
        raise InputError(
            f"{len(sources)} files exceed the limit of {limit.max_files}; "
            "narrow the target or raise --max-files"
        )
    payload = Chunk(tuple(sources)).render()
    tokens = client.count_tokens(system, payload)
    if tokens > limit.max_tokens:
        raise InputError(
            f"input is ~{tokens} tokens, above the limit of {limit.max_tokens}; "
            "narrow the target or raise --max-tokens"
        )


def verify_finding(raw: RawFinding, chunk: Chunk) -> Finding | None:
    """Keep a finding only if its file and full line range were actually sent."""
    lines: set[int] = set()
    for source in chunk.sources:
        if source.path == raw.file:
            lines |= source.line_numbers
    if not lines or raw.line_start not in lines or raw.line_end not in lines:
        return None
    try:
        return Finding(**raw.model_dump())
    except ValidationError:
        return None


def _review_chunk(chunk: Chunk, client: ReviewClient, system: str, depth: int = 0) -> _ChunkOutcome:
    outcome = _ChunkOutcome()
    try:
        response = client.review_chunk(system, chunk.render())
    except OutputLimitError:
        parts = halve(chunk)
        if depth >= MAX_SPLIT_DEPTH or len(parts) == 1:
            outcome.skipped.extend(
                SkippedSource(file=path, reason="response too long even after splitting")
                for path in chunk.paths
            )
            return outcome
        log.debug("max_tokens hit; splitting chunk of %d sources", len(chunk.sources))
        for part in parts:
            outcome.extend(_review_chunk(part, client, system, depth + 1))
        return outcome
    except RefusalError as exc:
        outcome.skipped.extend(
            SkippedSource(file=path, reason=f"declined by Claude ({exc.category or 'unknown'})")
            for path in chunk.paths
        )
        return outcome
    except InvalidResponseError:
        outcome.skipped.extend(
            SkippedSource(file=path, reason="invalid response from Claude") for path in chunk.paths
        )
        return outcome

    outcome.usage = response.usage
    if response.result.summary.strip():
        outcome.summaries.append(response.result.summary.strip())
    for raw in response.result.findings:
        finding = verify_finding(raw, chunk)
        if finding is None:
            outcome.dropped += 1
            log.debug("dropped finding citing unknown location: %s:%s", raw.file, raw.line_start)
        else:
            outcome.findings.append(finding)
    return outcome


def deduplicate(findings: list[Finding]) -> list[Finding]:
    """Same file + range + title (case-insensitive) → keep the most severe one."""
    best: dict[tuple[str, int, int, str], Finding] = {}
    for finding in findings:
        key = (finding.file, finding.line_start, finding.line_end, finding.title.strip().lower())
        current = best.get(key)
        if current is None or finding.severity.rank < current.severity.rank:
            best[key] = finding
    return list(best.values())


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.severity.rank, f.file, f.line_start, f.line_end))


def _summary(findings: list[Finding], summaries: list[str], source_count: int) -> str:
    counts = {severity: 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity] += 1
    tally = ", ".join(f"{count} {severity.value}" for severity, count in counts.items() if count)
    head = f"{len(findings)} finding(s) across {source_count} source(s)"
    head += f" ({tally})." if tally else "."
    return " ".join([head, *summaries]).strip()


def review(
    sources: list[Source],
    client: ReviewClient,
    options: ReviewOptions | None = None,
    now: datetime | None = None,
) -> Review:
    options = options or ReviewOptions()
    system = load_system_prompt()
    check_limits(sources, client, system, options.limit)

    total = _ChunkOutcome()
    for chunk in pack(sources, options.chunk_tokens):
        total.extend(_review_chunk(chunk, client, system))

    findings = [f for f in deduplicate(total.findings) if f.severity.at_least(options.min_severity)]
    findings = sort_findings(findings)
    skipped = list({s.file: s for s in total.skipped}.values())
    source_count = len({source.path for source in sources})

    return Review(
        findings=findings,
        summary=_summary(findings, total.summaries, source_count),
        model=client.model,
        usage=total.usage,
        source_count=source_count,
        skipped=skipped,
        dropped_finding_count=total.dropped,
        created_at=now or datetime.now(timezone.utc),
    )
