import sys
import os
import pytest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from grants import GrantStore
from policy import Policy

def test_grant_persists_restricts_and_revokes(tmp_path):
    workspace=tmp_path/'workspace'; extra=tmp_path/'extra'; sibling=tmp_path/'extra-evil'
    workspace.mkdir(); extra.mkdir(); sibling.mkdir(); (extra/'note.txt').write_text('safe',encoding='utf-8')
    store=GrantStore(tmp_path/'app'/'grants.json'); policy=Policy(workspace,store)
    store.add(extra); assert len(GrantStore(tmp_path/'app'/'grants.json').list())==1
    assert policy.resolve(str(extra/'note.txt'))==(extra/'note.txt').resolve()
    try: policy.resolve(str(sibling/'note.txt')); assert False
    except ValueError: pass
    grant_id=store.list()[0]['id']; store.revoke(grant_id)
    try: policy.resolve(str(extra/'note.txt')); assert False
    except ValueError: pass

def test_grant_store_rejects_root(tmp_path):
    store=GrantStore(tmp_path/'grants.json')
    try: store.add(Path(tmp_path.anchor)); assert False
    except ValueError: pass

def test_link_escape_is_excluded_or_rejected(tmp_path):
    approved=tmp_path/'approved'; outside=tmp_path/'outside'; approved.mkdir(); outside.mkdir()
    (outside/'secret.txt').write_text('outside',encoding='utf-8')
    link=approved/'link'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError,NotImplementedError) as exc:
        pytest.skip(f'cannot create Windows symlink/junction in this environment: {exc}')
    store=GrantStore(tmp_path/'grants.json'); store.add(approved); policy=Policy(approved,store)
    with pytest.raises(ValueError): policy.resolve(str(link/'secret.txt'))
    assert not any(item['path'].endswith('secret.txt') for item in policy.list_tree('.'))
    with pytest.raises(ValueError): policy.search('outside', '.')

def test_link_escape_write_destination_is_rejected(tmp_path):
    approved=tmp_path/'approved'; outside=tmp_path/'outside'; approved.mkdir(); outside.mkdir()
    link=approved/'out';
    try: link.symlink_to(outside, target_is_directory=True)
    except (OSError,NotImplementedError) as exc: pytest.skip(f'cannot create Windows symlink/junction: {exc}')
    store=GrantStore(tmp_path/'grants.json'); store.add(approved); policy=Policy(approved,store)
    assert not policy.check('apply_patch', {'path':str(link/'new.txt'),'old_text':'','new_text':'x'}).allowed

def test_search_finds_nested_file_in_granted_root_and_alias(tmp_path):
    workspace=tmp_path/'workspace'; desktop=tmp_path/'Desktop'; nested=desktop/'one'/'two'; workspace.mkdir(); nested.mkdir(parents=True)
    (nested/'hihi.txt').write_text('hello huhu marker',encoding='utf-8')
    store=GrantStore(tmp_path/'grants.json'); store.add(desktop); policy=Policy(workspace,store)
    results=policy.search('hihi','desktop',mode='filename')
    assert len(results)==1 and results[0]['path'].endswith('hihi.txt') and results[0]['match']=='filename'

def test_filename_search_tokenizes_comma_variants_and_skips_generated_dirs(tmp_path):
    workspace=tmp_path/'workspace'; desktop=tmp_path/'Desktop'; nested=desktop/'xerxes'/'nested'; generated=desktop/'node_modules'/'package'; workspace.mkdir(); nested.mkdir(parents=True); generated.mkdir(parents=True)
    (nested/'Huhu-note.txt').write_text('safe',encoding='utf-8'); (generated/'hihi.txt').write_text('generated',encoding='utf-8')
    store=GrantStore(tmp_path/'grants.json'); store.add(desktop); policy=Policy(workspace,store)
    assert policy.resolve('Desktop')==desktop.resolve()
    results=policy.search('hihi, riri, huhu, hehe, ruru','Desktop',mode='filename')
    assert len(results)==1 and results[0]['path'].endswith('Huhu-note.txt')

