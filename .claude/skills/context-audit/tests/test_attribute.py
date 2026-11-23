"""Tests for attribute.py, using synthetic repo files and transcripts only."""

from __future__ import annotations

from pathlib import Path

import pytest
from attribute import (
    CHARS_PER_TOKEN,
    Artifact,
    Loaded,
    Unmatched,
    calibrate_ratio,
    match_session,
    scan_repo,
    tokens_from_chars,
)
from transcript_factory import TranscriptBuilder
from transcripts import Session, load_sessions

AGENT_LINE = "- alpha-agent: synthetic agent description"
SKILL_LINE = "- alpha-skill: synthetic skill description"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_repo(root: Path) -> Path:
    write(
        root / ".claude/agents/alpha-agent.md",
        '---\nname: "alpha-agent"\ndescription: "Short."\nmodel: sonnet\n'
        "---\n\nBody.\n",
    )
    write(
        root / ".claude/skills/alpha-skill/SKILL.md",
        "---\nname: alpha-skill\ndescription: Plain one.\n---\n# Skill\ntext\n",
    )
    write(root / "rules/beta.md", "synthetic rule text")
    write(root / "AGENTS.md", "synthetic agents file")
    return root


def artifact(artifacts: list[Artifact], kind: str, name: str) -> Artifact:
    return next(a for a in artifacts if a.kind == kind and a.name == name)


def load_one(builder: TranscriptBuilder, tmp_path: Path) -> Session:
    builder.write(tmp_path / "projects" / "proj" / f"{builder.session_id}.jsonl")
    return next(load_sessions(tmp_path / "projects"))


@pytest.fixture
def artifacts(tmp_path: Path) -> list[Artifact]:
    return scan_repo(make_repo(tmp_path / "repo"))


# -- scan_repo ----------------------------------------------------------------


def test_scan_repo_finds_all_kinds(tmp_path: Path) -> None:
    found = scan_repo(make_repo(tmp_path))
    assert sorted((a.kind, a.name) for a in found) == [
        ("agent", "alpha-agent"),
        ("agents_md", "AGENTS.md"),
        ("rule", "beta"),
        ("skill", "alpha-skill"),
    ]
    agent = artifact(found, "agent", "alpha-agent")
    assert agent.path == tmp_path / ".claude/agents/alpha-agent.md"
    assert agent.description_chars == len("Short.")
    assert agent.body_chars == len("\nBody.\n")
    rule = artifact(found, "rule", "beta")
    assert (rule.description_chars, rule.body_chars) == (0, len("synthetic rule text"))
    agents_md = artifact(found, "agents_md", "AGENTS.md")
    assert (agents_md.description_chars, agents_md.body_chars) == (
        0,
        len("synthetic agents file"),
    )


def test_scan_repo_missing_pieces_is_empty(tmp_path: Path) -> None:
    assert scan_repo(tmp_path) == []


def test_scan_repo_skips_skill_dir_without_skill_md(tmp_path: Path) -> None:
    write(tmp_path / ".claude/skills/empty/notes.md", "x")
    assert scan_repo(tmp_path) == []


def test_skill_without_frontmatter_uses_directory_name(tmp_path: Path) -> None:
    write(tmp_path / ".claude/skills/plain-skill/SKILL.md", "# Title\nbody\n")
    (skill,) = scan_repo(tmp_path)
    assert (skill.kind, skill.name) == ("skill", "plain-skill")
    assert skill.description_chars == 0
    assert skill.body_chars == len("# Title\nbody\n")


def test_frontmatter_name_overrides_file_stem(tmp_path: Path) -> None:
    write(
        tmp_path / ".claude/agents/file-stem.md",
        "---\nname: real-name\ndescription: d\n---\nbody",
    )
    (agent,) = scan_repo(tmp_path)
    assert agent.name == "real-name"


def test_utf8_bom_does_not_hide_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / ".claude/agents/file-stem.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbf---\nname: real-name\ndescription: abcd\n---\nbody")
    (agent,) = scan_repo(tmp_path)
    assert (agent.name, agent.description_chars, agent.body_chars) == (
        "real-name",
        4,
        4,
    )


