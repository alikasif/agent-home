---
name: context-audit
description: >
  Attributes Claude Code context cost to this repo's agents, skills, rules and
  AGENTS.md by reading the session transcripts under ~/.claude/projects, and
  gives each a keep / trim / move-to-on-demand verdict. Use when the user asks
  about token cost, "what is using my context", why sessions are expensive, or
  which agents and skills to trim.
---

# Context Audit Skill

Use this skill when the user invokes `/context-audit` or asks what is using
their context window, what their agents and skills cost, or which ones are worth
trimming.

## How it works

Claude Code records, per session, which agent and skill listings and which
instruction files (`AGENTS.md`, rules) it loaded, plus per-call token usage.
`audit.py` reads those transcripts (read-only), matches the recorded blocks to
this repo's files by name, and reports for each file:

- `always`: tokens paid by every session that loads it
- `on-use`: tokens paid each time an agent or skill is actually invoked
- `used`: invoked in `a` of the `b` sessions that loaded it
- `$ window` and `$/session`: estimated dollars over the scanned window and per
  loaded session
- `verdict`: `keep`, `trim`, `move-to-on-demand` or `insufficient data`, with
  the reason listed under `Why:`

Below the table it shows context that is not this repo's (system prompt, built-in
and MCP tools, other skills and agents), the largest tool results, and the
verdict thresholds. Always-on files (`AGENTS.md`, rules) only get `keep` or
`trim`.

## Running the audit

Run `audit.py` from the repo root and echo its stdout **verbatim**; do not
paraphrase or summarise the output.

```
uv run --no-dev python "<skill_dir>/scripts/audit.py"
```

`--no-dev` keeps the first run from installing the repo's dev tools (pytest,
ruff, pyrefly); the script needs only the standard library.

Replace `<skill_dir>` with the absolute path to this skill directory (the folder
containing this `SKILL.md`). From the agent-home repo root that is
`.claude/skills/context-audit`.

Flags (all optional):

- `--project NAME`: only project directories under `~/.claude/projects` whose
  name contains `NAME` (case-insensitive), for example `--project agent-home`
- `--since YYYY-MM-DD`: only sessions that started on or after that UTC date
- `--top N`: show only the `N` costliest artifacts in the table
- `--json`: print JSON instead of the report, for `/context-trim`. The shape is
  `{"meta": {...}, "artifacts": [...]}`; read the list from `data["artifacts"]`.
  Artifact `path` values are relative to the repo with forward slashes. `--top`
  does not apply to JSON.
- `--root PATH`: transcripts root (default `~/.claude/projects`)
- `--repo PATH`: repo to audit (default: current directory)

Exit code `0` is success, including "No sessions found" (relay that message and
suggest loosening `--project` or `--since`). Exit code `2` with
`unrecognised transcript format` on stderr means a transcript is in a format
this tool does not recognise; stderr names the Claude Code version. Relay it and
stop: do not guess at figures. Any other exit `2` is a usage error from the
argument parser, for example a mistyped `--since` date: fix the command and do
not report it as a transcript format change.

## Reading the result honestly

- Figures marked `(estimated)` are estimated from recorded sizes at 4.0 chars
  per token and list prices; not derived from billing totals. Tokens are
  recorded characters divided by `CHARS_PER_TOKEN = 4.0`; dollars apply the
  per-model rates in `pricing.py` call by call. They rank files and size them;
  they are not billing numbers.
- Dollar figures assume the cache stays warm; cache misses are not counted, so
  they are understated.
- `used` counts invocations anywhere in a session, including agents and skills
  run inside a subagent (read from the session's subagent transcripts). On-use
  dollars cover the dispatch only, not the calls the subagent makes, so they are
  understated for agents that run long.
- The `MCP/plugin` figure in `Not ours` is understated (it can read about 1):
  deferred MCP tools are not in the recorded request shape.
- A `Warning:` line means a session came from a Claude Code version the parser
  was not tested against; say so when summarising.
- Files are matched to transcripts by name or path suffix. If other projects
  under `~/.claude/projects` have same-named files (another project's
  `AGENTS.md` or `rules/<name>.md`), they are counted as this repo's. Tell the
  user to pass `--project <name>` (for example `--project agent-home`) to limit
  the scan to this repo's sessions when that matters.
- Agents and skills only load in sessions started in this repo, so a
  repo-wide scan understates them relative to sessions in other projects.
- `AGENTS.md` in this repo is not auto-loaded: `CLAUDE.md` only points to it and
  Claude reads it on demand, so it normally reports `insufficient data`. In a
  fork whose `CLAUDE.md` imports it, it is matched like any other file.
- `insufficient data` means fewer than `MIN_SESSIONS` loaded sessions; it is not
  a verdict.

## Privacy

The report contains names, sizes and counts only. It never prints message text,
attachment text or file content, and it does not modify transcripts or repo
files.

## Examples

- **User**: `/context-audit`
  - Run `audit.py` and paste its output verbatim

- **User**: "What is eating my context?"
  - Run `audit.py`, paste the output, then point to the largest rows and the
    `Not ours` line

- **User**: "Audit only this repo's sessions since last week"
  - Run `audit.py --project agent-home --since YYYY-MM-DD` with the real date
