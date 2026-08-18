from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


config = Path("mempalace/config.py")
old_config = '''    @property
    def hooks_auto_save(self):
        """Whether the stop/precompact hooks should block for auto-save.

        When False, hooks pass through without blocking — equivalent to
        disabling auto-save while keeping hook scripts installed.
        """
        env_val = os.environ.get("MEMPALACE_HOOKS_AUTO_SAVE")
        if env_val is not None:
            return env_val.lower() not in ("false", "0", "no")
        hooks = self._file_config.get("hooks", {})
        return hooks.get("auto_save", True)
'''
new_config = '''    @property
    def hooks_auto_save(self):
        """Master switch for Stop, PreCompact, and SessionEnd auto-save.

        When False, every auto-save hook passes through without saving.
        Per-hook controls are applied by :meth:`hook_auto_save_enabled`.
        """
        env_val = os.environ.get("MEMPALACE_HOOKS_AUTO_SAVE")
        if env_val is not None:
            return env_val.lower() not in ("false", "0", "no")
        hooks = self._file_config.get("hooks", {})
        return hooks.get("auto_save", True)

    def hook_auto_save_enabled(self, hook_name: str) -> bool:
        """Return effective auto-save enablement for one hook.

        ``hooks.auto_save`` (and ``MEMPALACE_HOOKS_AUTO_SAVE``) remains the
        master switch. ``hooks.stop``, ``hooks.pre_compact``, and
        ``hooks.session_end`` can opt out independently. Missing or malformed
        per-hook values preserve the historical enabled behavior.
        """
        if not self.hooks_auto_save:
            return False
        hooks = self._file_config.get("hooks", {})
        if not isinstance(hooks, dict):
            return True
        value = hooks.get(hook_name, True)
        return value if isinstance(value, bool) else True
'''
replace_once(config, old_config, new_config)

hooks = Path("mempalace/hooks_cli.py")
text = hooks.read_text(encoding="utf-8")
direct_anchor = '''    # Respect auto_save config toggle (clean opt-out)
    if not MempalaceConfig().hooks_auto_save:
        _output({})
        return
'''
if text.count(direct_anchor) != 2:
    raise SystemExit(
        f"hooks_cli.py: expected two direct auto_save anchors, found {text.count(direct_anchor)}"
    )
text = text.replace(
    direct_anchor,
    '''    # Respect master + Stop-specific auto-save controls.
    if not MempalaceConfig().hook_auto_save_enabled("stop"):
        _output({})
        return
''',
    1,
)
text = text.replace(
    direct_anchor,
    '''    # Respect master + PreCompact-specific auto-save controls.
    if not MempalaceConfig().hook_auto_save_enabled("pre_compact"):
        _output({})
        return
''',
    1,
)
session_anchor = '            auto_save = config.hooks_auto_save\n'
if text.count(session_anchor) != 1:
    raise SystemExit(
        f"hooks_cli.py: expected one SessionEnd auto_save anchor, found {text.count(session_anchor)}"
    )
text = text.replace(
    session_anchor,
    '            auto_save = config.hook_auto_save_enabled("session_end")\n',
    1,
)
hooks.write_text(text, encoding="utf-8")

