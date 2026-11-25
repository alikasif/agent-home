# Agent Home

Single source of truth for my agents, skills, rules and shared standards. Run agents from this repo. Based on Matt Pocock's Home Repo pattern: https://www.aihero.dev/the-home-repo-pattern
`CLAUDE.md` points to this file and holds the reporting style. It is auto-loaded; this file is read on demand, so anything that must always apply goes in `CLAUDE.md`.

## Read first
- Coding standards (all repos): `docs/coding-standards.md`
- Agent team and orchestration workflow: `docs/orchestration.md`
- Global rules: `rules/` (install to `~/.claude/rules` with `scripts/install-rules.ps1`)

## Fork setup
1. Clone this repo and open it in Claude Code. Trust the folder when asked.
2. Accept the prompt to install the plugins listed in `.claude/settings.json` (`superpowers`, `mattpocock-skills`, both from `claude-plugins-official`).
3. Install the global rules: `scripts/install-rules.ps1` (Windows). On macOS/Linux copy `rules/*.md` to `~/.claude/rules/`.
4. Agents, skills and `pr-reviewer` permissions come from `.claude/` in this repo and need no further setup. `pr-reviewer` needs `gh auth login` and `uv`.

## Skill precedence
`superpowers` and `mattpocock-skills` overlap. When both could apply, use:
- Process and workflow (brainstorming, planning, executing plans, verification before completion, worktrees): `superpowers`.
- Bug diagnosis: `superpowers:systematic-debugging` first; use `mattpocock-skills:diagnosing-bugs` for performance regressions or hard-to-reproduce bugs.
- TDD: `superpowers:test-driven-development` by default; `mattpocock-skills:tdd` only if the user asks for red-green-refactor with integration tests.
- Code review: `superpowers:requesting-code-review` while building; `mattpocock-skills:code-review` for a standards+spec review of a finished branch.
- Stress-testing a plan (`grilling`), domain modeling, PR bodies and prototypes: `mattpocock-skills` (no overlap).
Explicit user instructions override this table.

Plugin versions are not pinned. Last tested with `mattpocock-skills` 1.3.1; if a plugin update changes behavior, check this section first.

## What lives here
- `.claude/agents/`: specialist agents (orchestrator, PRD, design, planning, ralph, coders, reviewers, docs)
- `.claude/skills/`: shared skills (model-router, pr-reviewer, token-budget, token-dashboard, secrets-detection, context-audit, context-trim). `context-audit` and `context-trim` estimate and trim this repo's context cost; see the README.
- Reporting style: canonical text is in `CLAUDE.md`. Each agent body and user-facing skill (`context-audit`, `context-trim`, `model-router`, `secrets-detection`, `token-budget`) carries a copy under `## Reporting style`; keep them in sync.
- `.claude/agent-memory/`: agent working-style memory shared across projects. Project-specific memory stays in the repo it is about.

## Other Repos (author's related projects, optional)
Paths are siblings of this repo (`../<name>`); clone them next to `agent-home` if you want them.
- agent-foundry (monorepo of agentic systems: harness-builder, skillful_agent, etc.): ../agent-foundry. Clone from https://github.com/alikasif/agent-foundry if missing. Read its docs/agents/ first.
- agents (collection of agent experiments and frameworks): ../agents. Clone from https://github.com/alikasif/agents if missing.
- bookmarks-organizer (Edge extension + Python backend that reorganizes bookmarks): ../bookmarks-organizer. Clone from https://github.com/alikasif/agentic-bookmark-organizer if missing.
- eda-agent (AI exploratory data analysis tool): ../eda-agent. Clone from https://github.com/alikasif/eda-agent if missing.
- progressive-context-disclosure (progressive context disclosure experiments): ../progressive-context-disclosure. Clone from https://github.com/alikasif/progressive-context-disclosure if missing.
- agent_sdk (async-first Python Agent SDK): ../agent_sdk. Clone from https://github.com/alikasif/agent_sdk if missing. Read its ARCHITECTURE.md first.
- conversational-survey-engine (adaptive AI survey platform): ../conversational-survey-engine. Clone from https://github.com/alikasif/conversational-survey-engine if missing.
- software_team (multi-agent software team workflow): ../software_team. Clone from https://github.com/alikasif/software_team if missing.
