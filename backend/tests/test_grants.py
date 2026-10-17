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
