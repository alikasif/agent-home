# Agent Home

Single source of truth for my agents, skills, rules and shared standards. Run agents from this repo.
`CLAUDE.md` is a one-line pointer to this file.

## Read first
- Coding standards (all repos): `docs/coding-standards.md`
- Agent team and orchestration workflow: `docs/orchestration.md`
- Global rules: `rules/` (install to `~/.claude/rules` with `scripts/install-rules.ps1`)

## What lives here
- `.claude/agents/`: specialist agents (orchestrator, PRD, design, planning, ralph, coders, reviewers, docs)
- `.claude/skills/`: shared skills (model-router, pr-reviewer, token-budget, token-dashboard, secrets-detection)
- `.claude/agent-memory/`: agent working-style memory shared across projects. Project-specific memory stays in the repo it is about.

## Other Repos
- agent-foundry (monorepo of agentic systems: harness-builder, skillful_agent, etc.): D:\GitHub\agent-foundry. Clone from https://github.com/alikasif/agent-foundry if missing. Read its docs/agents/ first.
- agents (collection of agent experiments and frameworks): D:\GitHub\agents. Clone from https://github.com/alikasif/agents if missing.
- bookmarks-organizer (Edge extension + Python backend that reorganizes bookmarks): D:\GitHub\bookmarks-organizer. Clone from https://github.com/alikasif/agentic-bookmark-organizer if missing.
- eda-agent (AI exploratory data analysis tool): D:\GitHub\eda-agent. Clone from https://github.com/alikasif/eda-agent if missing.
- progressive-context-disclosure (progressive context disclosure experiments): D:\GitHub\progressive-context-disclosure. Clone from https://github.com/alikasif/progressive-context-disclosure if missing.
- agent_sdk (async-first Python Agent SDK): D:\GitHub\agent_sdk. Clone from https://github.com/alikasif/agent_sdk if missing. Read its ARCHITECTURE.md first.
- conversational-survey-engine (adaptive AI survey platform): D:\GitHub\conversational-survey-engine. Clone from https://github.com/alikasif/conversational-survey-engine if missing.
- software_team (multi-agent software team workflow): D:\GitHub\software_team. Clone from https://github.com/alikasif/software_team if missing.