def test_bom_is_not_counted_in_whole_file_size(tmp_path: Path) -> None:
    path = tmp_path / "rules" / "beta.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbfrule")
    (rule,) = scan_repo(tmp_path)
    assert rule.body_chars == 4


def test_agent_without_name_uses_file_stem(tmp_path: Path) -> None:
    write(tmp_path / ".claude/agents/stem.md", "---\ndescription: d\n---\nbody")
    (agent,) = scan_repo(tmp_path)
    assert agent.name == "stem"


def test_description_forms(tmp_path: Path) -> None:
    forms = {
        "a": "---\nname: a\ndescription: >\n  one two\n  three\nmodel: x\n---\nB",
        "b": "---\nname: b\ndescription: >-\n  one\n  two\n---\nB",
        "c": "---\nname: c\ndescription: |\n  one\n  two\n---\nB",
        "d": '---\nname: d\ndescription: "line\\nbreak and \\"q\\""\n---\nB',
        "e": "---\nname: e\ndescription: 'it''s'\n---\nB",
        "f": "---\nname: f\ndescription: just words here\n---\nB",
    }
    for stem, text in forms.items():
        write(tmp_path / f".claude/agents/{stem}.md", text)
    found = {a.name: a for a in scan_repo(tmp_path)}
    assert found["a"].description_chars == len("one two three")
    assert found["b"].description_chars == len("one two")
    assert found["c"].description_chars == len("one\ntwo")
    assert found["d"].description_chars == len('line\nbreak and "q"')
    assert found["e"].description_chars == len("it's")
    assert found["f"].description_chars == len("just words here")
    assert all(a.body_chars == 1 for a in found.values())


def test_unterminated_frontmatter_counts_whole_file_as_body(tmp_path: Path) -> None:
    text = "---\nname: other\ndescription: d\nno closing delimiter\n"
    write(tmp_path / ".claude/agents/stem.md", text)
    (agent,) = scan_repo(tmp_path)
    assert agent.name == "stem"
    assert (agent.description_chars, agent.body_chars) == (0, len(text))


def test_crlf_files_parse(tmp_path: Path) -> None:
    path = tmp_path / ".claude/agents/crlf.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'---\r\nname: "crlf"\r\ndescription: abc\r\n---\r\nbody')
    (agent,) = scan_repo(tmp_path)
    assert agent.name == "crlf"
    assert agent.description_chars == 3


# -- match_session ------------------------------------------------------------


