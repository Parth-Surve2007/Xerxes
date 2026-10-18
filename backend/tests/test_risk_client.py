import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from risk_client import RiskClient, TunnelManager

def test_environment_configuration_overrides_user_file(monkeypatch,tmp_path):
    (tmp_path/'linuxone.json').write_text(json.dumps({'risk_url':'http://file.invalid'}),encoding='utf-8')
    monkeypatch.setenv('XERXES_CONFIG_DIR',str(tmp_path)); monkeypatch.setenv('XERXES_RISK_URL','http://env.invalid')
    client=RiskClient()
    assert client.direct=='http://env.invalid' and client.config_source=='environment'

def test_malformed_user_configuration_is_reported_without_values(monkeypatch,tmp_path):
    (tmp_path/'linuxone.json').write_text('{not-json',encoding='utf-8')
    monkeypatch.setenv('XERXES_CONFIG_DIR',str(tmp_path)); monkeypatch.delenv('XERXES_RISK_URL',raising=False)
    result=RiskClient().status()
    assert result['state']=='unconfigured' and result['configuration']['source']=='file'
    assert 'linuxone.json' in result['configuration']['error']

def test_status_does_not_start_tunnel(monkeypatch):
    monkeypatch.setenv('XERXES_LINUXONE_SSH_HOST','host'); monkeypatch.setenv('XERXES_LINUXONE_SSH_USER','user'); monkeypatch.setenv('XERXES_LINUXONE_SSH_KEY','external.pem')
    client=RiskClient(); started=[]; monkeypatch.setattr(client.tunnel,'start',lambda: started.append(True) or True)
    result=client.status()
    assert not started and result['state']=='configured'

def test_disconnect_preserves_historical_score(monkeypatch):
    monkeypatch.setenv('XERXES_RISK_URL','http://risk.invalid')
    client=RiskClient(); client.last_score={'risk_score':0.2,'category':'low','received_at':'now','latency_ms':12.0}
    result=client.disconnect()
    assert result['state']=='disconnected' and result['last_score']['risk_score']==0.2

def test_valid_configuration_persists_without_starting_tunnel(monkeypatch,tmp_path):
    key=tmp_path/'id_ed25519'; key.write_text('fixture key placeholder',encoding='utf-8')
    monkeypatch.setenv('XERXES_CONFIG_DIR',str(tmp_path)); client=RiskClient(); started=[]
    monkeypatch.setattr(client.tunnel,'start',lambda: started.append(True) or True)
    result=client.save_config({'ssh_host':'linuxone.example','ssh_user':'xerxes','ssh_key':str(key)})
    assert result['configured'] and not started
    saved=json.loads((tmp_path/'linuxone.json').read_text(encoding='utf-8'))
    assert saved['ssh_host']=='linuxone.example' and saved['ssh_key']==str(key.resolve())
    assert 'fixture key placeholder' not in json.dumps(saved)

def test_save_rejects_missing_key_without_writing(monkeypatch,tmp_path):
    monkeypatch.setenv('XERXES_CONFIG_DIR',str(tmp_path)); client=RiskClient()
    try: client.save_config({'ssh_host':'linuxone.example','ssh_user':'xerxes','ssh_key':str(tmp_path/'missing.pem')})
    except ValueError as exc: assert 'existing file' in str(exc)
    else: raise AssertionError('missing key was accepted')
    assert not (tmp_path/'linuxone.json').exists()

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
