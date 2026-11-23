---
name: context-trim
description: >
  Proposes concrete text edits that cut the context cost of this repo's agents,
  skills, rules and AGENTS.md, based on the verdicts from /context-audit, and
  applies an edit only after the user approves that specific edit. Use after an
  audit, or when the user asks to trim agent, skill or rule descriptions or
  reduce context cost.
---

# Context Trim Skill

Use this skill when the user invokes `/context-trim`, or asks to act on a
`/context-audit` result: trim agent, skill or rule descriptions, or reduce what
this repo costs in context.

## Hard rules

- **Nothing is written without per-edit approval.** Show one edit as a diff, then
  stop and ask. Do not batch-apply, do not apply "the rest" on a blanket yes, and
  do not edit anything while the user has not answered. Approval of one edit is
  not approval of the next.
- **Never apply structural moves.** Turning a rule or an `AGENTS.md` section into
  a skill, moving content out of a description into the file body, merging or
  deleting a file: mention these as suggestions only, in words, with no diff and
  no edit.
- **Always-on files** (`AGENTS.md` and `rules/*.md`) are only eligible for a text
  trim the user approves. They never get a structural change.
- **Skip `insufficient data`.** It is not a verdict. Say how many entries were
  skipped and why (fewer than `MIN_SESSIONS` loaded sessions), and leave them
  alone. Entries with verdict `keep` are also left alone.

## Steps

1. Run the audit from the repo root and read the JSON:

   ```
   uv run python "<context-audit skill_dir>/scripts/audit.py" --json
   ```

   Replace `<context-audit skill_dir>` with the absolute path to the
   `context-audit` skill directory. From the agent-home repo root that is
   `.claude/skills/context-audit`. The output is `{"meta": {...}, "artifacts":
   [...]}`; read the list from `data["artifacts"]`. Each entry has `artifact`,
   `kind` (`agent`, `skill`, `rule` or `agents_md`), `path` (relative to the repo,
   forward slashes), `always_tokens`, `on_use_tokens`, `loaded_sessions`,
   `used_sessions`, `cost_window`, `cost_per_session`, `verdict` (`keep`, `trim`,
   `move-to-on-demand` or `insufficient data`), `reason` and `measured`.
   `--project NAME` and `--since YYYY-MM-DD` are accepted as in `/context-audit`.
   Other projects' same-named files (`AGENTS.md`, `rules/<name>.md`) are counted
   as this repo's, which can inflate `loaded_sessions` past `MIN_SESSIONS` and
   produce a `trim` verdict from other projects' data. When that could matter,
   pass `--project NAME`, where NAME is this repo's folder name under
   `~/.claude/projects`, before proposing trims (see "Reading the result
   honestly" in the `context-audit` skill).
   Exit code `2` means an unrecognised transcript format: relay stderr and stop.
   If the output says "No sessions found", relay that and stop.

2. Select entries whose `verdict` is `trim` or `move-to-on-demand`. Tell the user
   the list (artifact, kind, `always_tokens`, `reason`), and which entries you
   skipped as `insufficient data`.

3. Work through the selected entries one at a time. For each: read the file at
   `path`, then propose ONE concrete edit as a diff. Prefer edits that keep the
   behaviour and cut the words, for example:
   - shorten or drop `<example>` blocks inside an agent `description`, keeping
     the trigger conditions that tell Claude when to use it
   - shorten a long skill `description` to the sentence that says when to use it
   - tighten repeated or explanatory prose in a rule or `AGENTS.md`

   For an agent or skill, the always-paid cost is its frontmatter `description`
   plus the name; the body is paid only when it is invoked. For a `rule` or
   `agents_md` entry the whole file is always paid.

4. Show the diff with the estimated saving (see "Estimating the saving"), say
   what behaviour could change (for example "agent may trigger less often"), then
   **stop and ask** whether to apply this edit. Apply it with the Edit tool only
   on a clear yes. On a no, or no answer, leave the file untouched and move on or
   stop, as the user prefers.

5. For a `move-to-on-demand` entry, or any edit that would restructure content,
   describe the move as a suggestion (for example "this rule could become a skill
   that loads only when needed") and do not apply it. If a text trim of the same
   file is also useful, propose that as a normal edit.

6. After the session of edits, re-run `audit.py --json` and report the result as
   described below. If nothing was applied, say so and skip this step.

## Estimating the saving

Do not present the re-run audit as proof of the saving. In `audit.py`, when a
session loaded an artifact, `always_tokens` is the mean of the sizes recorded in
those past transcripts. Only an artifact that no session loaded falls back to the
current file size. So:

- Edits do not show up in `always_tokens` until new sessions load the edited
  file, and even then the figure is averaged with the older sessions in the scan
  window. A narrower `--since` after the edit date gives a cleaner comparison
  once enough new sessions exist.
- An edited file no longer matches the text old transcripts recorded, so the old
  sessions may report its block as unmatched instead of counting it. Expect that,
  and do not read it as a regression.
- The before/after delta you show is therefore **estimated from file size**:
  compare the characters of the edited text before and after (the `description`
  for agents and skills, the whole file for rules and `AGENTS.md`), divide the
  difference by `CHARS_PER_TOKEN = 4.0`, and label it `(estimated)`. Report the
  before `always_tokens` from the first audit and the estimated after
  (`always_tokens` minus the estimated saving). Say that the re-run audit still
  reflects past sessions, and that new sessions will show the real change.

## Privacy

Show diffs of repo files the user already owns. Do not print transcript or
attachment text; the audit output is names, sizes and counts only.

## Examples

- **User**: `/context-trim`
  - Run `audit.py --json`, list the `trim` and `move-to-on-demand` entries, take
    the first, show a diff of one edit, and ask before applying it

- **User**: "Trim the agent descriptions the audit flagged"
  - Same steps, restricted to entries with `kind` `agent`; one diff at a time,
    asking after each

- **User**: "Just apply all the trims"
  - Still show each diff and ask per edit; a blanket yes does not apply the whole
    list. Offer to go through them one after another quickly