def test_filename_search_finds_extensionless_hidden_candidate(tmp_path):
    workspace=tmp_path/'workspace'; desktop=tmp_path/'Desktop'; nested=desktop/'nested'; workspace.mkdir(); nested.mkdir(parents=True)
    target=nested/'mimi'; target.write_text('hidden candidate contents',encoding='utf-8')
    store=GrantStore(tmp_path/'grants.json'); store.add(desktop); policy=Policy(workspace,store)
    results=policy.search('hehe huhu riri mimi','Desktop',mode='filename')
    assert len(results)==1 and Path(results[0]['path']).name=='mimi'

def test_filename_search_finds_windows_hidden_candidate_when_supported(tmp_path):
    if not sys.platform.startswith('win'):
        pytest.skip('Windows hidden-file attributes are platform-specific')
    import ctypes
    workspace=tmp_path/'workspace'; workspace.mkdir(); target=workspace/'mimi.txt'; target.write_text('hidden',encoding='utf-8')
    set_attributes=ctypes.windll.kernel32.SetFileAttributesW
    set_attributes.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32]; set_attributes.restype=ctypes.c_int
    if not set_attributes(str(target),2):
        pytest.skip('Windows hidden-file attribute is unavailable')
    try:
        results=Policy(workspace).search('mimi',mode='filename')
        assert len(results)==1 and Path(results[0]['path']).name=='mimi.txt'
    finally:
        set_attributes(str(target),0)

def test_search_reports_a_traversal_limit_without_leaving_authorized_root(tmp_path):
    workspace=tmp_path/'workspace'; workspace.mkdir()
    for index in range(3):
        folder=workspace/f'folder-{index}'; folder.mkdir(); (folder/'candidate.txt').write_text('x',encoding='utf-8')
    policy=Policy(workspace); policy.SEARCH_MAX_DIRECTORIES=2
    results=policy.search('candidate','.',mode='filename')
    assert policy.last_search_info['capped'] is True
    assert policy.last_search_info['visited_directories'] == 3
    assert all(policy.resolve(item['path']).is_relative_to(workspace) for item in results)

def test_search_stops_after_grant_revocation(tmp_path):
    workspace=tmp_path/'workspace'; desktop=tmp_path/'Desktop'; workspace.mkdir(); desktop.mkdir()
    (desktop/'note.txt').write_text('revocation marker',encoding='utf-8')
    store=GrantStore(tmp_path/'grants.json'); grant=store.add(desktop); policy=Policy(workspace,store)
    assert policy.search('revocation','desktop'); store.revoke(grant['id'])
    with pytest.raises(ValueError): policy.search('revocation','desktop')

def test_filename_exact_matches_rank_before_substrings_and_ids_are_stable(tmp_path):
    workspace=tmp_path/'workspace'; workspace.mkdir(); (workspace/'note.txt').write_text('partial',encoding='utf-8'); (workspace/'hihi.txt').write_text('exact',encoding='utf-8')
    policy=Policy(workspace)
    first=policy.search('hihi, note',mode='filename'); second=policy.search('hihi, note',mode='filename')
    assert first[0]['path'].endswith('hihi.txt') and first[0]['match_type']=='exact'
    assert [item['id'] for item in first]==[item['id'] for item in second]

def test_prunes_excluded_directories_but_keeps_similar_legitimate_names(tmp_path):
    workspace=tmp_path/'workspace'; workspace.mkdir(); (workspace/'build'/'nested').mkdir(parents=True); (workspace/'my-build-notes').mkdir(); (workspace/'cache-data').mkdir()
    (workspace/'build'/'nested'/'bad.txt').write_text('bad',encoding='utf-8'); (workspace/'my-build-notes'/'good.txt').write_text('good',encoding='utf-8'); (workspace/'cache-data'/'also-good.txt').write_text('good',encoding='utf-8')
    results=Policy(workspace).search('good',mode='filename')
    paths={item['path'] for item in results}
    assert any('my-build-notes' in path for path in paths) and any('cache-data' in path for path in paths)
    assert not any('build/nested' in path for path in paths)

def test_content_search_skips_binary_and_oversized_files(tmp_path):
    workspace=tmp_path/'workspace'; workspace.mkdir(); (workspace/'binary.bin').write_bytes(b'needle\x00\xff'); (workspace/'large.txt').write_text('needle',encoding='utf-8'); (workspace/'large.txt').write_bytes(b'needle'+b'x'*(512*1024))
    results=Policy(workspace).search('needle',mode='content')
    assert not any(item['path'].endswith('binary.bin') for item in results)
    assert not any(item['path'].endswith('large.txt') for item in results)
