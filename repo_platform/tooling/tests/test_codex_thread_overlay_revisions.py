from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from repo_platform.tooling import codex_thread_overlay as o
from scripts.tools.codex_thread_todo import sanitize_forward_looking_body

TID = '11111111-1111-4111-8111-111111111111'


def fixture(root, body='before\n', *, name='sketchpad', pin=True):
    paths = o.ThreadOverlayPaths(root/'.codex', root, root/f'thread_{name}s')
    target = o.ThreadTarget(TID, 'fixture', 'thread_id', None)
    path = paths.overlay_root/TID/f'{name}.md'
    result = o.mutate_overlay(paths, target, artifact_path=path, path_field=f'{name}_path',
        action='create', change=lambda b,m: body, pin=pin, expect_absent=True, allow_create=True)
    return paths, target, path, result['mutation']['mutation_token']


def mutate(data, **kwargs):
    paths,target,path,_ = data
    return o.mutate_overlay(paths,target,artifact_path=path,path_field=f'{path.stem}_path',action='test',**kwargs)


def capture(data):
    paths,target,path,_=data
    return o.capture_overlay(paths,target,artifact_path=path,path_field=f'{path.stem}_path')


def navigate(data, *, command='read', transform=lambda b:b, view='raw-v1', **kwargs):
    paths,target,path,_=data
    return o.navigate_overlay(paths,target,artifact_path=path,path_field=f'{path.stem}_path',
        transform=transform,view=view,args=argparse.Namespace(command=command,**kwargs))


def test_duplicate_nested_sections_and_exact_unicode_reconstruction(tmp_path):
    body='preamble😀\n# Same\nintro\n## Same\nfirst\n### Child\nchild\n## Same\nsecond\n# Same\nlast\n'
    data=fixture(tmp_path,body)
    outline=navigate(data,command='outline')
    sections=outline['sections']
    assert [section['title'] for section in sections]==['Same','Same','Child','Same','Same']
    assert len({section['section_ref'] for section in sections})==5
    first=navigate(data,section_ref=sections[1]['section_ref'],max_chars=3)
    assert [item['body'] for item in first['context']]==['preamble😀\n','# Same\nintro\n']
    parts=[first['body']]
    while first['next_cursor']:
        first=navigate(data,cursor=first['next_cursor'],max_chars=3)
        parts.append(first['body'])
    assert ''.join(parts)=='## Same\nfirst\n### Child\nchild\n'
    assert first['delivered']['complete_selection'] is False
    refs=navigate(data,section_ref=sections[1]['section_ref'],context='references')
    assert all('body' not in item for item in refs['context'])
    preamble=navigate(data,section_ref=outline['preamble']['section_ref'])
    assert preamble['body']=='preamble😀\n'


def test_literal_containers_and_headingless_document(tmp_path):
    body='''# Real
```md
# Fence
```
~~~
## Tilde
~~~
    # Indented
> # Quoted
<!--
# Comment
-->
<pre>
# Pre
</pre>
<div>
# Block
</div>

<!DOCTYPE
# Declaration
>
<custom-tag value=">">
# Custom
</custom-tag>

Setext
======
## Other
'''
    assert [s['title'] for s in o.section_outline(body)]==['Real','Other']
    data=fixture(tmp_path,'plain 😀\r\ntext\n')
    outline=navigate(data,command='outline')
    assert outline['sections']==[]
    assert navigate(data,section_ref=outline['preamble']['section_ref'])['body']=='plain 😀\ntext\n'
    assert outline['source']['revision'] != outline['view']['revision']


@pytest.mark.parametrize('selector', ['cursor','section_ref'])
def test_changed_parser_rejects_same_source_selection(tmp_path, monkeypatch, selector):
    data=fixture(tmp_path,'# A\nlong body\n')
    token=(navigate(data,max_chars=2)['next_cursor'] if selector=='cursor'
           else navigate(data,command='outline')['sections'][0]['section_ref'])
    monkeypatch.setattr(o,'PARSER_REVISION','changed-parser')
    with pytest.raises(o.ThreadOverlayError) as failure:
        navigate(data,**{selector:token})
    assert failure.value.code=='stale_selection'


