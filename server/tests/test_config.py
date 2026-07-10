from forge.store.config import Policy, load_config, policy_matches, save_global_policy


def test_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.base_url == "http://127.0.0.1:8317/v1"
    assert cfg.default_autonomy == "yolo" and cfg.max_concurrent == 3
    assert cfg.default_model == cfg.models[0].id


def test_load_toml(tmp_path):
    (tmp_path / "config.toml").write_text(
        'base_url = "http://localhost:9999/v1"\n'
        'default_model = "gpt-5.2"\n'
        "max_concurrent = 2\n\n"
        "[[models]]\n"
        'id = "gpt-5.2"\ndisplay_name = "gpt-5.2"\ncontext_window = 272000\n\n'
        "[[policies]]\n"
        'tool = "bash"\npattern = "pytest*"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.max_concurrent == 2 and cfg.models[0].context_window == 272000
    assert cfg.policies == [Policy(tool="bash", pattern="pytest*")]


def test_policy_matching_and_persist(tmp_path):
    pols = [Policy(tool="bash", pattern="pytest*")]
    assert policy_matches(pols, "bash", "pytest -q tests/")
    assert not policy_matches(pols, "bash", "rm -rf /")
    assert not policy_matches(pols, "edit_file", "pytest.ini")

    save_global_policy(tmp_path, Policy(tool="edit_file", pattern="*"))
    assert Policy(tool="edit_file", pattern="*") in load_config(tmp_path).policies
