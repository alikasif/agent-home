---
name: review-workflow-harness-builder
description: How to review harness-builder tasks reliably while other agents edit the tree concurrently; Windows probing tricks
metadata:
  type: feedback
---

When other agents are editing the working tree, review a clean snapshot of HEAD.
`git archive HEAD harness-builder codeact_agent skillful_agent | tar -x -C <scratchpad>/head`
works because pyproject has relative path deps (`../codeact_agent`, `../skillful_agent`).
Then run `uv sync --frozen --offline` and ruff, pyrefly and pytest there. Never extract harness-lab.

**Why:** In the T022 review, `cli/main.py` changed under me between two reads, and HEAD
moved twice. Line numbers from the working copy did not match HEAD.

**How to apply:**
- Cite line numbers from `git show HEAD:<file>`.
- To test path-escape on Windows, use `mklink /J` (a directory junction, which needs no
  privilege). `os.symlink` fails with WinError 1314.
- The lead says not to edit task_list.json, because RALPH sets status. Report which tasks
  need `review_feedback` in the handoff instead.
- Commit trailer: follow the harness attribution reminder, not an agent message's model
  name. See [[review-severity-path-escape]].
