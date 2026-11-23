# Context Audit: design

Status: APPROVED and implemented on branch feat/context-audit.
Inspired by: ContextSpy (https://github.com/RimantasZ/contextspy), a proxy-based context profiler. This design is deliberately different: no proxy, and it attributes cost to the files in this repo.

## Intent and why
The owner cares about **cost and visibility** of what Claude Code loads into context. `token-dashboard` shows how many tokens a session used, but not what they were spent on. ContextSpy shows request composition but needs a MITM proxy and CA certificate, and cannot tell you which file in your repo a block of tokens came from.

`agent-home` is itself a context source: every session pays for its agent descriptions, skill listings, rules and `AGENTS.md`. Nothing today says which of those files cost the most or whether they earn their place.

Key discovery (verified on a local transcript, Claude Code transcripts under `~/.claude/projects/<project>/<session>.jsonl`): the transcript already records
- `api-request-shape`: the full `system` blocks and full `tools` definitions of each request (`shape.system[].text`, `shape.tools[].{name,description,input_schema}`), keyed by `shapeHash`;
- `api-request`: per-call metadata (model, `shapeHash`, timestamp);
- assistant `message.usage`: real `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`;
- `cost-state`: real `totalCostUSD` and per-model usage;
- `tool_use` entries for `Skill` (`input.skill`) and `Agent` (`input.subagent_type`), which show what was actually invoked.

Correction found in the builder check and verified: the repo's own artifacts do **not** appear in `api-request-shape`. That shape holds only the built-in system blocks and tool definitions (none of 12 sampled shapes mention `AGENTS.md`, `python-backend-coder` or `model-router`). The repo's artifacts arrive as `attachment` entries (types include `instructions`, `skill_listing`, `agent_listing_delta`, `hook_additional_context`) and inside `api-request-blob` message content. Listings can be sent as deltas rather than on every request.

So we can measure what was actually loaded, with no proxy, but the authoritative source differs by artifact class: built-in system prompt and tool definitions come from `api-request-shape`; agent and skill listings, rules and `AGENTS.md` come from attachments and blobs. Reading attachment content is allowed; it is never printed.

## Goals
- `/context-audit`: a terminal report that, for every agent, skill, rule and `AGENTS.md` in this repo, shows
  1. tokens it adds to every session (always-paid: descriptions in the system prompt / tool list, rules, AGENTS.md),
  2. tokens it adds when invoked (paid-on-use: agent and skill bodies),
  3. how often it was actually used across sessions,
  4. estimated dollar cost, using real cache-read / cache-write / input rates for the model used,
  5. a verdict: keep, trim, or move to on-demand.
- Totals come from real recorded usage and recorded system/tool text. A chars/4 style estimate is used only to split a measured total across individual files.
- Also report what is not ours: tool definitions and system prompt size per request (visibility), and large or repeated tool results.
- `/context-trim`: takes the audit's trim suggestions and proposes concrete edits to agent/skill files (for example, shorten `<example>` blocks in descriptions). The user approves each edit; nothing is written without approval.

## Non-goals
- No proxy, no network capture, no CA certificate.
- No dashboard web server, no live monitoring. Terminal report only (like `token-dashboard`).
- Not a general analytics product and not a ContextSpy clone.
- Does not modify transcripts or any file outside the skill's own folder, other than edits to repo files that the user approves via `/context-trim`.

## Anti-goals (failure even if it "works")
- Presenting estimates as measurements. Every number is labeled measured or estimated.
- Printing message content. The report shows names, sizes and counts only. No prompt or response text, and no file contents from other projects.
- A silent wrong report after Claude Code changes its transcript format. On an unrecognised format the tool must say so and stop, not output plausible-looking zeros.
- Verdicts that push the user to delete something useful on thin data (for example, "unused" from only a handful of sessions). Show the sample size and mark low-confidence verdicts.

## Constraints
- Python 3.13, run with `uv`, standard library only, per `docs/coding-standards.md`.
- Lives in `.claude/skills/context-audit/` and `.claude/skills/context-trim/` in this repo, with `SKILL.md` files in the existing style (see `token-dashboard`).
- Transcript format is not a stable API. All parsing lives in one module; the tool records the Claude Code `version` it saw (each entry carries `version`) and the version range it was tested against.
- Works on Windows (primary platform) and does not assume a POSIX shell.
- Reads transcripts read-only.

## Approaches considered
- **A: static audit only.** Scan the repo, estimate tokens per artifact, split always-paid vs paid-on-use. Cheap, works with no data, but shows only the tax, not whether it is worth paying.
- **B (chosen): usage-weighted audit.** A plus transcripts: real loaded totals, usage counts, dollar cost. Answers "agent X costs N tokens in every session and ran in 2 of 80". The owner chose this.
- **C: proxy like ContextSpy.** Duplicates ContextSpy, needs a CA certificate, cannot map tokens to repo files. Rejected.

## Design
Skill layout:
```
.claude/skills/context-audit/
  SKILL.md
  scripts/
    transcripts.py   # the only module that knows the transcript format; version check
    attribute.py     # match recorded system/tool text back to repo files
    pricing.py       # per-model rates; cost from usage fields; falls back to cost-state
    report.py        # terminal report, verdicts, confidence
    audit.py         # CLI entry: --project, --since, --top N
.claude/skills/context-trim/
  SKILL.md           # drives proposing edits from the audit's output
```

Data flow for `/context-audit`:
1. `transcripts.py` walks `~/.claude/projects/*/*.jsonl` (all projects by default; `--project` filters), yields typed records: request shapes, request metadata, usage, invocations, cost state. Unknown or missing structure raises a clear error naming the Claude Code version.
2. `attribute.py` takes the repo's own artifacts (`.claude/agents/*.md`, `.claude/skills/*/SKILL.md`, `rules/*.md`, `AGENTS.md`) and matches them to text found in attachment entries (`instructions`, `skill_listing`, `agent_listing_delta`, `hook_additional_context`) and `api-request-blob` content. Built-in system prompt and tool definitions come from `api-request-shape`. Unmatched large blocks are reported as "other" (built-in tools, plugin skills, MCP). Matching uses the current repo text; a block that matches nothing current is reported as unmatched, not guessed.
3. Always-paid cost per artifact = its attributed tokens times the number of requests that carried it, priced at the cache-read rate (and the cache-write rate where the usage fields show a cache miss). Because listings may arrive as deltas, "carried" means present in the conversation prefix from the point it was introduced, not "present in every request"; the builder works out the exact derivation and labels any figure it cannot measure as estimated.
4. Paid-on-use cost = body size times number of invocations (from `Skill` and `Agent` tool_use entries, plus user-typed slash commands recorded as `<command-name>` in user messages).
5. `report.py` ranks by total cost, prints per-artifact line plus verdict. Verdict rules are simple and visible in the report footer. Fewer than a threshold number of sessions means "insufficient data".

Denominator for "used a/b": b is the number of sessions in which the artifact was actually loaded (visible in the attachments), not all sessions. Sessions where it was never loaded are not evidence either way.

Verdicts by artifact class:
- **Agents and skills** (description always-paid, body paid-on-use): keep, trim (shorten description or `<example>` blocks), or move-to-on-demand (low use relative to always-paid cost).
- **Always-on files** (`AGENTS.md`, rules): "used" is not measurable, so there is no use-rate rule. Verdicts are only keep or trim, driven by size. A structural move (for example turning a rule into a skill) may be mentioned in the report as a suggestion but is never proposed as an automatic edit.

`$` columns: the report shows `$ in window` (total for the scanned sessions) and `$/session` (average). No monthly extrapolation.

Report sketch:
```
context-audit  (47 sessions, 2026-10-01..2026-11-23, Claude Code 2.x tested)
artifact                     always  on-use  used   $ window  $/session  verdict
python-backend-coder.md       639    7084    6/47     1.90     0.04     trim examples
AGENTS.md                     960      -       -      2.80     0.06     keep
...
Not ours: system prompt 9.1k, built-in tool defs 12.1k, MCP/plugin 3.4k
```

`/context-trim` reads the audit output (JSON via `audit.py --json`), picks entries with a trim verdict, proposes a specific edit per file as a diff, and applies only on approval.

## Decisions by the owner
- Offer this as skills from the home repo (`.claude/skills/`).
- Cost and visibility are the goals.
- Approach B (usage-weighted), terminal report, no proxy.
- A second skill, `/context-trim`, applies suggestions with per-edit approval.

## Calls I made (to be checked)
- Reads all projects by default, with a `--project` filter.
- Standard library only, no third-party tokenizer. Attribution uses a chars-to-tokens ratio calibrated against measured totals where possible.
- Terminal output only; `--json` for `/context-trim`.
- Verdict rules are heuristic and shown in the report.
- (From builder check) "used a/b" counts only sessions where the artifact was loaded.
- (From builder check) Always-on files get only keep/trim verdicts; structural moves are mentioned, never auto-applied by `/context-trim`.
- (From builder check) A user-typed slash command counts as a skill invocation.
- (From builder check) Dollar columns are `$ in window` and `$/session`, not `$/mo`.
- (From builder check) The design originally said repo artifacts appear in the system prompt; that was wrong and is corrected above.

## Left to the builder
- Exact pricing table and how to keep it current (rates by model are needed; `cost-state` gives real totals per model to sanity-check against).
- How to match recorded text back to files robustly (name/description match versus fuzzy match).
- Thresholds for "insufficient data" and for each verdict.
- Test strategy. Include fixture transcripts with synthetic content (never real conversation content) and a test that an unknown format fails loudly.
- Whether to also feed the `token-dashboard` database. Not required.
