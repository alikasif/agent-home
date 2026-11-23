# Context Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/context-audit` and `/context-trim` skills that attribute what Claude Code loads into context (and what it costs) to the files in this repo, using only the transcripts Claude Code already writes.

**Architecture:** One module (`transcripts.py`) is the only code that knows the transcript format and yields typed records. `attribute.py` maps repo artifacts to what was loaded, `pricing.py` prices usage, `report.py` computes stats, verdicts and output, and `audit.py` is the CLI. `/context-trim` is a SKILL.md-only skill that consumes `audit.py --json`.

**Tech Stack:** Python 3.13, `uv`, standard library only at runtime. Dev: pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-11-23-context-audit-design.md`

## Global Constraints

- Python 3.13, `uv` only (never `pip`), standard library only at runtime. Type hints everywhere, docstrings on public APIs, line length 88, `ruff format` and `ruff check` clean, `pyrefly check` clean (`docs/coding-standards.md`).
- Work on branch `feat/context-audit`, never on `main`. Conventional commits, ending each commit message with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Skills live at `.claude/skills/context-audit/` and `.claude/skills/context-trim/`; `SKILL.md` in the style of `.claude/skills/token-dashboard/SKILL.md`.
- Transcripts are read-only, always opened with `encoding="utf-8"`. Works on Windows; no POSIX shell assumed.
- Privacy: output contains names, sizes and counts only, never message, attachment or file content.
- Every number in the report is labeled `measured` or `estimated`. The default chars-per-token ratio constant is `CHARS_PER_TOKEN = 4.0` and is used only to split measured totals across files.
- An unrecognised transcript format raises `TranscriptFormatError` naming the Claude Code `version`; the CLI exits with code 2 and prints nothing that looks like a report.
- "used a/b": `b` counts only sessions where the artifact was loaded.
- Always-on files (`AGENTS.md`, rules) get only `keep` or `trim` verdicts. A structural move may be mentioned, never auto-applied.
- Dollar columns are `$ in window` and `$/session`. No monthly figure.
- `MIN_SESSIONS = 10`: fewer loaded sessions means verdict `insufficient data`.

## Transcript format reference

Observed in `~/.claude/projects/<project>/<session>.jsonl` (one JSON object per line). Confirm any entry marked "confirm" by printing only its keys from a real transcript, never its content.

| `type` | Fields used |
|---|---|
| `api-request-shape` | `sessionId`, `shapeHash`, `version`, `shape.system[].text`, `shape.tools[].{name,description}` |
| `api-request` | `sessionId`, `timestamp`, `shapeHash`, `params.model`, `version` |
| `assistant` | `message.usage.{input_tokens,cache_creation_input_tokens,cache_read_input_tokens,output_tokens}`, `message.content[]` with `tool_use` blocks `{name,input}` (`Skill`: `input.skill`; `Agent`: `input.subagent_type`) |
| `user` | `message.content` string containing `<command-name>NAME</command-name>` for slash commands; list content with `tool_result` blocks `{tool_use_id,content}` (confirm) |
| `attachment` | `attachment.type` is one of: `skill_listing` (`content` str of `- name: description` lines, `names`, `isInitial`), `agent_listing_delta` (`addedTypes`, `addedLines` aligned by index, `removedTypes`, `isInitial`), `instructions` (`files[].{path,content}`) |
| `cost-state` | `totalCostUSD`, `modelUsage.<model>.costUSD` |

## Review Focus

Failure modes the spec implies that a user will hit; each has a test in the owning task.
1. No transcripts match (missing directory, bad `--project`, empty `--since` window): clear message, exit 0, no empty table. (Task 6)
2. A live session's last JSONL line is truncated: skipped, not a crash. (Task 1)
3. An artifact exists in the repo but was never loaded in any session: shown with `insufficient data`, no divide-by-zero. (Task 4)
4. A repo file was edited after the transcript was written, so its text no longer matches: reported as unmatched, never guessed. (Task 3)
5. Windows console is cp1252 and the report contains non-ASCII: no `UnicodeEncodeError`. (Task 6)

---

### Task 1: Scaffolding, fixture factory, transcript parsing

**Files:**
- Create: `pyproject.toml` (repo root: dev deps pytest, ruff, pyrefly; `[tool.pytest.ini_options]` with `pythonpath = [".claude/skills/context-audit/scripts"]` and `testpaths = [".claude/skills/context-audit/tests"]`)
- Create: `.claude/skills/context-audit/scripts/__init__.py`, `.claude/skills/context-audit/scripts/transcripts.py`
- Create: `.claude/skills/context-audit/tests/transcript_factory.py`, `.claude/skills/context-audit/tests/test_transcripts.py`

**Interfaces:**
- Produces (`transcripts.py`, all frozen dataclasses):
  - `class TranscriptFormatError(Exception)`
  - `Usage(input: int, cache_write: int, cache_read: int, output: int)`
  - `Call(timestamp: str, model: str, shape_hash: str | None, usage: Usage)`
  - `Invocation(kind: Literal["skill", "agent"], name: str, via: Literal["tool", "slash"])`
  - `InstructionFile(path: str, content: str, call_index: int)`
  - `ToolResult(tool_name: str, chars: int, content_hash: str)`
  - `Session(session_id: str, project: str, version: str, shapes: dict[str, RequestShape], calls: list[Call], skill_listings: list[tuple[int, str]], agent_added: list[tuple[int, dict[str, str]]], agent_removed: list[tuple[int, list[str]]], instructions: list[InstructionFile], invocations: list[Invocation], tool_results: list[ToolResult], cost_usd: float | None)` where tuple `int` is the index into `calls` of the first call made after that attachment arrived.
  - `RequestShape(system: tuple[str, ...], tools: tuple[tuple[str, str], ...])` (tool name, description)
  - `load_sessions(root: Path, project: str | None = None, since: date | None = None) -> Iterator[Session]`
- Produces (`transcript_factory.py`): `TranscriptBuilder` with chainable methods `shape`, `request`, `assistant`, `skill_listing`, `agent_delta`, `instructions`, `slash_command`, `tool_use`, `tool_result`, `cost_state`, and `write(path: Path) -> Path`. It emits entries in the format reference above using only synthetic text.

- [ ] **Step 1: Create branch and scaffold.** `git checkout -b feat/context-audit`; create `pyproject.toml`; `uv add --dev pytest ruff pyrefly`; run `uv run pytest` (expect "no tests ran").
- [ ] **Step 2: Write `TranscriptBuilder`** in `transcript_factory.py` producing a session with one shape, three requests, usage on each assistant entry, one skill listing, one agent delta, one instructions attachment, one `Skill` tool_use, one slash command, one tool_result, one cost-state.
- [ ] **Step 3: Write failing tests** in `test_transcripts.py`:
  - `test_load_sessions_parses_calls_and_usage`: three calls with the factory's usage values, `session.cost_usd` equals the cost-state value.
  - `test_attachments_are_indexed_to_next_call`: a `skill_listing` attachment written between call 1 and call 2 has index `1`.
  - `test_invocations_from_tool_and_slash`: yields one `Invocation("skill", "x", "tool")` and one with `via="slash"`.
  - `test_unknown_format_raises`: a file whose entries have none of the known types raises `TranscriptFormatError` and the message contains the file's `version` if present.
  - `test_truncated_last_line_is_skipped`: factory output with the final line cut in half still loads, with all earlier calls.
  - `test_project_and_since_filters`: two project dirs and two dates; filters return the expected session ids.
  - `test_skipped_lines_are_counted`: invalid JSON lines are counted in `Session.skipped_lines: int` (add this field to `Session`).
- [ ] **Step 4: Run `uv run pytest -v`**; expect FAIL (module missing).
- [ ] **Step 5: Implement `transcripts.py`** per the Interfaces. Stream each file line by line; skip unparseable lines; raise `TranscriptFormatError` only when a file has no entry of any type in the format reference. Link a `Call` to its shape via the nearest preceding `api-request` in the same session (confirm against a real transcript that `api-request` precedes its `assistant` entry; if not, adjust and note it in a code comment).
- [ ] **Step 6: Run `uv run pytest -v`**; expect all PASS. Run `uv run ruff format . && uv run ruff check . && uv run pyrefly check`.
- [ ] **Step 7: Commit.** `git add pyproject.toml uv.lock .claude/skills/context-audit docs/superpowers/specs/2026-11-23-context-audit-design.md docs/superpowers/plans/2026-11-23-context-audit.md` then `git commit -m "feat(context-audit): transcript parser and fixture factory"`.

---

### Task 2: Pricing

**Files:**
- Create: `.claude/skills/context-audit/scripts/pricing.py`
- Test: `.claude/skills/context-audit/tests/test_pricing.py`

**Interfaces:**
- Consumes: `Usage`, `Call` from Task 1.
- Produces:
  - `Rates(input: float, cache_write: float, cache_read: float, output: float)`: USD per million tokens.
  - `RATES: dict[str, Rates]`: keyed by model id as it appears in `Call.model`. Fill from Anthropic's current published pricing for the models seen in the user's transcripts (use the `claude-api` skill or the pricing page; do not guess), and add a comment with the date copied.
  - `Money(value: float, measured: bool)`
  - `usage_cost(usage: Usage, model: str, rates: dict[str, Rates] = RATES) -> Money | None`: `None` when the model has no entry; `measured=False` always (it is computed from rates).
  - `session_cost(session: Session, rates: dict[str, Rates] = RATES) -> Money | None`: returns `Money(session.cost_usd, True)` when recorded, else the sum of `usage_cost` over calls as estimated, else `None`.
  - `price_tokens(tokens: int, rate_per_mtok: float) -> float`

- [ ] **Step 1: Write failing tests**: `test_usage_cost_applies_each_rate` with an injected `Rates(1, 2, 0.5, 4)` and `Usage(1_000_000, 1_000_000, 1_000_000, 1_000_000)` asserting `7.5`; `test_unknown_model_returns_none`; `test_session_cost_prefers_recorded_cost_state` (measured `True`); `test_session_cost_falls_back_to_estimate` (measured `False`).
- [ ] **Step 2: Run tests**; expect FAIL.
- [ ] **Step 3: Implement `pricing.py`** per the Interfaces.
- [ ] **Step 4: Run tests**; expect PASS. Sanity check, manually: for one real session print `session_cost` measured vs the sum of `usage_cost`; they should agree within a few percent. If not, note the gap in a comment; do not hide it.
- [ ] **Step 5: Commit** `feat(context-audit): pricing from usage with cost-state preference`.

---

### Task 3: Repo scan and attribution

**Files:**
- Create: `.claude/skills/context-audit/scripts/attribute.py`
- Test: `.claude/skills/context-audit/tests/test_attribute.py`

**Interfaces:**
- Consumes: `Session`, `InstructionFile` from Task 1.
- Produces:
  - `Artifact(kind: Literal["agent", "skill", "rule", "agents_md"], name: str, path: Path, description_chars: int, body_chars: int)`. For agents and skills `description_chars` is the frontmatter `description` and `body_chars` the rest of the file. For rules and `AGENTS.md`, `description_chars = 0` and `body_chars` is the whole file.
  - `scan_repo(root: Path) -> list[Artifact]`: `.claude/agents/*.md`, `.claude/skills/*/SKILL.md`, `rules/*.md`, `AGENTS.md`.
  - `Loaded(artifact: Artifact, session_id: str, from_call: int, chars: int, measured: bool)`: `from_call` is the first call index that carried it; `chars` is its size as recorded.
  - `Unmatched(source: str, chars: int)`: a recorded block no artifact matched (`source` is `skill_listing`, `agent_listing`, `instructions:<basename>` or similar).
  - `match_session(artifacts: list[Artifact], session: Session) -> tuple[list[Loaded], list[Unmatched]]`
  - `tokens_from_chars(chars: int, ratio: float = CHARS_PER_TOKEN) -> int`
  - `calibrate_ratio(sessions: Iterable[Session]) -> float`: falls back to `CHARS_PER_TOKEN` when no session has a usable first call.

Matching rules: instructions by path suffix against `AGENTS.md` / `rules/<name>.md` (measured); agents by `addedTypes` name from `agent_added` (measured); skills by the line starting `- <name>:` inside a `skill_listing` (measured). An artifact whose recorded text no longer equals current repo text is still matched by name but `measured=True` applies to the recorded size, and the artifact is flagged by being listed in `Unmatched` only if its name is not found at all.

- [ ] **Step 1: Write failing tests** using the factory with synthetic text:
  - `test_scan_repo_finds_all_kinds` on a `tmp_path` repo with one of each.
  - `test_match_agent_from_listing_delta`, `test_match_skill_from_skill_listing`, `test_match_rule_and_agents_md_from_instructions`: each yields a `Loaded` with `measured=True` and the recorded char count.
  - `test_edited_artifact_not_found_by_name_is_unmatched`: a listing containing a name no longer in the repo yields an `Unmatched`, no exception, no guessed `Loaded`.
  - `test_from_call_uses_attachment_index`.
  - `test_calibrate_ratio_falls_back_to_default`.
- [ ] **Step 2: Run tests**; expect FAIL.
- [ ] **Step 3: Implement `attribute.py`**. Parse frontmatter with a minimal reader (no YAML dependency); a multi-line `description: >` block counts toward `description_chars`.
- [ ] **Step 4: Run tests**; expect PASS; ruff and pyrefly clean.
- [ ] **Step 5: Commit** `feat(context-audit): scan repo artifacts and match them to recorded context`.

---

### Task 4: Stats and verdicts

**Files:**
- Create: `.claude/skills/context-audit/scripts/report.py` (stats and verdict half)
- Test: `.claude/skills/context-audit/tests/test_stats.py`

**Interfaces:**
- Consumes: `Session`, `Artifact`, `Loaded`, `Unmatched`, `match_session`, `calibrate_ratio`, `tokens_from_chars`, `session_cost`, `price_tokens`, `Rates`.
- Produces:
  - `Verdict = Literal["keep", "trim", "move-to-on-demand", "insufficient data"]`
  - `ArtifactStats(artifact: Artifact, always_tokens: int, on_use_tokens: int, loaded_sessions: int, used_sessions: int, cost_window: Money | None, cost_per_session: Money | None, verdict: Verdict, reason: str)`
  - `OtherLoad(system_tokens: int, builtin_tool_tokens: int, mcp_tool_tokens: int, measured: bool)` (system prompt and tool definitions from request shapes; tools whose name starts with `mcp__` are counted as MCP).
  - `ToolResultStat(tool_name: str, total_chars: int, count: int, repeated: int)` where `repeated` counts results whose `content_hash` was seen before in the same session.
  - `build_stats(sessions: list[Session], artifacts: list[Artifact], rates: dict[str, Rates]) -> list[ArtifactStats]`
  - `other_load(sessions: list[Session]) -> OtherLoad`
  - `top_tool_results(sessions: list[Session], n: int) -> list[ToolResultStat]`
  - `decide_verdict(a: Artifact, loaded: int, used: int, always_tokens: int, on_use_tokens: int) -> tuple[Verdict, str]`
  - Constants `MIN_SESSIONS = 10`, `TRIM_DESCRIPTION_TOKENS`, `LOW_USE_RATE`; the builder picks starting values and every value used must appear in the report footer.

Cost formula for always-paid: tokens times (one cache-write rate for the call that introduced it, plus the cache-read rate for each later call in the session), priced at the model on each call. On-use cost is body tokens times invocations, priced at the input rate. Both estimated (`measured=False`) because they split measured totals.

- [ ] **Step 1: Write failing tests**:
  - `test_loaded_vs_used_counts_only_loaded_sessions`: 3 sessions load an agent, 1 invokes it, 2 sessions never load it; expect `loaded_sessions == 3`, `used_sessions == 1`.
  - `test_never_loaded_artifact_is_insufficient_data`: zero loaded sessions, no exception.
  - `test_fewer_than_min_sessions_is_insufficient_data`.
  - `test_always_on_file_never_gets_move_to_on_demand`: a large `AGENTS.md` gets `keep` or `trim` only.
  - `test_large_description_gets_trim` and `test_low_use_high_cost_agent_gets_move_to_on_demand` with 10+ synthetic sessions.
  - `test_slash_command_counts_as_skill_use`.
  - `test_other_load_splits_mcp_tools` and `test_top_tool_results_counts_repeats`.
  - `test_unknown_model_cost_is_none_not_zero`.
- [ ] **Step 2: Run tests**; expect FAIL.
- [ ] **Step 3: Implement** the stats half of `report.py` per the Interfaces and cost formula.
- [ ] **Step 4: Run tests**; expect PASS; ruff and pyrefly clean.
- [ ] **Step 5: Commit** `feat(context-audit): usage-weighted stats and verdict rules`.

---

### Task 5: Rendering and JSON

**Files:**
- Modify: `.claude/skills/context-audit/scripts/report.py` (render half)
- Test: `.claude/skills/context-audit/tests/test_render.py`

**Interfaces:**
- Consumes: Task 4 outputs.
- Produces:
  - `render_report(stats: list[ArtifactStats], other: OtherLoad, results: list[ToolResultStat], meta: ReportMeta, top: int | None) -> str`
  - `ReportMeta(session_count: int, first_day: date, last_day: date, claude_versions: list[str], tested_versions: tuple[str, ...], skipped_lines: int, unmatched: list[Unmatched])`
  - `render_json(stats: list[ArtifactStats], meta: ReportMeta) -> str`: stable keys `artifact`, `kind`, `path`, `always_tokens`, `on_use_tokens`, `loaded_sessions`, `used_sessions`, `cost_window`, `cost_per_session`, `verdict`, `reason`, `measured`.
  - `TESTED_VERSIONS: tuple[str, ...]` in `transcripts.py`: set to the Claude Code `version` strings seen in the user's transcripts during Task 1.

Report layout is the sketch in the spec (columns `always`, `on-use`, `used`, `$ window`, `$/session`, `verdict`), a "Not ours" line from `OtherLoad`, a "Largest tool results" section, an `(estimated)` marker on estimated figures, a line when `claude_versions` contains a version not in `TESTED_VERSIONS`, and a footer printing the verdict constants.

- [ ] **Step 1: Write failing tests**:
  - `test_render_never_contains_message_or_attachment_text`: build a session whose synthetic user message, listing text and instructions contain the sentinel `SECRET-SENTINEL-123`; assert it appears nowhere in `render_report` or `render_json` output.
  - `test_estimated_figures_are_marked`.
  - `test_footer_lists_verdict_constants`.
  - `test_untested_version_warning_shown`.
  - `test_render_json_has_stable_keys`.
  - `test_top_limits_rows`.
- [ ] **Step 2: Run tests**; expect FAIL.
- [ ] **Step 3: Implement** the render functions.
- [ ] **Step 4: Run tests**; expect PASS; ruff and pyrefly clean.
- [ ] **Step 5: Commit** `feat(context-audit): terminal report and JSON output`.

---

### Task 6: CLI and `/context-audit` skill

**Files:**
- Create: `.claude/skills/context-audit/scripts/audit.py`, `.claude/skills/context-audit/SKILL.md`
- Test: `.claude/skills/context-audit/tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv: Sequence[str] | None = None) -> int`. Flags: `--project NAME`, `--since YYYY-MM-DD`, `--top N`, `--json`, `--root PATH` (transcripts root, default `~/.claude/projects`), `--repo PATH` (default current directory). Exit codes: `0` ok (including "no sessions matched"), `2` on `TranscriptFormatError`. Reconfigures stdout to UTF-8 with `errors="replace"` at start of `main`.

- [ ] **Step 1: Write failing tests** (use `capsys` and the factory):
  - `test_no_matching_sessions_prints_message_and_exits_0`: nonexistent root and a `--project` that matches nothing both print `No sessions found` plus what was searched, no table.
  - `test_unknown_format_exits_2_without_report`.
  - `test_json_flag_outputs_valid_json`.
  - `test_report_survives_cp1252_stdout`: wrap stdout in a cp1252 `TextIOWrapper` with `errors="strict"`, include a non-ASCII artifact name, assert no exception.
  - `test_project_filter_and_since`.
- [ ] **Step 2: Run tests**; expect FAIL.
- [ ] **Step 3: Implement `audit.py`.** `if __name__ == "__main__": raise SystemExit(main())`.
- [ ] **Step 4: Write `SKILL.md`** with frontmatter `name: context-audit`, a `description` that says when to use it (token cost, "what is using my context", trimming agents/skills), and instructions: run `uv run python "<skill_dir>/scripts/audit.py"` and echo stdout verbatim; describe flags; state the privacy guarantee; state that figures marked `(estimated)` are splits of measured totals.
- [ ] **Step 5: Run `uv run pytest -v`**; expect all PASS. Then run the tool on real data: `uv run python .claude/skills/context-audit/scripts/audit.py --top 15`. Expected: a table that includes `python-backend-coder` and `AGENTS.md` rows with plausible token counts, no message text. Report anything implausible (zeros, missing artifacts) before committing; do not paper over it.
- [ ] **Step 6: Commit** `feat(context-audit): CLI and /context-audit skill`.

---

### Task 7: `/context-trim` skill and repo docs

**Files:**
- Create: `.claude/skills/context-trim/SKILL.md`
- Modify: `AGENTS.md` (the "What lives here" skills line), `README.md` (the "What you get" table and "Using the skills" list)

**Interfaces:**
- Consumes: `audit.py --json` output (keys listed in Task 5).

- [ ] **Step 1: Write `.claude/skills/context-trim/SKILL.md`**: `name: context-trim`; instructions: run `audit.py --json`; select entries with verdict `trim` or `move-to-on-demand`; for each, read the file and propose one concrete edit as a diff (for example shorten `<example>` blocks inside an agent `description`, keeping the trigger conditions); apply an edit only after the user approves that specific edit; never edit always-on files (`AGENTS.md`, rules) except text trims the user approves; mention structural moves without applying them; skip entries marked `insufficient data`; re-run the audit afterwards to show the before/after token delta.
- [ ] **Step 2: Update `AGENTS.md` and `README.md`** to list `context-audit` and `context-trim` and say they read `~/.claude/projects` transcripts, read-only.
- [ ] **Step 3: Verify.** Run `/context-audit` in a fresh Claude Code session in this repo; confirm the skill is discovered and prints a report. Run `/context-trim` on one `trim` entry and decline the edit; confirm nothing changed (`git status` shows only your own prior changes).
- [ ] **Step 4: Commit** `docs(context-audit): document context-audit and context-trim skills`.

---

## Self-review

- **Spec coverage:** transcripts source (T1), pricing with cost-state preference (T2), attribution from attachments (T3), always-paid vs on-use, used a/b over loaded sessions, per-class verdicts, `$` columns, not-ours block, tool results (T4), privacy, estimated labels, version warning, JSON (T5), CLI filters, loud failure, Windows (T6), `/context-trim` with per-edit approval and no structural auto-moves (T7). Test strategy with synthetic fixtures and unknown-format test: T1, T5.
- **Types:** `Session`, `Call`, `Usage`, `Artifact`, `Loaded`, `Unmatched`, `ArtifactStats`, `Money`, `Rates` have one definition each and later tasks consume them by those names.
- **Left to the builder (per spec):** the pricing table values, verdict thresholds, exact tokens-times-requests derivation, matching robustness.
