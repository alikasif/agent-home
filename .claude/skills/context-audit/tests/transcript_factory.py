"""Synthetic Claude Code transcript builder for tests.

Emits JSONL entries in the shape Claude Code writes under
``~/.claude/projects/<project>/<session>.jsonl``. Every string is synthetic;
no real transcript content is ever copied here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Self

DEFAULT_START = "2026-11-01T10:00:00+00:00"
DEFAULT_MODEL = "claude-test-model"


class TranscriptBuilder:
    """Chainable builder for one synthetic session transcript."""

    def __init__(
        self,
        session_id: str = "sess-1",
        version: str | None = "2.1.330",
        start: str = DEFAULT_START,
    ) -> None:
        self.session_id = session_id
        self.version = version
        self._clock = datetime.fromisoformat(start)
        self._lines: list[str] = []
        self._request_seq = 0
        self._message_seq = 0
        self._tool_seq = 0
        self._last_request_id: str | None = None
        self._last_message_id: str | None = None
        self._last_tool_id: str | None = None
        self._last_usage: dict[str, Any] = {}
        self._last_model = DEFAULT_MODEL

    # -- low level ---------------------------------------------------------

    def _envelope(self, entry_type: str, **fields: Any) -> dict[str, Any]:
        self._clock += timedelta(seconds=1)
        stamp = self._clock.isoformat(timespec="milliseconds")
        entry: dict[str, Any] = {
            "type": entry_type,
            "sessionId": self.session_id,
            "timestamp": stamp.replace("+00:00", "Z"),
            "isSidechain": False,
        }
        if self.version is not None:
            entry["version"] = self.version
        entry.update(fields)
        return entry

    def _emit(self, entry: dict[str, Any]) -> Self:
        self._lines.append(json.dumps(entry))
        return self

    def raw(self, line: str) -> Self:
        """Append an arbitrary line, for example invalid JSON."""
        self._lines.append(line)
        return self

    def entry(self, entry_type: str, **fields: Any) -> Self:
        """Append an entry of any ``type``, for example an unknown one."""
        return self._emit(self._envelope(entry_type, **fields))

    # -- request side ------------------------------------------------------

    def shape(
        self,
        shape_hash: str = "shape-a",
        system: tuple[str, ...] = ("synthetic system block",),
        tools: tuple[tuple[str, str], ...] = (("Read", "synthetic read tool"),),
    ) -> Self:
        """Emit an ``api-request-shape`` entry."""
        shape = {
            "system": [{"type": "text", "text": text} for text in system],
            "tools": [
                {"name": name, "description": desc, "input_schema": {}}
                for name, desc in tools
            ],
        }
        return self.entry("api-request-shape", shapeHash=shape_hash, shape=shape)

    def request(self, shape_hash: str = "shape-a", model: str = DEFAULT_MODEL) -> Self:
        """Emit an ``api-request`` entry; later assistant entries link to it."""
        self._request_seq += 1
        self._last_request_id = f"req-{self._request_seq}"
        return self.entry(
            "api-request",
            id=self._last_request_id,
            shapeHash=shape_hash,
            params={"model": model, "max_tokens": 1},
        )

    def link_to(self, request_id: str | None) -> Self:
        """Make later assistant entries reference ``request_id`` (``None`` omits it)."""
        self._last_request_id = request_id
        return self

    def assistant(
        self,
        input: int = 10,
        cache_write: int = 0,
        cache_read: int = 0,
        output: int = 5,
        entries: int = 1,
        model: str = DEFAULT_MODEL,
        cache_write_1h: int | None = None,
    ) -> Self:
        """Emit one API call as ``entries`` assistant entries sharing a message id.

        Claude Code writes one assistant entry per content block, each carrying
        the same usage, so ``entries > 1`` exercises per-message de-duplication.
        ``cache_write_1h`` (``None`` omits the field, as older versions do) is the
        part of ``cache_write`` written to the 1-hour cache.
        """
        self._message_seq += 1
        self._last_message_id = f"msg-{self._message_seq}"
        self._last_model = model
        self._last_usage = {
            "input_tokens": input,
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output,
        }
        if cache_write_1h is not None:
            self._last_usage["cache_creation"] = {
                "ephemeral_5m_input_tokens": cache_write - cache_write_1h,
                "ephemeral_1h_input_tokens": cache_write_1h,
            }
        for _ in range(entries):
            self._assistant_entry([{"type": "text", "text": "synthetic reply"}])
        return self

    def _assistant_entry(self, content: list[dict[str, Any]]) -> None:
        message = {
            "id": self._last_message_id,
            "model": self._last_model,
            "role": "assistant",
            "content": content,
            "usage": self._last_usage,
        }
        self.entry("assistant", message=message, requestRef=self._last_request_id)

    def tool_use(self, name: str, tool_input: dict[str, Any]) -> Self:
        """Emit a ``tool_use`` block as an extra entry of the last assistant call."""
        self._tool_seq += 1
        self._last_tool_id = f"toolu-{self._tool_seq}"
        block = {
            "type": "tool_use",
            "id": self._last_tool_id,
            "name": name,
            "input": tool_input,
        }
        self._assistant_entry([block])
        return self

    # -- user side ---------------------------------------------------------

    def slash_command(self, name: str) -> Self:
        """Emit a user entry for a typed slash command (name without the slash)."""
        text = (
            f"<command-message>{name}</command-message>\n"
            f"<command-name>/{name}</command-name>"
        )
        return self.entry("user", message={"role": "user", "content": text})

    def tool_result(
        self,
        content: str | list[dict[str, Any]],
        tool_use_id: str | None = None,
    ) -> Self:
        """Emit a user entry holding a ``tool_result`` for the last tool_use."""
        block = {
            "type": "tool_result",
            "tool_use_id": tool_use_id or self._last_tool_id,
            "content": content,
        }
        return self.entry("user", message={"role": "user", "content": [block]})

    # -- attachments -------------------------------------------------------

    def _attachment(self, **fields: Any) -> Self:
        return self.entry("attachment", attachment=fields)

    def skill_listing(
        self,
        content: str = "- synthetic-skill: does a synthetic thing",
        names: tuple[str, ...] = ("synthetic-skill",),
        is_initial: bool = True,
    ) -> Self:
        """Emit a ``skill_listing`` attachment."""
        return self._attachment(
            type="skill_listing",
            content=content,
            names=list(names),
            isInitial=is_initial,
        )

    def agent_delta(
        self,
        added: dict[str, str] | None = None,
        removed: tuple[str, ...] = (),
        is_initial: bool = True,
    ) -> Self:
        """Emit an ``agent_listing_delta`` attachment (name to listing line)."""
        added = (
            {"synthetic-agent": "- synthetic-agent: helper"} if added is None else added
        )
        return self._attachment(
            type="agent_listing_delta",
            addedTypes=list(added),
            addedLines=list(added.values()),
            removedTypes=list(removed),
            isInitial=is_initial,
        )

    def instructions(self, files: dict[str, str] | None = None) -> Self:
        """Emit an ``instructions`` attachment (path to content)."""
        files = files or {"/repo/AGENTS.md": "synthetic instructions"}
        return self._attachment(
            type="instructions",
            files=[
                {"path": path, "content": text, "type": "Project"}
                for path, text in files.items()
            ],
        )

    # -- bookkeeping -------------------------------------------------------

    def cost_state(self, total: float = 1.25, model: str = DEFAULT_MODEL) -> Self:
        """Emit a ``cost-state`` entry."""
        return self.entry(
            "cost-state", totalCostUSD=total, modelUsage={model: {"costUSD": total}}
        )

    def write(self, path: Path) -> Path:
        """Write the transcript to ``path`` (parents created) and return it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("\n".join(self._lines) + "\n").encode("utf-8"))
        return path


def standard_session(
    session_id: str = "sess-1", start: str = DEFAULT_START
) -> TranscriptBuilder:
    """Build the reference session most tests start from.

    One shape, three calls (the first split over two assistant entries plus a
    ``Skill`` tool_use), an instructions attachment and an agent delta before
    call 0, a skill listing between calls 0 and 1, a slash command before call
    2, one tool_result and a final cost-state of 1.25.
    """
    return (
        TranscriptBuilder(session_id=session_id, start=start)
        .shape()
        .instructions()
        .agent_delta()
        .request()
        .assistant(input=10, cache_write=100, cache_read=0, output=5, entries=2)
        .tool_use("Skill", {"skill": "x"})
        .tool_result("synthetic result")
        .skill_listing()
        .request()
        .assistant(input=2, cache_write=0, cache_read=100, output=7)
        .slash_command("y")
        .request()
        .assistant(input=3, cache_write=0, cache_read=110, output=9)
        .cost_state(1.25)
    )
