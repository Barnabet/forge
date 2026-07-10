from forge.engine.actor import SessionMeta
from forge.engine.skills import discover_skills
from forge.engine.sysprompt import build_system_prompt
from forge.tools.base import ToolContext
from forge.tools.skills_tool import LoadSkillTool


def make_skill(root, name, desc="does things", body="Step one."):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n")
    (d / "helper.sh").write_text("echo hi\n")
    return d


def test_discovery_project_overrides_global(tmp_path):
    make_skill(tmp_path / "g", "deploy", desc="global deploy")
    make_skill(tmp_path / "p", "deploy", desc="project deploy")
    make_skill(tmp_path / "g", "review")
    skills = discover_skills([tmp_path / "g", tmp_path / "p"])
    by_name = {s.name: s for s in skills}
    assert by_name["deploy"].description == "project deploy"
    assert set(by_name) == {"deploy", "review"}


async def test_load_skill_returns_body_and_files(tmp_path):
    make_skill(tmp_path / "g", "deploy", body="Run helper.sh first.")
    tool = LoadSkillTool([tmp_path / "g"])
    r = await tool.run({"name": "deploy"}, ToolContext(cwd=tmp_path))
    assert "Run helper.sh first." in r.output and "helper.sh" in r.output
    assert "---" not in r.output  # frontmatter stripped
    missing = await tool.run({"name": "nope"}, ToolContext(cwd=tmp_path))
    assert missing.is_error


def test_system_prompt_sections(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (home / "memory").mkdir(parents=True)
    (home / "FORGE.md").write_text("Global rule: be terse.")
    (cwd / "AGENTS.md").write_text("Project rule: use uv.")
    (home / "memory" / "MEMORY.md").write_text("- user prefers pnpm")
    make_skill(home / "skills", "deploy", desc="ship it")
    meta = SessionMeta(id="s1", cwd=str(cwd), model="m")
    sp = build_system_prompt(meta, home)
    for needle in ["Global rule: be terse.", "Project rule: use uv.",
                   "user prefers pnpm", "deploy", "ship it", "load_skill",
                   str(cwd)]:
        assert needle in sp


def test_discovery_survives_malformed_skill_md(tmp_path):
    make_skill(tmp_path / "g", "good")
    bad = tmp_path / "g" / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: [unclosed\n---\nbody\n")
    scalar = tmp_path / "g" / "scalar"
    scalar.mkdir()
    (scalar / "SKILL.md").write_text("---\njust a string\n---\nbody\n")
    skills = discover_skills([tmp_path / "g"])
    by_name = {s.name: s for s in skills}
    assert "good" in by_name
    assert "bad" not in by_name
    assert by_name["scalar"].name == "scalar"  # non-mapping frontmatter -> dir-name fallback