@pytest.mark.parametrize('selector', ['cursor','section_ref'])
def test_source_or_view_change_rejects_continuation(tmp_path,selector):
    data=fixture(tmp_path,'# A\nlong body\n')
    token=(navigate(data,max_chars=2)['next_cursor'] if selector=='cursor'
           else navigate(data,command='outline')['sections'][0]['section_ref'])
    with pytest.raises(o.ThreadOverlayError,match='source, view'):
        navigate(data,view='changed-view',**{selector:token})
    data[2].write_text('# A\nnew body\n')
    with pytest.raises(o.ThreadOverlayError,match='source, view'):
        navigate(data,**{selector:token})


def test_sanitized_hidden_change_invalidates_body_basis_and_cursor(tmp_path):
    data=fixture(tmp_path,'# Todo\n## Now\n- active\n## Done\n- history\n',name='todo')
    first=navigate(data,transform=sanitize_forward_looking_body,view='todo-v1',max_chars=2)
    data[2].write_text('# Todo\n## Now\n- active\n## Done\n- changed history\n')
    second=navigate(data,transform=sanitize_forward_looking_body,view='todo-v1')
    assert first['view']==second['view']
    assert first['source']['revision']!=second['source']['revision']
    with pytest.raises(o.ThreadOverlayError):
        mutate(data,change=lambda b,m:'bad',expected=first['mutation_token'])
    with pytest.raises(o.ThreadOverlayError):
        navigate(data,transform=sanitize_forward_looking_body,view='todo-v1',cursor=first['next_cursor'])


def test_body_edit_preserves_newer_pin_but_explicit_pin_basis_conflicts(tmp_path):
    data=fixture(tmp_path)
    old=data[3]
    mutate(data,pin=False,expected=old)
    result=mutate(data,change=lambda b,m:'after\n',expected=old)
    assert result['metadata']['pinned'] is False
    with pytest.raises(o.ThreadOverlayError) as failure:
        mutate(data,pin=True,expected=old)
    assert failure.value.code=='revision_conflict'
    with pytest.raises(o.ThreadOverlayError):
        mutate(data,change=lambda b,m:'bad',expected=old)
    assert data[2].read_text()=='after\n'


def test_creation_rejects_existing_and_inconsistent_state(tmp_path):
    data=fixture(tmp_path)
    with pytest.raises(o.ThreadOverlayError):
        mutate(data,change=lambda b,m:'bad',expect_absent=True)
    data[2].unlink()
    with pytest.raises(o.ThreadOverlayError):
        mutate(data,change=lambda b,m:'bad',expect_absent=True)
    with pytest.raises(o.ThreadOverlayError):
        mutate(data,repair=True,expected=data[3])
    assert not data[2].exists()


def test_legacy_metadata_and_body_only_state_are_distinct(tmp_path):
    data=fixture(tmp_path)
    meta=o.metadata_path(data[0],TID)
    value=json.loads(meta.read_text());value.pop('body_revision');meta.write_text(json.dumps(value))
    mutate(data,change=lambda b,m:'legacy edit\n',expected=capture(data)['mutation_token'])
    assert capture(data)['consistency']=='consistent'
    meta.unlink()
    assert capture(data)['consistency']=='inconsistent'
    with pytest.raises(o.ThreadOverlayError):
        mutate(data,change=lambda b,m:'bad',expect_absent=True)
    assert data[2].read_text()=='legacy edit\n'


