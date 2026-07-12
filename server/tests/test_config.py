import pytest
from pydantic import ValidationError

from forge.store.config import (
    ForgeConfig, ModelConfig, Policy, dump_config_toml, load_config,
    policy_matches, save_config, save_global_policy,
)


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


def test_memory_similarity_threshold_bounds():
    assert ForgeConfig(memory_similarity_threshold=0.0).memory_similarity_threshold == 0.0
    assert ForgeConfig(memory_similarity_threshold=1.0).memory_similarity_threshold == 1.0
    for bad in (-0.1, 1.5):
        with pytest.raises(ValidationError):
            ForgeConfig(memory_similarity_threshold=bad)


def test_dump_config_roundtrips_through_load(tmp_path):
    cfg = ForgeConfig(
        max_concurrent=7, memory_similarity_threshold=0.8, base_url="http://x/v1",
        models=[ModelConfig(id="m1", display_name="M1", context_window=123)],
        policies=[Policy(tool="bash", pattern="ls*")])
    save_config(tmp_path, cfg)
    loaded = load_config(tmp_path)
    assert loaded.max_concurrent == 7
    assert loaded.memory_similarity_threshold == 0.8
    assert loaded.base_url == "http://x/v1"
    assert loaded.models == cfg.models
    assert loaded.policies == cfg.policies


def test_dump_config_toml_is_reparseable():
    text = dump_config_toml(ForgeConfig())
    assert "max_concurrent = 3" in text
    # Re-parsing the emitted TOML yields an equivalent config.
    import tomllib
    ForgeConfig.model_validate(tomllib.loads(text))


def test_policy_with_quotes_and_backslashes_roundtrips(tmp_path):
    # Real command lines carry quotes and backslashes; naive interpolation would
    # emit invalid TOML and brick load_config on the next boot.
    policy = Policy(tool="bash", pattern=r'echo "hello world" \& stuff')
    save_global_policy(tmp_path, policy)
    loaded = load_config(tmp_path).policies       # must not raise
    assert policy in loaded
    # saving the same policy twice yields exactly one entry
    save_global_policy(tmp_path, policy)
    assert load_config(tmp_path).policies.count(policy) == 1
