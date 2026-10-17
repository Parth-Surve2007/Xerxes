import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import AgentEngine

def engine(tmp_path): return AgentEngine(tmp_path,str(tmp_path/'agent.db'))

def test_create_pdf_requires_approval_then_writes_valid_pdf(tmp_path):
    e=engine(tmp_path); task=e.create_task('pdf'); task['plan']={'actions':[{'tool':'create_pdf','arguments':{'filename':'Welcome.pdf','text':'Hello, welcome to Xerxes','title':'Welcome'}}]}
    with e._db() as c: import json; c.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(task),task['id'])); c.commit()
    pending=e.execute(task['id']); assert pending['status']=='approval_required'; assert not (tmp_path/'Welcome.pdf').exists()
    done=e.execute(task['id'],approve=True); assert done['status']=='completed'; assert (tmp_path/'Welcome.pdf').read_bytes().startswith(b'%PDF-')

def test_pdf_rejects_conflict_and_outside(tmp_path):
    e=engine(tmp_path); (tmp_path/'Existing.pdf').write_bytes(b'existing')
    assert not e.policy.check('create_pdf',{'filename':'Existing.pdf','text':'x'}).allowed
    assert not e.policy.check('create_pdf',{'filename':'x.pdf','destination':'../outside','text':'x'}).allowed
    assert not e.policy.check('create_pdf',{'filename':'x.txt','text':'x'}).allowed
    assert not e.policy.check('create_pdf',{'filename':'x.pdf','text':'   '}).allowed

def test_pdf_plan_is_allowed_and_revocation_blocks(tmp_path):
    e=engine(tmp_path/'workspace'); extra=tmp_path/'extra'; extra.mkdir(); grant=e.grants.add(extra)
    args={'filename':'x.pdf','destination':str(extra),'text':'hello'}; assert e.policy.check('create_pdf',args).allowed
    e.grants.revoke(grant['id']); assert not e.policy.check('create_pdf',args).allowed

def test_content_alias_is_normalized_and_missing_text_is_rejected(tmp_path):
    e=engine(tmp_path)
    task=e.create_task('create pdf'); task['plan']={'actions':[{'tool':'create_pdf','arguments':{'content':'hello welcome to IBM Z Datathon','filename':'alias.pdf'}}]}
    with e._db() as c: import json; c.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(task),task['id'])); c.commit()
    assert e.execute(task['id'])['status']=='approval_required'
    assert e.execute(task['id'],approve=True)['status']=='completed'
    assert not e.policy.check('create_pdf',{'filename':'missing.pdf'}).allowed

def test_readme_is_read_before_pdf_and_content_is_included(tmp_path):
    (tmp_path/'README.md').write_text('README source content',encoding='utf-8')
    e=engine(tmp_path); e.model.plan=lambda prompt:{'actions':[{'tool':'create_pdf','arguments':{'filename':'Welcome_to_Xerxes.pdf','text':'placeholder'}}]}; task=e.create_task('Make a PDF and copy contents from readme file')
    names=[a['tool'] for a in task['plan']['actions']]
    assert names.index('read_file')<names.index('create_pdf')
    done=e.execute(task['id'],approve=True)
    assert done['status']=='completed'
    assert (tmp_path/'Welcome_to_Xerxes.pdf').exists()

def test_pdf_cannot_be_modified_with_apply_patch(tmp_path):
    assert not engine(tmp_path).policy.check('apply_patch',{'path':'output.pdf','old_text':'a','new_text':'b'}).allowed