@pytest.mark.parametrize('failure_point',['body','metadata','index','pinned'])
def test_partial_publication_readback_and_repair_do_not_replay_append(tmp_path,monkeypatch,failure_point):
    data=fixture(tmp_path)
    original_replace=o.os.replace
    failed=False
    def fail_once(source,dest):
        nonlocal failed
        name=Path(dest).name
        match={'body':'sketchpad.md','metadata':'metadata.json','index':'index.json','pinned':'pinned.json'}[failure_point]
        if name==match and not failed:
            failed=True
            raise OSError('injected publication interruption')
        return original_replace(source,dest)
    monkeypatch.setattr(o.os,'replace',fail_once)
    with pytest.raises(o.ThreadOverlayError) as failure:
        mutate(data,change=lambda b,m:b+'append\n',expected=data[3])
    assert failure.value.code=='publication_partial'
    current=capture(data)
    expected_body='before\n' if failure_point=='body' else 'before\nappend\n'
    assert data[2].read_text()==expected_body
    mutate(data,repair=True,expected=current['mutation_token'])
    assert data[2].read_text()==expected_body
    assert capture(data)['metadata']['pinned'] is True
    assert capture(data)['consistency']=='consistent'
    assert TID in o.read_json(o.pinned_path(data[0]))['threads']


def _competing_writer(root,token,start,queue,mode):
    paths=o.ThreadOverlayPaths(Path(root)/'.codex',Path(root),Path(root)/'thread_sketchpads')
    target=o.ThreadTarget(TID,'','thread_id',None);path=paths.overlay_root/TID/'sketchpad.md'
    start.wait(5)
    try:
        o.mutate_overlay(paths,target,artifact_path=path,path_field='sketchpad_path',action=mode,expected=token,
            change=(lambda b,m:b+'append\n') if mode=='append' else (lambda b,m:'replacement\n'))
        queue.put('applied')
    except o.ThreadOverlayError as exc:queue.put(exc.code)


def test_competing_processes_apply_one_prepared_edit(tmp_path):
    data=fixture(tmp_path)
    ctx=multiprocessing.get_context('spawn');start=ctx.Event();queue=ctx.Queue()
    workers=[ctx.Process(target=_competing_writer,args=(str(tmp_path),data[3],start,queue,mode)) for mode in ('append','replace')]
    for worker in workers:worker.start()
    start.set()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode==0
    assert sorted(queue.get(timeout=2) for _ in workers)==['applied','revision_conflict']
    assert data[2].read_text() in {'before\nappend\n','replacement\n'}


def _crash_after_publication(root,token,filename):
    paths=o.ThreadOverlayPaths(Path(root)/'.codex',Path(root),Path(root)/'thread_sketchpads')
    target=o.ThreadTarget(TID,'','thread_id',None);path=paths.overlay_root/TID/'sketchpad.md'
    original=o.os.replace
    def crash(source,destination):
        original(source,destination)
        if Path(destination).name==filename:os._exit(43)
    o.os.replace=crash
    o.mutate_overlay(paths,target,artifact_path=path,path_field='sketchpad_path',action='append',
                     change=lambda b,m:b+'once\n',expected=token)


@pytest.mark.parametrize('filename',['sketchpad.md','metadata.json','index.json','pinned.json'])
def test_process_exit_at_publication_boundary_releases_lock_and_preserves_body(tmp_path,filename):
    data=fixture(tmp_path)
    ctx=multiprocessing.get_context('spawn')
    worker=ctx.Process(target=_crash_after_publication,args=(str(tmp_path),data[3],filename))
    worker.start();worker.join(10)
    assert worker.exitcode==43
    current=capture(data)
    assert data[2].read_text()=='before\nonce\n'
    mutate(data,repair=True,expected=current['mutation_token'])
    assert data[2].read_text()=='before\nonce\n'
    assert capture(data)['consistency']=='consistent'


