from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from repo_platform.tooling import codex_thread_overlay as overlay
from scripts.tools import codex_instruction_delivery as delivery

ROOT = Path(__file__).resolve().parents[3]
THREAD = "22222222-2222-4222-8222-222222222222"


def args(tmp_path, command="install", operation="install"):
    source = tmp_path / "source"
    owner = source / delivery.SOURCE_PATH
    owner.parent.mkdir(parents=True, exist_ok=True)
    if not owner.exists():
        owner.write_text(delivery.START + '\n# Working together\nOwners {{REPO_ROOT}}\nHome {{CODEX_HOME}}\nState {{BACKUP_ROOT}}\nInterpreter {{PYTHON_COMMAND}}\n' + delivery.END, encoding="utf-8")
    return argparse.Namespace(command=command, operation=operation, source_root=str(source), repo_root=None,
                              codex_root=str(tmp_path / "profile"), backup_root=str(tmp_path / "state"), format="json")


def test_managed_lifecycle_preserves_outside_bytes_and_configuration(tmp_path):
    options = args(tmp_path)
    home = Path(options.codex_root)
    home.mkdir()
    original = b'\xef\xbb\xbf# My rules\r\n\r\nUse careful judgment.\r\nNo trailing newline'
    target = home / 'AGENTS.md'
    target.write_bytes(original)
    config = home / 'config.toml'
    config.write_bytes(b'personal = true\n')
    preview_args = args(tmp_path, 'preview')
    before = {p.name: p.read_bytes() for p in home.iterdir()}
    preview = delivery.build_plan(preview_args)
    assert {p.name: p.read_bytes() for p in home.iterdir()} == before
    assert preview['candidate'].startswith(original)
    delivery.apply_plan(preview)
    assert target.read_bytes() == preview['candidate']
    # Users may add guidance around the managed block, including after install.
    target.write_bytes(target.read_bytes() + b'\n\n# Added afterward\nKeep this.\n')
    owner = Path(options.source_root) / delivery.SOURCE_PATH
    owner.write_text(owner.read_text() .replace('Working together', 'Updated guidance'), encoding='utf-8')
    update = delivery.build_plan(args(tmp_path, 'update'))
    delivery.apply_plan(update)
    assert b'Updated guidance' in target.read_bytes()
    assert target.read_bytes().startswith(original)
    delivery.apply_plan(delivery.build_plan(args(tmp_path, 'remove')))
    assert target.read_bytes() == original + b'\n\n# Added afterward\nKeep this.\n'
    assert config.read_bytes() == b'personal = true\n'
    assert not (home / delivery.STATE_FILENAME).exists()


def test_fresh_install_repeat_and_remove(tmp_path):
    options = args(tmp_path)
    plan = delivery.build_plan(options)
    assert not plan['home'].exists()
    with delivery.installation_lock(plan['home']):
        delivery.apply_plan(plan)
    repeated = delivery.build_plan(options)
    assert not repeated['changed']
    delivery.apply_plan(repeated)
    delivery.apply_plan(delivery.build_plan(args(tmp_path, 'remove')))
    assert list(plan['home'].iterdir()) == []


def test_user_edits_inside_managed_block_are_never_overwritten(tmp_path):
    options = args(tmp_path)
    plan = delivery.build_plan(options)
    plan['home'].mkdir()
    delivery.apply_plan(plan)
    plan['target'].write_bytes(plan['target'].read_bytes().replace(b'Working together', b'My custom wording'))
    edited = plan['target'].read_bytes()
    for operation in ('install', 'update', 'remove'):
        with pytest.raises(ValueError, match='was edited'):
            delivery.build_plan(args(tmp_path, operation))
        assert plan['target'].read_bytes() == edited


def test_override_and_unowned_marker_refuse_install(tmp_path):
    options = args(tmp_path)
    home = Path(options.codex_root)
    home.mkdir()
    override = home / 'AGENTS.override.md'
    override.write_text('Other guidance', encoding='utf-8')
    with pytest.raises(ValueError, match='shadows'):
        delivery.build_plan(options)
    assert override.read_text() == 'Other guidance'
    override.unlink()
    (home / 'AGENTS.md').write_bytes(delivery.MANAGED_START)
    with pytest.raises(ValueError, match='without owned metadata'):
        delivery.build_plan(options)


def test_prepared_install_refuses_concurrent_user_edit(tmp_path):
    options = args(tmp_path)
    plan = delivery.build_plan(options)
    plan['home'].mkdir()
    plan['target'].write_text('Concurrent user edit', encoding='utf-8')
    with pytest.raises(ValueError, match='changed during preparation'):
        delivery.apply_plan(plan)
    assert plan['target'].read_text() == 'Concurrent user edit'


