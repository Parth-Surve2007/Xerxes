import json, math, os, shutil, socket, subprocess, threading, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

FEATURES = ('action_category','inside_workspace','reversible','network','approval','data_sensitivity','privilege')
MODEL_VERSION = 'synthetic-risk-logreg-v1'

class TunnelManager:
    def __init__(self):
        self.host=os.environ.get('XERXES_LINUXONE_SSH_HOST','').strip()
        self.user=os.environ.get('XERXES_LINUXONE_SSH_USER','').strip()
        self.key=os.environ.get('XERXES_LINUXONE_SSH_KEY','').strip()
        self.remote_port=int(os.environ.get('XERXES_LINUXONE_REMOTE_PORT','5001'))
        self.local_port=int(os.environ.get('XERXES_LINUXONE_LOCAL_PORT','5001'))
        self.process=None; self.error=None; self.started_at=None; self.lock=threading.Lock()
    def _key_path(self):
        if self.key: return Path(self.key).expanduser()
        candidates=list((Path(__file__).resolve().parents[1]/'Keys').glob('*'))
        private=[p for p in candidates if p.is_file() and p.suffix.lower() in {'.pem','.key'}]
        return private[0] if len(private)==1 else None
    def state(self):
        if not self.host or not self.user: return 'disabled'
        if self.process and self.process.poll() is None: return 'ready'
        return 'unavailable' if self.error else 'configured'
    def start(self):
        with self.lock:
            if self.state()=='ready': return True
            key=self._key_path()
            if not key or not key.is_file(): self.error='SSH key is not configured'; return False
            ssh=shutil.which('ssh')
            if not ssh: self.error='ssh executable is unavailable'; return False
            args=[ssh,'-N','-T','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-L',f'127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}','-i',str(key),f'{self.user}@{self.host}']
            try: self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)); self.started_at=time.time()
            except OSError as exc: self.error=f'SSH startup failed: {type(exc).__name__}'; return False
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.process.poll() is not None:
                message=self.process.stderr.read(1000).decode(errors='replace').strip() if self.process.stderr else ''
                self.error='SSH tunnel exited'+(f': {message[-300:]}' if message else ''); return False
            try:
                with socket.create_connection(('127.0.0.1',self.local_port),timeout=.3): return True
            except OSError: time.sleep(.2)
        self.error='SSH tunnel readiness timeout'; return False
    def stop(self):
        with self.lock:
            process=self.process; self.process=None
        if process and process.poll() is None: process.terminate()
    def __del__(self):
        try: self.stop()
        except Exception: pass

class RiskClient:
    def __init__(self):
        self.direct=os.environ.get('XERXES_RISK_URL','').rstrip('/')
        self.tunnel=TunnelManager(); self.last_status=None; self.last_score=None
    def _url(self):
        if self.direct: return self.direct
        if self.tunnel.host and self.tunnel.user and self.tunnel.start(): return f'http://127.0.0.1:{self.tunnel.local_port}'
        return ''
    def _get(self,path):
        with urllib.request.urlopen(self._url()+path,timeout=3) as res:
            if res.status != 200: raise ValueError(f'HTTP {res.status}')
            body=res.read(65536); data=json.loads(body)
            if not isinstance(data,dict): raise ValueError('response must be an object')
            return data
    def status(self):
        url=self._url()
        if not url:
            return {'state':self.tunnel.state() if self.tunnel.host else 'unconfigured','tunnel':self.tunnel.state(),'url':None,'error':self.tunnel.error}
        try:
            health=self._get('/api/v1/health'); model=self._get('/api/v1/model/info')
            if health.get('status')!='ok' or health.get('model_version')!=MODEL_VERSION: raise ValueError('remote health response is invalid')
            result={'state':'ready','tunnel':self.tunnel.state() if self.direct=='' else 'not_required','health':health,'model':model,'architecture':health.get('architecture'),'health_checked_at':datetime.now(timezone.utc).isoformat(),'last_score':self.last_score,'experimental':True,'advisory':True,'local_policy_authoritative':True}
            self.last_status=result; return result
        except Exception as exc:
            result={'state':'unavailable','tunnel':self.tunnel.state() if self.direct=='' else 'not_required','error':str(exc)[:240],'last_score':self.last_score}; self.last_status=result; return result
    def score(self,metadata):
        if not all(k in metadata for k in FEATURES) or set(metadata)-set(FEATURES): raise ValueError('risk metadata schema mismatch')
        if not self._url(): return None
        started=time.perf_counter(); body=json.dumps(metadata).encode(); req=urllib.request.Request(self._url()+'/api/v1/risk/score',data=body,headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=5) as res: result=json.loads(res.read(65536))
        score=result.get('risk_score')
        if result.get('model_version')!=MODEL_VERSION or not isinstance(score,(int,float)) or not math.isfinite(score) or not isinstance(result.get('category'),str): raise ValueError('invalid risk response')
        result['latency_ms']=round((time.perf_counter()-started)*1000,1); result['received_at']=datetime.now(timezone.utc).isoformat(); result['metadata_fields']=list(metadata); self.last_score=result; return result
