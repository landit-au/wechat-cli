"""init 回归测试 — init --force 不得覆盖已配置的 db_dir（多账号场景）"""

import json

import pytest
from click.testing import CliRunner

import wechat_cli.commands.init as init_mod
import wechat_cli.keys as keys_mod
from wechat_cli.commands.init import init


@pytest.fixture
def env(tmp_path, monkeypatch):
    state_dir = tmp_path / ".wechat-cli"
    state_dir.mkdir()
    config_file = state_dir / "config.json"
    keys_file = state_dir / "all_keys.json"

    monkeypatch.setattr(init_mod, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(init_mod, "CONFIG_FILE", str(config_file))
    monkeypatch.setattr(init_mod, "KEYS_FILE", str(keys_file))
    return state_dir, config_file, keys_file


def test_init_force_preserves_configured_db_dir(env, tmp_path, monkeypatch):
    """多账号机器上 --force 不得因自动检测选错而覆盖 db_dir。"""
    _, config_file, keys_file = env
    good_dir = tmp_path / "xwechat_files" / "account_a" / "db_storage"
    good_dir.mkdir(parents=True)
    wrong_dir = tmp_path / "xwechat_files" / "account_b" / "db_storage"
    wrong_dir.mkdir(parents=True)

    config_file.write_text(json.dumps({"db_dir": str(good_dir)}))
    keys_file.write_text("{}")

    def _auto_detect_should_not_run():
        raise AssertionError("auto_detect_db_dir 不应被调用 — 已有配置应优先")
    monkeypatch.setattr(init_mod, "auto_detect_db_dir", _auto_detect_should_not_run)
    monkeypatch.setattr(keys_mod, "extract_keys", lambda *a, **k: {"k1": "v1"})

    result = CliRunner().invoke(init, ["--force"])
    assert result.exit_code == 0, result.output
    assert json.loads(config_file.read_text())["db_dir"] == str(good_dir)
    assert "提取到 1 个数据库密钥" in result.output


def test_init_force_autodetect_when_no_config(env, tmp_path, monkeypatch):
    state_dir, config_file, keys_file = env
    detected = tmp_path / "detected" / "db_storage"
    detected.mkdir(parents=True)
    monkeypatch.setattr(init_mod, "auto_detect_db_dir", lambda: str(detected))
    monkeypatch.setattr(keys_mod, "extract_keys", lambda *a, **k: {"k1": "v1"})

    result = CliRunner().invoke(init, ["--force"])
    assert result.exit_code == 0, result.output
    assert json.loads(config_file.read_text())["db_dir"] == str(detected)
