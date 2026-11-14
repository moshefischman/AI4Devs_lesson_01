# System Architecture

## Purpose
- Provide a concise architecture reference for service boundaries, ownership, and major flows.

## System Overview
A single Python package, `reviewer/`, used as a library or through its CLI
(`python -m reviewer`). It runs locally; the only remote dependency is the Claude API.

## Primary Components
| Module | Responsibility |
|---|---|
| `reviewer/cli.py` | Argument parsing, exit codes, wiring |
| `reviewer/source.py` | Collect files, directories, and diffs; skip ignored, binary, and secret files |
| `reviewer/chunk.py` | Pack sources into token-bounded chunks; split oversized sources with overlap |
| `reviewer/claude_client.py` | The only module that imports the Anthropic SDK; structured output, errors |
| `reviewer/review_service.py` | Orchestrates chunk → Claude → validate → merge → sort |
| `reviewer/render.py` | Text, JSON, and Markdown output; file writing |
| `reviewer/model.py` | Pydantic models: `Review`, `Finding`, `Usage`, `SkippedSource` |
| `reviewer/prompt/` | Versioned system prompts |

## Data Flow
1. CLI resolves the target into `Source` objects (line numbers preserved).
2. Limits are checked (file count, then `messages.count_tokens` on the whole payload).
3. Sources are packed into chunks; each chunk is one streaming request with
   `output_config.format` (JSON schema), adaptive thinking, and configurable effort.
4. Each response is validated; findings that cite lines not present in the chunk are
   dropped and counted.
5. Findings are de-duplicated, filtered by `--min-severity`, sorted, and rendered.

## Auth
- Credentials resolve through the Anthropic SDK (`ANTHROPIC_API_KEY` from the
  environment or `.env`, or an `ant auth login` profile). The package never reads,
  prints, or logs the key value.

## External Dependencies
- Claude API (`claude-opus-5-5` by default, `REVIEWER_MODEL` / `--model` to override).
- Server-side refusal fallback (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`), disable with `--no-fallback`.

## Operational Concerns
- Retries for 429/5xx/connection errors are delegated to the SDK (`max_retries`).
- `stop_reason: "max_tokens"` → the chunk is split and retried; `refusal` → the chunk's
  files are reported as skipped sources.
- `--verbose` logs request ids and token usage, never credentials.
- Env/secret files are never collected as sources.

## Change Log
- 2026-11-14 — Initial architecture for the code review engine (plan 001).
