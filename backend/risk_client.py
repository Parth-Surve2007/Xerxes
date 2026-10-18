import json, math, os, re, shutil, socket, subprocess, threading, time, urllib.error, urllib.request
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
        self.config,self.config_source,self.config_error=self._load_config()
        self.direct=self.config.get('risk_url','').rstrip('/')
        self.tunnel=TunnelManager(); self.tunnel.host=self.config.get('ssh_host',''); self.tunnel.user=self.config.get('ssh_user',''); self.tunnel.key=self.config.get('ssh_key','')
        self.tunnel.remote_port=int(self.config.get('remote_port') or self.tunnel.remote_port); self.tunnel.local_port=int(self.config.get('local_port') or self.tunnel.local_port)
        self.last_status=None; self.last_score=None
    @staticmethod
    def _config_path():
        configured=os.environ.get('XERXES_CONFIG_DIR','').strip()
        if configured: return Path(configured).expanduser()/'linuxone.json'
        local=os.environ.get('LOCALAPPDATA','').strip()
        return (Path(local)/'Xerxes'/'linuxone.json') if local else None
    @classmethod
    def _load_config(cls):
        names={'risk_url':'XERXES_RISK_URL','ssh_host':'XERXES_LINUXONE_SSH_HOST','ssh_user':'XERXES_LINUXONE_SSH_USER','ssh_key':'XERXES_LINUXONE_SSH_KEY','remote_port':'XERXES_LINUXONE_REMOTE_PORT','local_port':'XERXES_LINUXONE_LOCAL_PORT'}
        path=cls._config_path(); file_values={}
        if path and path.is_file():
            try:
                file_values=json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(file_values,dict): raise ValueError('configuration must be an object')
            except (OSError,ValueError,json.JSONDecodeError) as exc: return {},'file',f'invalid linuxone.json: {type(exc).__name__}'
        values={key:file_values.get(key,'') for key in names}
        source='file' if any(values.values()) else 'none'
        for key,env_name in names.items():
            raw=os.environ.get(env_name)
            if raw is not None and raw.strip(): values[key]=raw.strip(); source='environment'
        for key in ('remote_port','local_port'):
            if values[key]:
                try: values[key]=int(values[key]); assert 1 <= values[key] <= 65535
                except (ValueError,AssertionError): return {},source,f'{key} must be a TCP port'
        if values['risk_url'] and not values['risk_url'].startswith(('http://','https://')): return {},source,'risk_url must use http or https'
        missing=[] if values['risk_url'] else [key for key in ('ssh_host','ssh_user','ssh_key') if not values[key]]
        if not values['risk_url'] and not missing: source=source
        return values,source,('missing: '+','.join(missing)) if missing else None
    def diagnostics(self):
        required=['risk_url'] if self.direct else ['ssh_host','ssh_user','ssh_key']
        return {'source':self.config_source,'configured':not self.config_error,'missing':[key for key in required if not self.config.get(key)],'error':self.config_error}
    def save_config(self,values):
        if not isinstance(values,dict): raise ValueError('configuration must be an object')
        host=str(values.get('ssh_host','')).strip(); user=str(values.get('ssh_user','')).strip(); key=str(values.get('ssh_key','')).strip()
        if not host or len(host)>253 or not re.fullmatch(r'[A-Za-z0-9.:-]+',host): raise ValueError('SSH hostname is invalid')
        if not user or len(user)>128 or not re.fullmatch(r'[A-Za-z0-9._-]+',user): raise ValueError('SSH username is invalid')
        key_path=Path(key).expanduser()
        if not key_path.is_file(): raise ValueError('SSH key path must point to an existing file')
        if not os.access(key_path,os.R_OK): raise ValueError('SSH key file is not readable')
        clean={'ssh_host':host,'ssh_user':user,'ssh_key':str(key_path.resolve()),'remote_port':values.get('remote_port',5001) or 5001,'local_port':values.get('local_port',5001) or 5001}
        for name in ('remote_port','local_port'):
            try: clean[name]=int(clean[name]); assert 1<=clean[name]<=65535
            except (ValueError,AssertionError): raise ValueError(f'{name} must be a TCP port')
        path=self._config_path()
        if not path: raise ValueError('LinuxONE configuration directory is unavailable')
        path.parent.mkdir(parents=True,exist_ok=True); temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(clean,indent=2)+'\n',encoding='utf-8'); os.replace(temporary,path)
        try: os.chmod(path,0o600)
        except OSError: pass
        self.config=clean; self.config_source='file'; self.config_error=None; self.direct=''; self.tunnel.stop(); self.tunnel.host=host; self.tunnel.user=user; self.tunnel.key=str(key_path.resolve()); self.tunnel.remote_port=clean['remote_port']; self.tunnel.local_port=clean['local_port']
        return self.diagnostics()
    def _url(self,start=True):
        if self.direct: return self.direct
        if self.tunnel.host and self.tunnel.user:
            if not start or self.tunnel.state()=='ready' or self.tunnel.start(): return f'http://127.0.0.1:{self.tunnel.local_port}'
        return ''
    def _get(self,path):
        with urllib.request.urlopen(self._url()+path,timeout=3) as res:
            if res.status != 200: raise ValueError(f'HTTP {res.status}')
            body=res.read(65536); data=json.loads(body)
            if not isinstance(data,dict): raise ValueError('response must be an object')
            return data
    def status(self):
        url=self._url(start=False)
        if not url:
            state=self.tunnel.state() if self.tunnel.host and self.tunnel.user and not self.config_error else 'unconfigured'
            return {'state':state,'configured':state!='unconfigured','configuration':self.diagnostics(),'tunnel':self.tunnel.state(),'url':None,'error':self.config_error or self.tunnel.error,'last_score':self.last_score}
        if not self.direct and self.tunnel.state()!='ready':
            return {'state':'configured','configured':True,'configuration':self.diagnostics(),'tunnel':self.tunnel.state(),'url':None,'error':self.tunnel.error,'last_score':self.last_score}
        try:
            health=self._get('/api/v1/health'); model=self._get('/api/v1/model/info')
            if health.get('status')!='ok' or health.get('model_version')!=MODEL_VERSION: raise ValueError('remote health response is invalid')
            result={'state':'ready','configured':True,'configuration':self.diagnostics(),'tunnel':self.tunnel.state() if self.direct=='' else 'not_required','health':health,'model':model,'architecture':health.get('architecture'),'health_checked_at':datetime.now(timezone.utc).isoformat(),'last_score':self.last_score,'last_score_received_at':(self.last_score or {}).get('received_at') if self.last_score else None,'experimental':True,'advisory':True,'local_policy_authoritative':True}
            self.last_status=result; return result
        except Exception as exc:
            result={'state':'unavailable','configured':True,'configuration':self.diagnostics(),'tunnel':self.tunnel.state() if self.direct=='' else 'not_required','error':str(exc)[:240],'last_score':self.last_score,'last_score_received_at':(self.last_score or {}).get('received_at') if self.last_score else None}; self.last_status=result; return result
    def score(self,metadata):
        if not all(k in metadata for k in FEATURES) or set(metadata)-set(FEATURES): raise ValueError('risk metadata schema mismatch')
        if not self._url(): return None
        started=time.perf_counter(); body=json.dumps(metadata).encode(); req=urllib.request.Request(self._url()+'/api/v1/risk/score',data=body,headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=5) as res: result=json.loads(res.read(65536))
        score=result.get('risk_score')
        if result.get('model_version')!=MODEL_VERSION or not isinstance(score,(int,float)) or not math.isfinite(score) or not isinstance(result.get('category'),str): raise ValueError('invalid risk response')
        result['latency_ms']=round((time.perf_counter()-started)*1000,1); result['received_at']=datetime.now(timezone.utc).isoformat(); result['metadata_fields']=list(metadata); self.last_score=result; return result
    def connect(self):
        if self.direct: return self.status()
        if self.config_error: return self.status()
        if not self.tunnel.host or not self.tunnel.user: return self.status()
        if not self.tunnel.start(): return self.status()
        return self.status()
    def disconnect(self):
        self.tunnel.stop()
        return {'state':'disconnected','configured':bool(self.tunnel.host and self.tunnel.user or self.direct),'configuration':self.diagnostics(),'tunnel':self.tunnel.state(),'last_score':self.last_score,'last_score_received_at':(self.last_score or {}).get('received_at') if self.last_score else None,'experimental':True,'advisory':True,'local_policy_authoritative':True}
