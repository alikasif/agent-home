"""Find this repo's agents, skills, rules and ``AGENTS.md`` and match them to
what a session recorded as loaded.

Matching is by name only, never by comparing text, so an artifact edited after
a transcript was written still matches and its recorded size is what counts.
A recorded block whose name is not in the repo is reported as
:class:`Unmatched` and never guessed at. Real sessions list many built-in and
plugin skills and agents, so ``Unmatched`` is mostly "not ours".

Only names and character counts leave this module.
"""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from transcripts import Session

# Turns recorded characters into estimated tokens. ``calibrate_ratio`` runs low
# (it cannot see tool schemas or user messages), so this fixed ratio is used.
CHARS_PER_TOKEN = 4.0
FRONTMATTER_FENCE = "---"
AGENTS_MD = "AGENTS.md"
SKILL_LISTING = "skill_listing"
AGENT_LISTING = "agent_listing"
INSTRUCTIONS_PREFIX = "instructions:"
KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")
BLOCK_INDICATOR = re.compile(r"^([>|])[+-]?\d*$")

Kind = Literal["agent", "skill", "rule", "agents_md"]


@dataclass(frozen=True)
class Artifact:
    """One repo file that can end up in a session's context.

    For agents and skills ``description_chars`` is the length of the frontmatter
    ``description`` value and ``body_chars`` is everything after the frontmatter
    (the part loaded when the artifact is invoked). Files with no frontmatter,
    rules and ``AGENTS.md`` have ``description_chars == 0`` and ``body_chars``
    equal to the whole file.
    """

    kind: Kind
    name: str
    path: Path
    description_chars: int
    body_chars: int


@dataclass(frozen=True)
class Loaded:
    """An artifact a session recorded as loaded.

    ``from_call`` is the index of the first call that carried it and ``chars``
    its size as recorded in the transcript (not the current repo size).
    """

    artifact: Artifact
    session_id: str
    from_call: int
    chars: int
    measured: bool


@dataclass(frozen=True)
class Unmatched:
    """A recorded block no repo artifact matched.

    ``source`` is ``skill_listing`` or ``agent_listing`` (one entry per session
    summing the distinct unmatched entries) or ``instructions:<basename>`` (one
    entry per file).
    """

    source: str
    chars: int


# -- scanning the repo --------------------------------------------------------


def scan_repo(root: Path) -> list[Artifact]:
    """Return the artifacts under ``root``.

    Looks at ``.claude/agents/*.md``, ``.claude/skills/*/SKILL.md``,
    ``rules/*.md`` and ``AGENTS.md``. Names come from the frontmatter ``name``,
    falling back to the file stem (the directory name for skills).
    """
    found: list[Artifact] = []
    for path in sorted((root / ".claude" / "agents").glob("*.md")):
        found.append(_scan_file("agent", path, path.stem))
    for path in sorted((root / ".claude" / "skills").glob("*/SKILL.md")):
        found.append(_scan_file("skill", path, path.parent.name))
    for path in sorted((root / "rules").glob("*.md")):
        found.append(_whole_file("rule", path.stem, path))
    agents_md = root / AGENTS_MD
    if agents_md.is_file():
        found.append(_whole_file("agents_md", AGENTS_MD, agents_md))
    return found


def _read(path: Path) -> str:
    # utf-8-sig: a byte order mark would otherwise hide the frontmatter fence.
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _whole_file(kind: Kind, name: str, path: Path) -> Artifact:
    return Artifact(kind, name, path, 0, len(_read(path)))


