---
name: task-list-conventions
description: Gotchas when writing task_list.json for ralph in agent-foundry (status value mismatch, shared-file chaining, deps only in scaffold)
metadata:
  type: feedback
---

Ralph's agent definition classifies "ready" as status `not_started`, while the mandated task schema uses `pending`. Always keep `pending` and add an execution note in plan.md (and the handback) telling ralph to treat `pending` as `not_started`.

**Why:** a mismatch would leave every task un-dispatched; surfaced during harness-builder planning (2026-11-16).

**How to apply:**
- Add all deps (runtime, dev, path sources) in the single project_structure task so parallel coders never race on pyproject/uv.lock.
- Chain every task that edits a shared composition file (builder, cli main, spec models, README) via blocked_by.
- Generate task_list.json with a scratchpad script that asserts schema, unique ids, existing deps, acyclicity and FR coverage.
- Docker/live-dependent test tasks must be completable via "skip with reason, recorded in notes"; make an arch gate report whether they actually ran.
- Never name the excluded remote-push agent, even in prohibitions; say "local commits only, no push".
