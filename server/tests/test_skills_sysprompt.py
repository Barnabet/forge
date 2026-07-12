from forge.engine.actor import SessionMeta
from forge.engine.skills import (
    discover_skills, skill_tool_activations,
)
from forge.engine.sysprompt import build_system_prompt
from forge.tools.base import ToolContext
from forge.tools.skills_tool import LoadSkillTool


def make_skill(root, name, desc="does things", body="Step one.", activates=None):
    d = root / name
    d.mkdir(parents=True)
    fm = f"---\nname: {name}\ndescription: {desc}\n"
    if activates:
        fm += "activates_tools: [" + ", ".join(activates) + "]\n"
    fm += "---\n"
    (d / "SKILL.md").write_text(f"{fm}\n{body}\n")
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


async def test_load_skill_reports_activated_tools(tmp_path):
    make_skill(tmp_path / "g", "image-generation", body="Prompt well.",
               activates=["create_image"])
    tool = LoadSkillTool([tmp_path / "g"],
                         tool_descriptions={"create_image": "Generate an image."})
    r = await tool.run({"name": "image-generation"}, ToolContext(cwd=tmp_path))
    assert "Activated tools (now available):" in r.output
    assert "create_image — Generate an image." in r.output


def test_skill_activations_parsed(tmp_path):
    make_skill(tmp_path / "g", "image-generation", activates=["create_image"])
    make_skill(tmp_path / "g", "plain")
    skills = {s.name: s for s in discover_skills([tmp_path / "g"])}
    assert skills["image-generation"].activates == ["create_image"]
    assert skills["plain"].activates == []
    assert skill_tool_activations([tmp_path / "g"]) == {"create_image": "image-generation"}


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
    sp = build_system_prompt(meta, home, memory_search=True)
    for needle in ["Global rule: be terse.", "Project rule: use uv.",
                   "## Memory", "remember", "read_memory", "deploy", "ship it",
                   "load_skill", str(cwd), "## Delegation", "spawn_agents"]:
        assert needle in sp
    assert "user prefers pnpm" not in sp  # memory is recalled via tools, not inlined


def test_memory_prompt_matches_tool_availability(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (home / "memory").mkdir(parents=True)
    meta = SessionMeta(id="s1", cwd=str(cwd), model="m")

    # Auto-recall is always advertised; read_memory always available.
    indexed = build_system_prompt(meta, home, memory_search=True)
    plain = build_system_prompt(meta, home, memory_search=False)
    for sp in (indexed, plain):
        assert "recalled automatically" in sp
        assert "read_memory" in sp

    # `remember` is only mentioned in the Memory section when search is available.
    def memory_section(sp):
        end = sp.index("## Skills") if "## Skills" in sp else sp.index("## Guidelines")
        return sp[sp.index("## Memory"):end]

    assert "`remember`" in memory_section(indexed)
    assert "remember" not in memory_section(plain)


def test_request_triage_guidance_present_and_ordered(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (home / "memory").mkdir(parents=True)

    act_meta = SessionMeta(id="s1", cwd=str(cwd), model="m")
    act = build_system_prompt(act_meta, home)
    assert "## Request triage" in act
    assert act.count("## Request triage") == 1
    # Triage guidance sits before the Delegation section.
    assert act.index("## Request triage") < act.index("## Delegation")
    # Guidance covers clarification, planning, and delegation triage.
    triage = act[act.index("## Request triage"):act.index("## Delegation")]
    assert "Clarify only when" in triage
    assert "materially change the result" in triage
    assert "outline your approach before editing" in triage
    assert "update_todos" in triage
    assert "delegate when there are 2+ independent" in triage

    plan_meta = SessionMeta(id="s2", cwd=str(cwd), model="m", mode="plan")
    plan = build_system_prompt(plan_meta, home)
    # Not duplicated in plan mode, and plan-mode block is appended after guidance.
    assert plan.count("## Request triage") == 1
    assert plan.index("## Guidelines") < plan.index("## Plan mode")
    assert plan.index("## Request triage") < plan.index("## Plan mode")


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


def test_stock_skills_bundled_and_user_overridable(tmp_path):
    from forge.engine.skills import stock_skills_dir

    stock = discover_skills([stock_skills_dir()])
    assert "creating-skills" in {s.name for s in stock}

    # A user skill with the same name wins over the stock one.
    make_skill(tmp_path, "creating-skills", desc="user override")
    merged = discover_skills([stock_skills_dir(), tmp_path])
    by_name = {s.name: s for s in merged}
    assert by_name["creating-skills"].description == "user override"
