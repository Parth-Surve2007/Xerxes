import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from risk_client import RiskClient, TunnelManager

def test_disabled_client_is_explicitly_unconfigured(monkeypatch):
    monkeypatch.delenv('XERXES_RISK_URL', raising=False)
    monkeypatch.delenv('XERXES_LINUXONE_SSH_HOST', raising=False)
    result=RiskClient().status()
    assert result['state']=='unconfigured' and result['tunnel']=='disabled'

def test_missing_ssh_is_reported_without_starting_a_process(monkeypatch):
    monkeypatch.setenv('XERXES_LINUXONE_SSH_HOST','example.invalid'); monkeypatch.setenv('XERXES_LINUXONE_SSH_USER','linux1')
    manager=TunnelManager(); monkeypatch.setattr('risk_client.shutil.which',lambda _:None)
    assert manager.start() is False and 'ssh executable' in manager.error
    assert manager.process is None

def test_score_allowlist_rejects_extra_metadata(monkeypatch):
    client=RiskClient(); client.direct='http://127.0.0.1:1'
    metadata={key:True for key in ('inside_workspace','reversible','network','approval')}
    metadata.update(action_category='read_file',data_sensitivity='workspace-metadata',privilege='user',secret='nope')
    try: client.score(metadata)
    except ValueError as exc: assert 'schema' in str(exc)
    else: raise AssertionError('extra metadata was accepted')

def test_invalid_remote_score_is_rejected(monkeypatch):
    client=RiskClient(); client.direct='http://example.invalid'
    monkeypatch.setattr(client,'_url',lambda:'http://example.invalid')
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,*args): return json.dumps({'model_version':'synthetic-risk-logreg-v1','category':'low','risk_score':float('nan')}).encode()
    monkeypatch.setattr('risk_client.urllib.request.urlopen',lambda *args,**kwargs:Response())
    metadata={'action_category':'read_file','inside_workspace':True,'reversible':True,'network':False,'approval':False,'data_sensitivity':'workspace-metadata','privilege':'user'}
    try: client.score(metadata)
    except ValueError as exc: assert 'invalid risk' in str(exc)
    else: raise AssertionError('invalid score was accepted')
