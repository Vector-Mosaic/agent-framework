from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from repo_platform.tooling import codex_thread_overlay as overlay


ROOT = Path(__file__).resolve().parents[3]


def test_failed_json_publication_preserves_previous_complete_file(tmp_path, monkeypatch):
    target = tmp_path / "index.json"
    overlay.write_json(target, {"threads": {"existing": {"pinned": True}}})
    original = target.read_bytes()

    def fail_replace(source, destination):
        assert Path(source).parent == target.parent
        assert json.loads(Path(source).read_text(encoding="utf-8"))["threads"] == {"new": {}}
        raise OSError("simulated publication failure")

    monkeypatch.setattr(overlay.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publication failure"):
        overlay.write_json(target, {"threads": {"new": {}}})
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_parallel_processes_preserve_every_index_and_pin_update(tmp_path):
    """Real processes contend on both registries while an unlocked reader polls."""

    script = r'''
import sys, time
from pathlib import Path
from repo_platform.tooling import codex_thread_overlay as o
root=Path(sys.argv[1]); worker=int(sys.argv[2])
paths=o.ThreadOverlayPaths(root/'.codex', root, root/'thread_sketchpads')
original=o.read_json
def delayed_read(path):
    value=original(path)
    if path.name in {'index.json','pinned.json'}:
        time.sleep(0.01)  # Makes lost read/modify/write updates observable without a lock.
    return value
o.read_json=delayed_read
(root/f'ready-{worker}').write_text('ready')
while not (root/'start').exists():
    time.sleep(0.005)
for n in range(4):
    tid=f'{worker:08x}-1111-4111-8111-{n:012x}'
    artifact=paths.overlay_root/tid/'sketchpad.md'
    metadata={'thread_id':tid,'thread_name':f'worker {worker} item {n}',
              'pinned':False,'sketchpad_path':artifact.as_posix(), 'updated_at':o.utc_now()}
    o.write_metadata(paths,tid,metadata,path_field='sketchpad_path')
    target=o.ThreadTarget(tid,'','thread_id',None)
    o.set_pinned(paths,target,pinned=True,artifact_path=artifact,path_field='sketchpad_path',missing_noun='sketchpad')
    if n % 2:
        o.set_pinned(paths,target,pinned=False,artifact_path=artifact,path_field='sketchpad_path',missing_noun='sketchpad')
'''
    processes = [subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(worker)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for worker in range(4)]
    try:
        deadline = time.monotonic() + 20
        while len(list(tmp_path.glob("ready-*"))) < len(processes):
            assert time.monotonic() < deadline, "workers failed to reach the start barrier"
            assert all(process.poll() is None for process in processes)
            time.sleep(0.01)
        (tmp_path / "start").write_text("start", encoding="utf-8")
        paths = overlay.ThreadOverlayPaths(tmp_path / ".codex", tmp_path, tmp_path / "thread_sketchpads")
        observed_reads = 0
        while any(process.poll() is None for process in processes):
            assert time.monotonic() < deadline, "registry writers did not finish"
            for path in (overlay.index_path(paths), overlay.pinned_path(paths)):
                if path.exists():
                    assert isinstance(overlay.read_json(path).get("threads"), dict)
                    observed_reads += 1
            time.sleep(0.003)
        for process in processes:
            stdout, stderr = process.communicate(timeout=2)
            assert process.returncode == 0, stdout + stderr
        expected = {f"{worker:08x}-1111-4111-8111-{n:012x}" for worker in range(4) for n in range(4)}
        expected_pinned = {tid for tid in expected if int(tid[-12:], 16) % 2 == 0}
        index = overlay.read_json(overlay.index_path(paths))
        pins = overlay.read_json(overlay.pinned_path(paths))
        assert set(index["threads"]) == expected
        assert set(pins["threads"]) == expected_pinned
        assert pins["pinned_thread_ids"] == sorted(expected_pinned)
        assert {tid for tid, entry in index["threads"].items() if entry["pinned"]} == expected_pinned
        assert observed_reads > 0
        assert not list(paths.overlay_root.rglob("*.tmp"))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_registry_lock_is_released_when_its_process_exits(tmp_path):
    script = """
import os,sys
from pathlib import Path
from repo_platform.tooling import codex_thread_overlay as o
p=Path(sys.argv[1]); paths=o.ThreadOverlayPaths(p,p,p)
with o._registry_lock(paths):
    os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, timeout=5)
    assert result.returncode == 0
    paths = overlay.ThreadOverlayPaths(tmp_path, tmp_path, tmp_path)
    overlay.refresh_index(paths, "existing", {"pinned": True}, path_field="sketchpad_path")
    assert overlay.read_json(overlay.index_path(paths))["threads"]["existing"]["pinned"] is True
