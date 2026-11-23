"""Read Claude Code transcripts into typed records.

This is the only module that knows the transcript format. Claude Code writes one
JSON object per line to ``~/.claude/projects/<project>/<session>.jsonl``. The
format is not a stable API, so everything format-specific lives here and an
unrecognised file raises :class:`TranscriptFormatError` instead of yielding
plausible-looking zeros.

Differences between the documented format and real transcripts (Claude Code
2.1.201 to 2.1.330) that this parser accounts for:

* One API call is written as several ``assistant`` entries, one per content
  block, all with the same ``message.id`` and identical ``usage``. A
  :class:`Call` is created once per message id.
* ``assistant`` entries name their request in ``requestRef``, which equals the
  ``id`` of an ``api-request`` entry. ``api-request`` does NOT reliably precede
  its assistant entry, so linking is by ``requestRef``. Only when ``requestRef``
  is absent do we fall back to the nearest preceding ``api-request``.
* Assistant entries with model ``<synthetic>`` (zero usage, no request) are
  Claude Code's own placeholders, not API calls, and are ignored.
* Slash commands are recorded as ``<command-name>/name</command-name>`` with a
  leading slash, which is stripped.
* ``tool_result.content`` is a string or a list of ``text``/``tool_reference``
  blocks.
* ``cost-state`` is absent from many files; the last one in a file wins.
* A subagent's transcript is a separate file,
  ``<project>/<session>/subagents/agent-*.jsonl`` (next to an
  ``agent-*.meta.json``), in the same format. Only its invocations are used.

Only names, sizes and counts are meant to leave this module's callers; the text
fields on the records exist so later stages can match them against repo files.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

SYNTHETIC_MODEL = "<synthetic>"
UNKNOWN_VERSION = "unknown"
# Claude Code versions whose transcripts this parser was checked against. The
# report warns when a scanned session came from a version not listed here.
TESTED_VERSIONS: tuple[str, ...] = ("2.1.201", "2.1.325", "2.1.326", "2.1.330")
HASH_LENGTH = 16
# Fewer unparseable lines than this in a file with no JSON object is treated as a
# live session cut mid-write; more means the format changed.
MIN_LINES_TO_REJECT = 2
# Entry types Claude Code writes that this parser ignores. They count as
# "recognised", so a session holding only these loads as an empty session.
BOOKKEEPING_TYPES = frozenset(
    {
        "agent-name",
        "ai-title",
        "api-request-blob",
        "atis-latch",
        "custom-title",
        "data-flow-records",
        "derived-from",
        "file-history-delta",
        "file-history-snapshot",
        "last-prompt",
        "mode",
        "permission-mode",
        "pr-link",
        "queue-operation",
        "session-owner",
        "system",
        "tools-used",
    }
)
COMMAND_NAME = re.compile(r"<command-name>/?([^<]*?)</command-name>")


class TranscriptFormatError(Exception):
    """A transcript file has entries but none of a recognised type."""


@dataclass(frozen=True)
class Usage:
    """Token counts reported for one API call.

    ``cache_write`` is the total of cache-creation tokens; ``cache_write_1h`` is
    the part of it written to the 1-hour cache (the rest is the 5-minute tier).
    """

    input: int
    cache_write: int
    cache_read: int
    output: int
    cache_write_1h: int = 0


@dataclass(frozen=True)
class Call:
    """One API call (one assistant message)."""

    timestamp: str
    model: str
    shape_hash: str | None
    usage: Usage


@dataclass(frozen=True)
class Invocation:
    """A skill or agent that was actually run."""

    kind: Literal["skill", "agent"]
    name: str
    via: Literal["tool", "slash"]


@dataclass(frozen=True)
class InstructionFile:
    """A file delivered in an ``instructions`` attachment."""

    path: str
    content: str
    call_index: int


@dataclass(frozen=True)
class ToolResult:
    """Size and identity of one tool result; never its text."""

    tool_name: str
    chars: int
    content_hash: str


@dataclass(frozen=True)
class RequestShape:
    """System blocks and tool definitions of a request."""

    system: tuple[str, ...]
    tools: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Session:
    """Everything one transcript file says about one session.

    Every ``int`` in the attachment lists is an index into ``calls``: the first
    call made after that attachment arrived (``len(calls)`` if none followed).
    """

    session_id: str
    project: str
    version: str
    shapes: dict[str, RequestShape]
    calls: list[Call]
    skill_listings: list[tuple[int, str]]
    agent_added: list[tuple[int, dict[str, str]]]
    agent_removed: list[tuple[int, list[str]]]
    instructions: list[InstructionFile]
    invocations: list[Invocation]
    tool_results: list[ToolResult]
    cost_usd: float | None
    skipped_lines: int = 0


@dataclass(frozen=True)
class _Request:
    shape_hash: str | None
    model: str
    timestamp: str


@dataclass
class _Accumulator:
    """Mutable state while streaming one transcript file."""

    shapes: dict[str, RequestShape] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    skill_listings: list[tuple[int, str]] = field(default_factory=list)
    agent_added: list[tuple[int, dict[str, str]]] = field(default_factory=list)
    agent_removed: list[tuple[int, list[str]]] = field(default_factory=list)
    instructions: list[InstructionFile] = field(default_factory=list)
    invocations: list[Invocation] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    cost_usd: float | None = None
    skipped: int = 0
    version: str | None = None
    first_timestamp: str | None = None
    parsed: int = 0
    recognised: int = 0
    requests: dict[str, _Request] = field(default_factory=dict)
    last_request: _Request | None = None
    call_index: dict[str, int] = field(default_factory=dict)
    tool_names: dict[str, str] = field(default_factory=dict)


def load_sessions(
    root: Path, project: str | None = None, since: date | None = None
) -> Iterator[Session]:
    """Yield sessions under ``root/<project>/<session>.jsonl``.

    The invocations (Skill and Agent tool uses, slash commands) recorded in the
    session's subagent transcripts, ``<project>/<session>/subagents/*.jsonl``,
    are added to its ``invocations``, so an agent dispatched by another agent
    counts as used. Their calls, shapes and attachments are not added: the
    attachment indexes point into the parent's own ``calls``. Subagent files of
    a session that is absent or filtered out are not read.

    ``project`` keeps project directories whose name contains it (case
    insensitive). ``since`` keeps sessions whose first timestamp falls on or
    after that UTC date. A missing ``root`` yields nothing. Raises
    :class:`TranscriptFormatError` for a file with entries but none recognised.
    """
    for path in _transcript_paths(root, project):
        session = _load_file(path, since)
        if session is not None:
            yield session


def _transcript_paths(root: Path, project: str | None) -> Iterator[Path]:
    if not root.is_dir():
        return
    needle = project.lower() if project else None
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if needle is not None and needle not in directory.name.lower():
            continue
        yield from sorted(directory.glob("*.jsonl"))


def _load_file(path: Path, since: date | None) -> Session | None:
    acc = _read_checked(path)
    if acc is None:
        return None
    if since is not None and not _started_on_or_after(acc.first_timestamp, since):
        return None
    for subagent in _subagent_paths(path):
        sub = _read_checked(subagent)
        if sub is not None:
            acc.invocations.extend(sub.invocations)
    return Session(
        session_id=path.stem,
        project=path.parent.name,
        version=acc.version or UNKNOWN_VERSION,
        shapes=acc.shapes,
        calls=acc.calls,
        skill_listings=acc.skill_listings,
        agent_added=acc.agent_added,
        agent_removed=acc.agent_removed,
        instructions=acc.instructions,
        invocations=acc.invocations,
        tool_results=acc.tool_results,
        cost_usd=acc.cost_usd,
        skipped_lines=acc.skipped,
    )


def _subagent_paths(main: Path) -> list[Path]:
    """Subagent transcripts of the session at ``main``, in name order."""
    return sorted((main.parent / main.stem / "subagents").glob("*.jsonl"))


def _read_checked(path: Path) -> _Accumulator | None:
    """Parse ``path``; ``None`` when it holds nothing yet.

    Raises :class:`TranscriptFormatError` when it has entries but none of a
    recognised type, or is mostly lines that are not JSON objects.
    """
    acc = _read_entries(path)
    if acc.parsed == 0:
        if acc.skipped < MIN_LINES_TO_REJECT:
            return None  # empty, brand-new or only one truncated line
        raise TranscriptFormatError(
            f"{path.name}: {acc.skipped} lines and none is a JSON object "
            f"(Claude Code version {acc.version or UNKNOWN_VERSION})"
        )
    if acc.recognised == 0:
        raise TranscriptFormatError(
            f"{path.name}: no recognised transcript entries "
            f"(Claude Code version {acc.version or UNKNOWN_VERSION})"
        )
    return acc


def _read_entries(path: Path) -> _Accumulator:
    acc = _Accumulator()
    # errors="replace": a live session can end mid multi-byte character.
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                _handle_line(acc, line)
    return acc


def _handle_line(acc: _Accumulator, line: str) -> None:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        acc.skipped += 1
        return
    if not isinstance(entry, dict):
        acc.skipped += 1
        return
    acc.parsed += 1
    _note_envelope(acc, entry)
    entry_type = entry.get("type")
    handler = _HANDLERS.get(entry_type) if isinstance(entry_type, str) else None
    if handler is None:
        # A list or dict ``type`` is unhashable and simply not recognised.
        acc.recognised += int(
            isinstance(entry_type, str) and entry_type in BOOKKEEPING_TYPES
        )
        return
    acc.recognised += 1
    if not handler(acc, entry):
        acc.skipped += 1


def _note_envelope(acc: _Accumulator, entry: dict[str, Any]) -> None:
    version = entry.get("version")
    if acc.version is None and isinstance(version, str) and version:
        acc.version = version
    stamp = entry.get("timestamp")
    if acc.first_timestamp is None and isinstance(stamp, str) and stamp:
        acc.first_timestamp = stamp


def _started_on_or_after(stamp: str | None, since: date) -> bool:
    if stamp is None:
        return False
    try:
        return datetime.fromisoformat(stamp).date() >= since
    except ValueError:
        return False


# -- entry handlers: each returns False when the entry is structurally broken ----


def _on_shape(acc: _Accumulator, entry: dict[str, Any]) -> bool:
    shape_hash, shape = entry.get("shapeHash"), entry.get("shape")
    if not isinstance(shape_hash, str) or not isinstance(shape, dict):
        return False
    system = tuple(_block_text(block) for block in _as_list(shape.get("system")))
    tools = tuple(
        (str(tool.get("name", "")), str(tool.get("description", "")))
        for tool in _as_list(shape.get("tools"))
        if isinstance(tool, dict)
    )
    acc.shapes[shape_hash] = RequestShape(system=system, tools=tools)
    return True


def _on_request(acc: _Accumulator, entry: dict[str, Any]) -> bool:
    params = entry.get("params")
    model = params.get("model") if isinstance(params, dict) else None
    shape_hash = entry.get("shapeHash")
    request = _Request(
        shape_hash=shape_hash if isinstance(shape_hash, str) else None,
        model=model if isinstance(model, str) else "",
        timestamp=_str(entry.get("timestamp")),
    )
    acc.last_request = request
    request_id = entry.get("id")
    if isinstance(request_id, str):
        acc.requests[request_id] = request
    return True


def _on_assistant(acc: _Accumulator, entry: dict[str, Any]) -> bool:
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("model") == SYNTHETIC_MODEL:
        return True
    usage = _parse_usage(message.get("usage"))
    if usage is None:
        return False
    for block in _as_list(message.get("content")):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            _on_tool_use(acc, block)
    key = message.get("id")
    if not isinstance(key, str):
        key = f"entry-{len(acc.calls)}"  # no message id: each entry is its own call
    index = acc.call_index.get(key)
    if index is not None:  # later block of a call we already have
        acc.calls[index] = replace(acc.calls[index], usage=usage)
        return True
    acc.call_index[key] = len(acc.calls)
    acc.calls.append(_new_call(acc, entry, message, usage))
    return True


def _new_call(
    acc: _Accumulator, entry: dict[str, Any], message: dict[str, Any], usage: Usage
) -> Call:
    ref = entry.get("requestRef")
    request = acc.requests.get(ref) if isinstance(ref, str) else acc.last_request
    return Call(
        timestamp=(request.timestamp if request else "")
        or _str(entry.get("timestamp")),
        model=(request.model if request else "") or _str(message.get("model")),
        shape_hash=request.shape_hash if request else None,
        usage=usage,
    )


def _parse_usage(raw: object) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    cache_write = _count(raw.get("cache_creation_input_tokens"))
    breakdown = raw.get("cache_creation")
    one_hour = _count(
        breakdown.get("ephemeral_1h_input_tokens") if isinstance(breakdown, dict) else 0
    )
    return Usage(
        input=_count(raw.get("input_tokens")),
        cache_write=cache_write,
        cache_read=_count(raw.get("cache_read_input_tokens")),
        output=_count(raw.get("output_tokens")),
        cache_write_1h=min(one_hour, cache_write),
    )


def _on_tool_use(acc: _Accumulator, block: dict[str, Any]) -> None:
    name, tool_id = block.get("name"), block.get("id")
    if isinstance(name, str) and isinstance(tool_id, str):
        acc.tool_names[tool_id] = name
    tool_input = block.get("input")
    if not isinstance(tool_input, dict):
        return
    if name == "Skill":
        _add_invocation(acc, "skill", tool_input.get("skill"), "tool")
    elif name == "Agent":
        _add_invocation(acc, "agent", tool_input.get("subagent_type"), "tool")


def _add_invocation(
    acc: _Accumulator,
    kind: Literal["skill", "agent"],
    name: object,
    via: Literal["tool", "slash"],
) -> None:
    if isinstance(name, str) and name:
        acc.invocations.append(Invocation(kind, name.removeprefix("/"), via))


def _on_user(acc: _Accumulator, entry: dict[str, Any]) -> bool:
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        _scan_slash_command(acc, content)
        return True
    for block in _as_list(content):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            _on_tool_result(acc, block)
        elif block.get("type") == "text":
            _scan_slash_command(acc, _str(block.get("text")))
    return True


def _scan_slash_command(acc: _Accumulator, text: str) -> None:
    match = COMMAND_NAME.search(text)
    if match:
        _add_invocation(acc, "skill", match.group(1), "slash")


def _on_tool_result(acc: _Accumulator, block: dict[str, Any]) -> None:
    content = block.get("content")
    if isinstance(content, str):
        text = content
    else:  # tool_reference and other non-text blocks count as zero characters
        text = "".join(
            _str(item.get("text"))
            for item in _as_list(content)
            if isinstance(item, dict) and item.get("type") == "text"
        )
    tool_use_id = block.get("tool_use_id")
    name = acc.tool_names.get(tool_use_id, "unknown") if tool_use_id else "unknown"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LENGTH]
    acc.tool_results.append(ToolResult(name, len(text), digest))


def _on_attachment(acc: _Accumulator, entry: dict[str, Any]) -> bool:
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict):
        return False
    at = len(acc.calls)
    kind = attachment.get("type")
    if kind == "skill_listing" and isinstance(attachment.get("content"), str):
        acc.skill_listings.append((at, attachment["content"]))
    elif kind == "agent_listing_delta":
        _on_agent_delta(acc, attachment, at)
    elif kind == "instructions":
        for file in _as_list(attachment.get("files")):
            if isinstance(file, dict):
                path, text = _str(file.get("path")), _str(file.get("content"))
                acc.instructions.append(InstructionFile(path, text, at))
    return True


def _on_agent_delta(acc: _Accumulator, attachment: dict[str, Any], at: int) -> None:
    names = [str(name) for name in _as_list(attachment.get("addedTypes"))]
    lines = [str(line) for line in _as_list(attachment.get("addedLines"))]
    if names:
        acc.agent_added.append((at, dict(zip(names, lines, strict=False))))
    removed = [str(name) for name in _as_list(attachment.get("removedTypes"))]
    if removed:
        acc.agent_removed.append((at, removed))


def _on_cost_state(acc: _Accumulator, entry: dict[str, Any]) -> bool:
    total = entry.get("totalCostUSD")
    if isinstance(total, bool) or not isinstance(total, int | float):
        return False
    acc.cost_usd = float(total)
    return True


# The recognised entry types; a file with none of them is an unknown format.
_HANDLERS = {
    "api-request-shape": _on_shape,
    "api-request": _on_request,
    "assistant": _on_assistant,
    "user": _on_user,
    "attachment": _on_attachment,
    "cost-state": _on_cost_state,
}


# -- small coercion helpers ---------------------------------------------------


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _block_text(block: object) -> str:
    if isinstance(block, dict):
        return _str(block.get("text"))
    return _str(block)