Path("tests/test_per_hook_auto_save.py").write_text(
    '''import json
from unittest.mock import MagicMock

import pytest

import mempalace.hooks_cli as hooks_cli
from mempalace.config import MempalaceConfig


@pytest.fixture
def hook_env(monkeypatch, tmp_path):
    root = tmp_path / ".mempalace"
    state = root / "hook_state"
    state.mkdir(parents=True)
    monkeypatch.setattr(hooks_cli, "PALACE_ROOT", root)
    monkeypatch.setattr(hooks_cli, "STATE_DIR", state)
    monkeypatch.setattr(hooks_cli, "_MINE_PID_DIR", state / "mine_pids")
    return state


def _config(tmp_path, monkeypatch, hooks):
    monkeypatch.delenv("MEMPALACE_HOOKS_AUTO_SAVE", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return MempalaceConfig(config_dir=str(tmp_path))


def test_per_hook_controls_default_to_enabled(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, {})
    assert cfg.hook_auto_save_enabled("stop") is True
    assert cfg.hook_auto_save_enabled("pre_compact") is True
    assert cfg.hook_auto_save_enabled("session_end") is True


def test_per_hook_controls_are_independent(tmp_path, monkeypatch):
    cfg = _config(
        tmp_path,
        monkeypatch,
        {"stop": False, "pre_compact": True, "session_end": False},
    )
    assert cfg.hook_auto_save_enabled("stop") is False
    assert cfg.hook_auto_save_enabled("pre_compact") is True
    assert cfg.hook_auto_save_enabled("session_end") is False


def test_master_auto_save_false_disables_every_hook(tmp_path, monkeypatch):
    cfg = _config(
        tmp_path,
        monkeypatch,
        {"auto_save": False, "stop": True, "pre_compact": True, "session_end": True},
    )
    assert cfg.hook_auto_save_enabled("stop") is False
    assert cfg.hook_auto_save_enabled("pre_compact") is False
    assert cfg.hook_auto_save_enabled("session_end") is False


def test_env_master_true_still_allows_per_hook_opt_out(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(
        json.dumps({"hooks": {"auto_save": False, "stop": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMPALACE_HOOKS_AUTO_SAVE", "true")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.hooks_auto_save is True
    assert cfg.hook_auto_save_enabled("stop") is False
    assert cfg.hook_auto_save_enabled("pre_compact") is True


def test_non_boolean_per_hook_value_preserves_enabled_behavior(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, {"stop": "false"})
    assert cfg.hook_auto_save_enabled("stop") is True


def _disabled_hook_config():
    config = MagicMock()
    config.hook_auto_save_enabled.return_value = False
    config.hook_desktop_toast = False
    return config


def test_stop_disable_short_circuits_before_counting(monkeypatch, hook_env):
    config = _disabled_hook_config()
    output = []
    count = MagicMock()
    monkeypatch.setattr(hooks_cli, "MempalaceConfig", lambda: config)
    monkeypatch.setattr(hooks_cli, "_output", output.append)
    monkeypatch.setattr(hooks_cli, "_count_human_messages", count)
    hooks_cli.hook_stop(
        {"session_id": "s", "stop_hook_active": False, "transcript_path": ""},
        "claude-code",
    )
    assert output == [{}]
    config.hook_auto_save_enabled.assert_called_once_with("stop")
    count.assert_not_called()


def test_precompact_disable_skips_ingest_and_mine(monkeypatch, hook_env):
    config = _disabled_hook_config()
    output = []
    ingest = MagicMock()
    mine = MagicMock()
    monkeypatch.setattr(hooks_cli, "MempalaceConfig", lambda: config)
    monkeypatch.setattr(hooks_cli, "_output", output.append)
    monkeypatch.setattr(hooks_cli, "_ingest_transcript", ingest)
    monkeypatch.setattr(hooks_cli, "_mine_sync", mine)
    hooks_cli.hook_precompact(
        {"session_id": "s", "transcript_path": "/tmp/session.jsonl"},
        "claude-code",
    )
    assert output == [{}]
    config.hook_auto_save_enabled.assert_called_once_with("pre_compact")
    ingest.assert_not_called()
    mine.assert_not_called()


def test_session_end_disable_skips_flush_but_keeps_cleanup(monkeypatch, hook_env):
    config = _disabled_hook_config()
    output = []
    save = MagicMock()
    ingest = MagicMock()
    auto_ingest = MagicMock()
    cleanup = MagicMock()
    monkeypatch.setattr(hooks_cli, "MempalaceConfig", lambda: config)
    monkeypatch.setattr(hooks_cli, "_output", output.append)
    monkeypatch.setattr(hooks_cli, "_save_diary_direct", save)
    monkeypatch.setattr(hooks_cli, "_ingest_transcript", ingest)
    monkeypatch.setattr(hooks_cli, "_maybe_auto_ingest", auto_ingest)
    monkeypatch.setattr(hooks_cli, "_clear_session_last_save", cleanup)
    hooks_cli.hook_session_end(
        {"session_id": "s", "transcript_path": "/tmp/session.jsonl"},
        "claude-code",
    )
    assert output == [{}]
    config.hook_auto_save_enabled.assert_called_once_with("session_end")
    save.assert_not_called()
    ingest.assert_not_called()
    auto_ingest.assert_not_called()
    cleanup.assert_called_once_with("s")
''',
    encoding="utf-8",
)

readme = Path("hooks/README.md")
readme_text = readme.read_text(encoding="utf-8")
marker = "## Per-hook auto-save controls"
if marker not in readme_text:
    readme_text += '''\n\n## Per-hook auto-save controls\n\n`hooks.auto_save` (or `MEMPALACE_HOOKS_AUTO_SAVE`) remains the master switch. Individual save hooks can also be disabled without disabling the others:\n\n```json\n{\n  "hooks": {\n    "auto_save": true,\n    "stop": false,\n    "pre_compact": true,\n    "session_end": true\n  }\n}\n```\n\nThe three per-hook keys default to `true` when omitted. Setting `auto_save` to `false` disables all three regardless of their individual values.\n'''
    readme.write_text(readme_text, encoding="utf-8")
