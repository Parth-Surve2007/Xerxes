import io, os, sys, zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from app import app
def bundle(name='main.py',text='x = 1\n'):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr(name,text)
    return out.getvalue()
def client(monkeypatch):
    monkeypatch.setenv('XERXES_VERIFICATION_TOKEN','test-token'); return app.test_client()
def test_valid_and_invalid_syntax(monkeypatch):
    c=client(monkeypatch); h={'X-Xerxes-Verification-Token':'test-token'}
    ok=c.post('/api/v1/verification/jobs',headers=h,data={'archive':(io.BytesIO(bundle()),'a.zip')}).get_json(); assert ok['status']=='syntax_check_passed'
    bad=c.post('/api/v1/verification/jobs',headers=h,data={'archive':(io.BytesIO(bundle(text='if True print(1)')),'a.zip')}).get_json(); assert bad['status']=='syntax_check_failed'
def test_unsafe_and_arbitrary_command_rejected(monkeypatch):
    c=client(monkeypatch); h={'X-Xerxes-Verification-Token':'test-token'}
    assert c.post('/api/v1/verification/jobs',headers=h,data={'command':'pytest','archive':(io.BytesIO(bundle()),'a.zip')}).status_code==400
    assert c.post('/api/v1/verification/jobs',headers=h,data={'archive':(io.BytesIO(bundle('../x.py')),'a.zip')}).status_code==400
def test_authentication_required(monkeypatch):
    monkeypatch.setenv('XERXES_VERIFICATION_TOKEN','test-token'); assert app.test_client().get('/api/v1/verification/capabilities').status_code==401