@pytest.mark.parametrize('verb,arguments',[
    ('activate',['--text','replacement']),('write',['--text','replacement']),
    ('append',['--text','note']),('pin',[]),('unpin',[]),('clear',[]),
    ('add',['--text','new item']),('complete',['--id','T001']),
    ('reopen',['--id','T001']),('remove',['--id','T001']),
])
def test_every_todo_cli_mutation_honors_relevant_observed_basis(tmp_path,capsys,verb,arguments):
    from scripts.tools import codex_thread_todo as cli
    data=fixture(tmp_path,'# To-do\n## Now\n- [ ] T001: original\n',name='todo')
    mutate(data,change=lambda b,m:b+'new correction\n',pin=False,expected=data[3])
    before=data[2].read_bytes()
    code=cli.main([verb,'--codex-root',str(data[0].codex_root),'--backup-root',str(tmp_path),
                   '--thread-id',TID,'--expected',data[3],'--json',*arguments])
    assert code==1
    result=json.loads(capsys.readouterr().err)
    assert result['errors'][0]['code']=='revision_conflict'
    assert data[2].read_bytes()==before
    assert capture(data)['metadata']['pinned'] is False


def test_cli_missing_basis_does_not_fetch_and_replay(tmp_path,capsys):
    from scripts.tools import codex_thread_sketchpad as cli
    data=fixture(tmp_path)
    assert cli.main(['append','--codex-root',str(data[0].codex_root),'--backup-root',str(tmp_path),
                     '--thread-id',TID,'--text','unsafe','--json'])==1
    result=json.loads(capsys.readouterr().err)
    assert result['errors'][0]['code']=='precondition_required'
    assert data[2].read_text()=='before\n'


@pytest.mark.parametrize('field', ['body_revision', 'pin_revision'])
@pytest.mark.parametrize('malformed', [[], {'unexpected': 'object'}])
def test_malformed_revision_metadata_requires_reconciliation(tmp_path, field, malformed):
    data = fixture(tmp_path)
    path = o.metadata_path(data[0], TID)
    metadata = json.loads(path.read_text())
    metadata[field] = malformed
    path.write_text(json.dumps(metadata))
    current = capture(data)
    assert current['consistency'] == 'inconsistent'
    with pytest.raises(o.ThreadOverlayError) as failure:
        mutate(data, change=lambda b, m: 'replacement', expected=current['mutation_token'])
    assert failure.value.code == 'inconsistent_overlay'
    assert data[2].read_text() == 'before\n'


@pytest.mark.parametrize("operation", ["_capture_locked", "_file_lock"])
def test_cli_initial_access_failure_is_structured_without_effect(tmp_path, monkeypatch, capsys, operation):
    from scripts.tools import codex_thread_sketchpad as cli
    data = fixture(tmp_path)
    def unavailable(*args, **kwargs):
        raise PermissionError('synthetic unavailable store')
    monkeypatch.setattr(o, operation, unavailable)
    result = cli.main(['append', '--codex-root', str(data[0].codex_root),
                       '--backup-root', str(tmp_path), '--thread-id', TID,
                       '--expected', data[3], '--text', 'once', '--json'])
    assert result == 1
    error = json.loads(capsys.readouterr().err)['errors'][0]
    assert error['code'] == 'overlay_unavailable'
    assert error['details']['effect'] == 'none'
    assert data[2].read_text() == 'before\n'


def test_final_readback_failure_preserves_committed_effects(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    original = o._capture_locked
    calls = 0
    def fail_final(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError('synthetic final readback failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(o, '_capture_locked', fail_final)
    with pytest.raises(o.ThreadOverlayError) as failure:
        mutate(data, change=lambda b, m: b + 'once\n', expected=data[3])
    assert failure.value.code == 'publication_partial'
    assert set(failure.value.details['effects'].values()) == {'committed'}
    assert data[2].read_text() == 'before\nonce\n'
    current = capture(data)
    mutate(data, repair=True, expected=current['mutation_token'])
    assert data[2].read_text() == 'before\nonce\n'