def test_match_agent_from_listing_delta(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    builder = TranscriptBuilder("s1").agent_delta({"alpha-agent": AGENT_LINE})
    session = load_one(builder.request().assistant(), tmp_path)
    loaded, unmatched = match_session(artifacts, session)
    assert loaded == [
        Loaded(
            artifact(artifacts, "agent", "alpha-agent"),
            "s1",
            0,
            len(AGENT_LINE),
            True,
        )
    ]
    assert unmatched == []


def test_match_skill_from_skill_listing(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    content = f"{SKILL_LINE}\n- alpha-skill-two: other"
    session = load_one(
        TranscriptBuilder("s1").skill_listing(content, ("alpha-skill",)), tmp_path
    )
    loaded, unmatched = match_session(artifacts, session)
    assert [(item.artifact.name, item.chars, item.measured) for item in loaded] == [
        ("alpha-skill", len(SKILL_LINE), True)
    ]
    # the longer name must not be mistaken for the repo skill
    assert unmatched == [Unmatched("skill_listing", len("- alpha-skill-two: other"))]


def test_plugin_skill_with_same_short_name_is_not_the_repo_skill(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    line = "- someplugin:alpha-skill: from a plugin"
    session = load_one(TranscriptBuilder("s1").skill_listing(line), tmp_path)
    loaded, unmatched = match_session(artifacts, session)
    assert loaded == []
    assert unmatched == [Unmatched("skill_listing", len(line))]


def test_listing_boilerplate_lines_are_ignored(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    content = f"Intro text\n{SKILL_LINE}\nTrailing text"
    session = load_one(TranscriptBuilder("s1").skill_listing(content), tmp_path)
    loaded, unmatched = match_session(artifacts, session)
    assert [item.chars for item in loaded] == [len(SKILL_LINE)]
    assert unmatched == []


def test_match_rule_and_agents_md_from_instructions(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    files: dict[str, str] = {
        "D:\\GitHub\\repo\\AGENTS.md": "a" * 30,
        "C:\\Users\\someone\\.claude\\rules\\beta.md": "b" * 20,
        "/home/x/CLAUDE.md": "c" * 7,
    }
    session = load_one(TranscriptBuilder("s1").instructions(files), tmp_path)
    loaded, unmatched = match_session(artifacts, session)
    by_name = {item.artifact.name: item for item in loaded}
    assert set(by_name) == {"AGENTS.md", "beta"}
    assert (by_name["AGENTS.md"].chars, by_name["AGENTS.md"].measured) == (30, True)
    assert (by_name["beta"].chars, by_name["beta"].measured) == (20, True)
    assert unmatched == [Unmatched("instructions:CLAUDE.md", 7)]


def test_instruction_paths_match_on_component_boundaries(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    files: dict[str, str] = {
        "/home/x/NOTAGENTS.md": "x" * 3,
        "/home/x/xrules/beta.md": "y" * 4,
        "/home/x/rules/beta.md.bak": "z" * 5,
        "/home/x/agents.MD": "w" * 6,
    }
    session = load_one(TranscriptBuilder("s1").instructions(files), tmp_path)
    loaded, unmatched = match_session(artifacts, session)
    assert [item.artifact.name for item in loaded] == ["AGENTS.md"]  # case-insensitive
    assert sorted(u.source for u in unmatched) == [
        "instructions:NOTAGENTS.md",
        "instructions:beta.md",
        "instructions:beta.md.bak",
    ]


def test_edited_artifact_not_found_by_name_is_unmatched(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    ghost_agent = "- ghost-agent: was renamed later"
    ghost_skill = "- ghost-skill: was deleted later"
    session = load_one(
        TranscriptBuilder("s1")
        .agent_delta({"ghost-agent": ghost_agent})
        .skill_listing(ghost_skill)
        .request()
        .assistant(),
        tmp_path,
    )
    loaded, unmatched = match_session(artifacts, session)
    assert loaded == []
    assert sorted(unmatched, key=lambda u: u.source) == [
        Unmatched("agent_listing", len(ghost_agent)),
        Unmatched("skill_listing", len(ghost_skill)),
    ]


def test_recorded_size_is_used_when_repo_text_differs(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    old_line = "- alpha-skill: an older and much longer description " + "x" * 200
    session = load_one(TranscriptBuilder("s1").skill_listing(old_line), tmp_path)
    (item,) = match_session(artifacts, session)[0]
    assert item.chars == len(old_line)
    assert item.measured is True


def test_from_call_uses_attachment_index(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    builder = (
        TranscriptBuilder("s1")
        .instructions({"/r/AGENTS.md": "a" * 10})
        .request()
        .assistant()
        .skill_listing(SKILL_LINE)
        .request()
        .assistant()
        .request()
        .assistant()
        .agent_delta({"alpha-agent": AGENT_LINE}, is_initial=False)
        .request()
        .assistant()
    )
    loaded, _ = match_session(artifacts, load_one(builder, tmp_path))
    assert {item.artifact.name: item.from_call for item in loaded} == {
        "AGENTS.md": 0,
        "alpha-skill": 1,
        "alpha-agent": 3,
    }


def test_attachment_after_last_call_indexes_len_calls(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    builder = TranscriptBuilder("s1").request().assistant().skill_listing(SKILL_LINE)
    (item,) = match_session(artifacts, load_one(builder, tmp_path))[0]
    assert item.from_call == 1


def test_resent_attachments_keep_first_index_and_do_not_double_count(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    files: dict[str, str] = {"/r/AGENTS.md": "a" * 10, "/r/CLAUDE.md": "c" * 4}
    ghosts = {"alpha-agent": AGENT_LINE, "ghost-agent": "- ghost-agent: g"}
    listing = f"{SKILL_LINE}\n- ghost: g"
    builder = (
        TranscriptBuilder("s1")
        .instructions(files)
        .skill_listing(listing)
        .agent_delta(ghosts)
        .request()
        .assistant()
        .instructions(files)
        .skill_listing(listing, is_initial=False)
        .agent_delta(ghosts, is_initial=False)
        .request()
        .assistant()
    )
    loaded, unmatched = match_session(artifacts, load_one(builder, tmp_path))
    assert sorted(item.artifact.name for item in loaded) == [
        "AGENTS.md",
        "alpha-agent",
        "alpha-skill",
    ]
    assert {item.from_call for item in loaded} == {0}
    assert sorted((u.source, u.chars) for u in unmatched) == [
        ("agent_listing", len("- ghost-agent: g")),
        ("instructions:CLAUDE.md", 4),
        ("skill_listing", len("- ghost: g")),
    ]


def test_artifact_in_no_attachment_is_not_loaded(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    session = load_one(TranscriptBuilder("s1").request().assistant(), tmp_path)
    assert match_session(artifacts, session) == ([], [])


def test_removed_agent_still_counts_as_loaded(
    artifacts: list[Artifact], tmp_path: Path
) -> None:
    builder = (
        TranscriptBuilder("s1")
        .agent_delta({"alpha-agent": AGENT_LINE})
        .request()
        .assistant()
        .agent_delta({}, removed=("alpha-agent",), is_initial=False)
    )
    loaded, _ = match_session(artifacts, load_one(builder, tmp_path))
    assert [item.artifact.name for item in loaded] == ["alpha-agent"]


# -- tokens and calibration ---------------------------------------------------


def test_tokens_from_chars_rounds_and_defaults() -> None:
    assert tokens_from_chars(400) == round(400 / CHARS_PER_TOKEN)
    assert tokens_from_chars(11, ratio=3.0) == 4
    assert tokens_from_chars(0) == 0


def test_calibrate_ratio_falls_back_to_default(tmp_path: Path) -> None:
    assert calibrate_ratio([]) == CHARS_PER_TOKEN
    no_calls = load_one(TranscriptBuilder("s1").instructions(), tmp_path)
    assert calibrate_ratio([no_calls]) == CHARS_PER_TOKEN


def test_calibrate_ratio_ignores_first_call_without_known_shape(
    tmp_path: Path,
) -> None:
    builder = TranscriptBuilder("s1").request(shape_hash="missing").assistant(input=50)
    assert calibrate_ratio([load_one(builder, tmp_path)]) == CHARS_PER_TOKEN


def test_calibrate_ratio_uses_first_call_context(tmp_path: Path) -> None:
    # 100 system chars + a 10+5 char tool + 20 attachment chars = 135 chars
    builder = (
        TranscriptBuilder("s1")
        .shape(system=("s" * 100,), tools=(("t" * 10, "d" * 5),))
        .instructions({"/r/CLAUDE.md": "i" * 20})
        .request()
        .assistant(input=5, cache_write=20, cache_read=0)
        .request()
        .assistant(input=1, cache_read=500)
    )
    ratio = calibrate_ratio([load_one(builder, tmp_path)])
    assert ratio == pytest.approx(135 / 25)


def test_calibrate_ratio_pools_sessions_by_total(tmp_path: Path) -> None:
    first = (
        TranscriptBuilder("s1")
        .shape(system=("a" * 100,), tools=())
        .request()
        .assistant(input=10, cache_write=10)
    )
    second = (
        TranscriptBuilder("s2")
        .shape(system=("b" * 300,), tools=())
        .request()
        .assistant(input=50, cache_write=0)
    )
    sessions = [
        load_one(first, tmp_path / "one"),
        load_one(second, tmp_path / "two"),
    ]
    assert calibrate_ratio(sessions) == pytest.approx(400 / 70)