def _scan_file(kind: Kind, path: Path, fallback_name: str) -> Artifact:
    text = _read(path)
    split = _split_frontmatter(text)
    if split is None:
        return Artifact(kind, fallback_name, path, 0, len(text))
    fields, body = split
    return Artifact(
        kind,
        fields.get("name") or fallback_name,
        path,
        len(fields.get("description", "")),
        len(body),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """Return ``(fields, body)``, or ``None`` without a terminated frontmatter."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        return None
    for end in range(1, len(lines)):
        if lines[end].strip() == FRONTMATTER_FENCE:
            header = [line.rstrip("\r\n") for line in lines[1:end]]
            return _parse_fields(header), "".join(lines[end + 1 :])
    return None


def _parse_fields(lines: list[str]) -> dict[str, str]:
    """Minimal frontmatter reader: top-level ``key: value`` scalars only."""
    fields: dict[str, str] = {}
    key: str | None = None
    first = ""
    rest: list[str] = []

    def flush() -> None:
        if key is not None:
            fields[key] = _scalar(first, rest)

    for line in lines:
        match = None if line[:1].isspace() else KEY_LINE.match(line)
        if match:
            flush()
            key, first, rest = match.group(1), match.group(2).strip(), []
        elif key is not None:
            rest.append(line)
    flush()
    return fields


def _scalar(first: str, rest: list[str]) -> str:
    block = BLOCK_INDICATOR.match(first)
    if block:
        body = textwrap.dedent("\n".join(rest)).strip("\n")
        if block.group(1) == "|":
            return body
        return " ".join(part.strip() for part in body.splitlines() if part.strip())
    joined = " ".join([first, *(part.strip() for part in rest)]).strip()
    if joined.startswith('"') and joined.endswith('"') and len(joined) >= 2:
        try:
            decoded = json.loads(joined)
        except json.JSONDecodeError:
            return joined[1:-1]
        return decoded if isinstance(decoded, str) else joined[1:-1]
    if joined.startswith("'") and joined.endswith("'") and len(joined) >= 2:
        return joined[1:-1].replace("''", "'")
    return joined


# -- matching a session -------------------------------------------------------


def match_session(
    artifacts: list[Artifact], session: Session
) -> tuple[list[Loaded], list[Unmatched]]:
    """Match what ``session`` recorded as loaded against ``artifacts``.

    Instruction files match by path suffix (``AGENTS.md`` or
    ``rules/<name>.md``, either slash style, case-insensitive), agents by the
    names in ``agent_listing_delta`` attachments and skills by the
    ``- <name>:`` line of a ``skill_listing``. Each artifact is reported once,
    at the first call that carried it, with its recorded size. Removal of an
    agent later in the session does not undo that it was loaded.
    """
    state = _Matcher(artifacts, session.session_id)
    for file in session.instructions:
        state.instruction(file.path, file.content, file.call_index)
    for at, content in session.skill_listings:
        for line in content.splitlines():
            if line.startswith("- "):
                state.listing_entry(SKILL_LISTING, "skill", _entry_name(line), line, at)
    for at, added in session.agent_added:
        for name, line in added.items():
            state.listing_entry(AGENT_LISTING, "agent", name, line, at)
    return state.result()


class _Matcher:
    """Collects first-seen matches and unmatched blocks for one session."""

    def __init__(self, artifacts: list[Artifact], session_id: str) -> None:
        self._session_id = session_id
        self._by_name = {(a.kind, a.name): a for a in artifacts}
        self._by_tail: dict[tuple[str, ...], Artifact] = {}
        for artifact in artifacts:
            if artifact.kind == "agents_md":
                self._by_tail[(AGENTS_MD.lower(),)] = artifact
            elif artifact.kind == "rule":
                self._by_tail[("rules", artifact.path.name.lower())] = artifact
        self._loaded: dict[tuple[Kind, str], Loaded] = {}
        self._unmatched_files: dict[str, Unmatched] = {}
        self._unmatched_entries: dict[str, dict[str, int]] = {}

    def instruction(self, path: str, content: str, at: int) -> None:
        parts = path.replace("\\", "/").lower().split("/")
        artifact = self._by_tail.get(tuple(parts[-1:])) or self._by_tail.get(
            tuple(parts[-2:])
        )
        if artifact is not None:
            self._add(artifact, at, len(content))
            return
        basename = path.replace("\\", "/").rsplit("/", 1)[-1]
        source = INSTRUCTIONS_PREFIX + basename
        key = path.replace("\\", "/").lower()
        self._unmatched_files.setdefault(key, Unmatched(source, len(content)))

    def listing_entry(
        self, source: str, kind: Kind, name: str, line: str, at: int
    ) -> None:
        artifact = self._by_name.get((kind, name))
        if artifact is not None:
            self._add(artifact, at, len(line))
        else:
            self._unmatched_entries.setdefault(source, {}).setdefault(name, len(line))

    def _add(self, artifact: Artifact, at: int, chars: int) -> None:
        key = (artifact.kind, artifact.name)
        if key not in self._loaded:
            self._loaded[key] = Loaded(artifact, self._session_id, at, chars, True)

    def result(self) -> tuple[list[Loaded], list[Unmatched]]:
        loaded = sorted(
            self._loaded.values(),
            key=lambda item: (item.from_call, item.artifact.kind, item.artifact.name),
        )
        unmatched = [
            Unmatched(source, sum(sizes.values()))
            for source, sizes in sorted(self._unmatched_entries.items())
        ]
        unmatched.extend(self._unmatched_files.values())
        return loaded, unmatched


def _entry_name(line: str) -> str:
    """Name of a ``- <name>: <description>`` listing line.

    Plugin skills are ``plugin:skill``, so the name ends at the first colon
    followed by a space, not at the first colon.
    """
    rest = line[2:]
    end = rest.find(": ")
    return rest[:end] if end >= 0 else rest.removesuffix(":")


# -- chars to tokens ----------------------------------------------------------


def tokens_from_chars(chars: int, ratio: float = CHARS_PER_TOKEN) -> int:
    """Estimate tokens for ``chars`` characters at ``ratio`` chars per token."""
    return round(chars / ratio)


def calibrate_ratio(sessions: Iterable[Session]) -> float:
    """Chars per token implied by the first call of each session.

    A session is usable when its first call has a recorded request shape and a
    non-zero input. The ratio pools usable sessions (total recorded chars over
    total measured input tokens). The recorded chars are the system blocks, tool
    names and descriptions and the attachments that arrived before that call, so
    the ratio runs low: tool schemas and the user's own message are measured
    tokens that this module cannot see. Falls back to ``CHARS_PER_TOKEN`` when no
    session is usable.
    """
    chars = tokens = 0
    for session in sessions:
        if not session.calls:
            continue
        first = session.calls[0]
        shape = session.shapes.get(first.shape_hash or "")
        used = first.usage.input + first.usage.cache_write + first.usage.cache_read
        if shape is None or used <= 0:
            continue
        chars += sum(len(text) for text in shape.system)
        chars += sum(len(name) + len(desc) for name, desc in shape.tools)
        chars += _attachment_chars(session, 0)
        tokens += used
    if chars <= 0 or tokens <= 0:
        return CHARS_PER_TOKEN
    return chars / tokens


def _attachment_chars(session: Session, at: int) -> int:
    total = sum(len(f.content) for f in session.instructions if f.call_index == at)
    total += sum(len(text) for index, text in session.skill_listings if index == at)
    for index, added in session.agent_added:
        if index == at:
            total += sum(len(line) for line in added.values())
    return total