def test_metadata_write_failure_restores_previous_file(tmp_path, monkeypatch):
    options = args(tmp_path)
    home = Path(options.codex_root)
    home.mkdir()
    (home / 'AGENTS.md').write_bytes(b'Original')
    plan = delivery.build_plan(options)
    real_write = delivery.atomic_write
    def fail_metadata(path, data):
        if path.name == delivery.STATE_FILENAME:
            raise OSError('Synthetic failure')
        real_write(path, data)
    monkeypatch.setattr(delivery, 'atomic_write', fail_metadata)
    with pytest.raises(OSError, match='Synthetic'):
        delivery.apply_plan(plan)
    assert plan['target'].read_bytes() == b'Original'


def test_environment_defaults_and_explicit_identity_without_index(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'selected profile'))
    monkeypatch.setenv('AGENT_FRAMEWORK_STATE_HOME', str(tmp_path / 'private state'))
    assert overlay.default_codex_root() == (tmp_path / 'selected profile').resolve()
    assert overlay.default_backup_root() == (tmp_path / 'private state').resolve()
    paths = overlay.resolve_paths(argparse.Namespace(codex_root=None, backup_root=None), 'thread_sketchpads')
    def must_not_read_index(_):
        raise AssertionError('exact identity must not read the application index')
    monkeypatch.setattr(overlay, 'load_session_index', must_not_read_index)
    selected = overlay.resolve_thread_target(argparse.Namespace(thread_id=THREAD), paths, required=True)
    assert selected.thread_id == THREAD
    monkeypatch.setenv('CODEX_THREAD_ID', THREAD)
    selected = overlay.resolve_thread_target(argparse.Namespace(), paths, required=True)
    assert selected.thread_id == THREAD


@pytest.mark.parametrize('name', ['sketchpad', 'todo'])
def test_cli_continuity_from_unindexed_profile(tmp_path, name):
    env = dict(os.environ, CODEX_HOME=str(tmp_path / 'profile'), AGENT_FRAMEWORK_STATE_HOME=str(tmp_path / 'state'), CODEX_THREAD_ID=THREAD)
    env['PYTHONIOENCODING'] = 'utf-8'
    script = ROOT / 'scripts' / 'tools' / f'codex_thread_{name}.py'
    body = '# Current work\n\n## Now\n\n- [ ] Verify unicode: café 東京\n\n## Next\n\n## Parked\n'
    created = subprocess.run([sys.executable, str(script), 'write', '--thread-id', THREAD, '--stdin', '--pin', '--format', 'json'], input=body, capture_output=True, text=True, encoding='utf-8', env=env, cwd=tmp_path)
    assert created.returncode == 0, created.stderr
    checked = subprocess.run([sys.executable, str(script), 'check', '--format', 'json'], capture_output=True, text=True, encoding='utf-8', env=env, cwd=tmp_path)
    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout)
    assert payload['active'] and payload['pinned']
    assert 'café 東京' in payload['excerpt']
    assert not (tmp_path / 'profile').exists()  # no app state/index/database written


def test_cli_uses_actual_public_kernel_and_preserves_home(tmp_path):
    home = tmp_path / 'profile with spaces'
    home.mkdir()
    target = home / 'AGENTS.md'
    target.write_bytes(b'Personal instructions\r\n')
    script = ROOT / 'scripts' / 'tools' / 'codex_instruction_delivery.py'
    common = ['--codex-root', str(home), '--backup-root', str(tmp_path / 'state'), '--format', 'json']
    before = target.read_bytes()
    preview = subprocess.run([sys.executable, str(script), 'preview', *common], capture_output=True, text=True, encoding='utf-8')
    assert preview.returncode == 0, preview.stdout + preview.stderr
    payload = json.loads(preview.stdout)
    assert payload['data']['installed'] is False
    assert '{{' not in payload['data']['candidate_text']
    assert delivery.python_command() in payload['data']['candidate_text']
    assert payload['data']['bindings']['PYTHON_COMMAND'] == delivery.python_command()
    assert target.read_bytes() == before
    for command in ('install', 'update', 'remove'):
        result = subprocess.run([sys.executable, str(script), command, *common], capture_output=True, text=True, encoding='utf-8')
        assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_bytes() == before
    assert list(home.iterdir()) == [target]



def test_python_command_keeps_selected_virtual_environment_launcher(monkeypatch):
    selected = os.path.abspath(os.path.join('venv with spaces', 'bin', 'python'))
    monkeypatch.setattr(delivery.sys, 'executable', selected)
    def refuse_symlink_resolution(*args, **kwargs):
        raise AssertionError('resolving launcher symlinks loses the virtual environment')
    monkeypatch.setattr(delivery.Path, 'resolve', refuse_symlink_resolution)
    command = delivery.python_command()
    expected = ("& '" + selected.replace("'", "''") + "'") if os.name == 'nt' else delivery.shlex.quote(selected)
    assert command == expected
