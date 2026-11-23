# agent-home

A reusable home for Claude Code agents, skills, rules and shared standards. Fork it, run Claude Code from the repo root, and you get a multi-agent software team plus a few utility skills.

This repo is an implementation of the **Home Repo pattern** described by Matt Pocock in [The Home Repo Pattern](https://www.aihero.dev/the-home-repo-pattern) (AI Hero). See [Credits](#credits).

## What you get

| Path | Contents |
|---|---|
| `.claude/agents/` | Specialist agents: orchestrator, product requirements, design, planning, execution (`ralph`), Python coder, test runner, reviewers, docs writer |
| `.claude/skills/` | `model-router`, `pr-reviewer`, `token-budget`, `token-dashboard`, `secrets-detection`, `context-audit`, `context-trim` |
| `.claude/agent-memory/` | Working-style memory shared by the agents |
| `.claude/settings.json` | Enables the `superpowers` and `mattpocock-skills` plugins and pre-allows the `pr-reviewer` commands |
| `rules/` | Global rules, installed to `~/.claude/rules` |
| `docs/` | `coding-standards.md` and `orchestration.md` |

## Quick start

1. Clone the repo and open it in Claude Code. Trust the folder when asked.
2. Accept the prompt to install the plugins (`superpowers`, `mattpocock-skills`).
3. Install the global rules:
   - Windows: `scripts/install-rules.ps1`
   - macOS/Linux: copy `rules/*.md` to `~/.claude/rules/`
4. Ask for work, for example: *"Build X using the software-team-orchestrator."*

## Requirements

- [Claude Code](https://claude.com/claude-code)
- [`uv`](https://docs.astral.sh/uv/) for Python tooling (the repo uses `uv`, never `pip`)
- [`gh`](https://cli.github.com/), authenticated with `gh auth login`, for `pr-reviewer`
- Node.js, only if you use the Context7 rule (`npx ctx7`)

## How the agent team works

`software-team-orchestrator` runs four phases: requirements, design, planning, then execution. You approve the PRD and the plan. Execution then dispatches the coders, test runner, reviewers and docs writer in parallel. Coordination files go in `<project>/shared/`, and agents make local commits only. Details are in [`docs/orchestration.md`](docs/orchestration.md).

## Using the skills

- `/pr-reviewer <handle[/repo]>`, usually as `/loop 5m /pr-reviewer <handle>`: reviews open PRs once per head SHA.
- `/token-budget` and `/token-dashboard`: set a token budget and view usage history. These rely on hooks that are **not** included in this repo (a `Stop` hook that records usage to `~/.claude/token_usage.db`, and a hook that injects budget status). Wire them up in your `~/.claude/settings.json` using the scripts in each skill's `scripts/` folder, or the skills will have no data.
- `/context-audit` and `/context-trim`: show which agents, skills, rules and `AGENTS.md` cost the most context and give each a keep, trim or move-to-on-demand verdict, then propose trims one edit at a time (nothing is applied without your approval). They read the session transcripts in `~/.claude/projects` (read-only), including subagent transcripts, and need no hooks, unlike `/token-budget` and `/token-dashboard`. The skills run the script with `uv run --no-dev python .claude/skills/context-audit/scripts/audit.py`, so the first run does not install the dev tools. Token and dollar figures are estimated from recorded sizes at 4.0 chars per token and list prices; not derived from billing totals. The transcript format is not a stable API: they are tested against Claude Code 2.1.201, 2.1.325, 2.1.326 and 2.1.330 (`TESTED_VERSIONS` in `.claude/skills/context-audit/scripts/transcripts.py`), and warn on other versions.
- `/secrets-detection`: scan for leaked credentials.
- `/model-router`: pick the right model for planning, coding or delegating.

## Customizing

- Add or change an agent in `.claude/agents/<name>.md`, keep its `model:` line consistent with `model-router`, and update `docs/orchestration.md`.
- When `superpowers` and `mattpocock-skills` overlap, `AGENTS.md` says which wins.
- Edit `rules/` and re-run the installer to change your global rules.

## Credits

- **The Home Repo pattern** is Matt Pocock's idea, from [The Home Repo Pattern](https://www.aihero.dev/the-home-repo-pattern) on [AI Hero](https://www.aihero.dev). In short: one repo holds your skills, `AGENTS.md` and shared docs, you start your agent sessions there, and `AGENTS.md` points to your other repos so one agent can work across them. This repo follows that structure (`AGENTS.md` pointers, shared standards in `docs/`, shared skills in `.claude/skills/`). The agents, orchestration workflow and token tooling here are my own additions.
- **[`mattpocock-skills`](https://github.com/mattpocock/skills)** by Matt Pocock, installed as a plugin.
- **[`superpowers`](https://github.com/obra/superpowers)** by Jesse Vincent, installed as a plugin.

Neither plugin's code is copied into this repo. They are installed from their upstream sources.

## More

- [`AGENTS.md`](AGENTS.md): instructions the agents read (fork setup, skill precedence, related repos)
- [`docs/coding-standards.md`](docs/coding-standards.md)
- [`docs/orchestration.md`](docs/orchestration.md)

## License

[MIT](LICENSE). The `superpowers` and `mattpocock-skills` plugins are separate projects with their own licenses.
