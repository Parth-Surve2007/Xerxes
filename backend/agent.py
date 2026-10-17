import atexit, difflib, json, os, re, socket, sqlite3, subprocess, urllib.request, uuid
from datetime import datetime,timezone
from pathlib import Path
from policy import Policy
from native_provider import NativeProvider
from grants import GrantStore
MODEL_VERSION='local-adapter-v1'
class LocalModel:
    def __init__(self):
        self.native = NativeProvider() if os.environ.get('XERXES_MODEL_PROVIDER','http').strip().lower() == 'native' else None
        self.url=os.environ.get('XERXES_LOCAL_MODEL_URL','').strip()
        self.server_exe=os.environ.get('XERXES_LLAMA_SERVER_EXE','').strip()
        self.model_path=os.environ.get('XERXES_LOCAL_MODEL_PATH','').strip()
        self.server_host='127.0.0.1'; self.server_port=int(os.environ.get('XERXES_LLAMA_SERVER_PORT','56322')); self.process=None
        self.ollama_url=os.environ.get('XERXES_OLLAMA_URL','http://127.0.0.1:11434').rstrip('/')
        self.name=os.environ.get('XERXES_LOCAL_MODEL','qwen2.5-coder:1.5b')
        atexit.register(self.stop)
    def _server_url(self): return f'http://{self.server_host}:{self.server_port}'
    def _ensure_server(self):
        if self.url: return self.url.rstrip('/')
        if not (self.server_exe and self.model_path): return ''
        if self.process and self.process.poll() is None: return self._server_url()
        args=[self.server_exe,'-m',self.model_path,'--host',self.server_host,'--port',str(self.server_port),'-c','2048','-ngl','0','-n','128']
        self.process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,cwd=str(Path(self.server_exe).parent))
        for _ in range(90):
            try:
                with urllib.request.urlopen(self._server_url()+'/health',timeout=2) as res:
                    if res.status==200: return self._server_url()
            except Exception: pass
            if self.process.poll() is not None: break
            import time; time.sleep(1)
        error=self.process.stderr.read(4000).decode(errors='replace') if self.process else ''
        self.stop(); raise RuntimeError(f'llama-server failed to become ready: {error[-1000:]}')
    def stop(self):
        if self.native:
            self.native.stop()
            return
        if self.process and self.process.poll() is None: self.process.terminate()
        self.process=None
    def status(self):
        if self.native: return self.native.status()
        if self.url: return {'state':'ready','provider':'llama.cpp-openai','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':False,'url':self.url}
        if self.server_exe and self.model_path:
            running=bool(self.process and self.process.poll() is None)
            return {'state':'ready' if running else 'configured','provider':'llama.cpp-openai','model':self.model_path,'version':MODEL_VERSION,'diagnostic_mode':False,'server':'managed-lazy'}
        try:
            with urllib.request.urlopen(self.ollama_url+'/api/tags',timeout=2) as res: models=json.loads(res.read()).get('models',[])
            names={m.get('name') for m in models}
            if self.name not in names: return {'state':'missing','provider':'ollama','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':True,'ollama_url':self.ollama_url}
            return {'state':'ready','provider':'ollama','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':False,'ollama_url':self.ollama_url}
        except Exception as exc: return {'state':'unavailable','provider':'ollama','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':True,'reason':type(exc).__name__}
    def plan(self,prompt):
        if self.native:
            text=self.native.plan(prompt)
            fenced=re.search(r'```(?:json)?\s*(.*?)\s*```',text,re.S)
            candidate=fenced.group(1) if fenced else text
            raw=json.loads(candidate)
            if not isinstance(raw,dict) or not isinstance(raw.get('actions'),list) or len(raw['actions'])>12: raise ValueError('native model returned invalid action plan')
            allowed={'list_workspace','read_file','search_workspace','run_tests','apply_patch'}
            if any(not isinstance(a,dict) or a.get('tool') not in allowed or not isinstance(a.get('arguments',{}),dict) for a in raw['actions']): raise ValueError('native model returned unauthorized action')
            return raw
        endpoint=self._ensure_server()
        if endpoint:
            body={'messages':[{'role':'system','content':'Return only JSON with an actions array. Each action must have tool and arguments. Allowed tools: list_workspace, read_file, search_workspace, run_tests, apply_patch.'},{'role':'user','content':prompt}], 'temperature':0, 'max_tokens':512, 'stream':False}
            req=urllib.request.Request(endpoint+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
        else:
            if self.status().get('state')!='ready': return None
            body={'model':self.name,'stream':False,'format':'json','messages':[{'role':'system','content':'Return only JSON with an actions array. Each action must have tool and arguments. Allowed tools: list_workspace, read_file, search_workspace, run_tests, apply_patch.'},{'role':'user','content':prompt}]}
            req=urllib.request.Request(self.ollama_url+'/api/chat',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=120) as res: raw=json.loads(res.read())
        if endpoint:
            content=raw.get('choices',[{}])[0].get('message',{}).get('content','{}').strip()
            fenced=re.search(r'```(?:json)?\s*(.*?)\s*```',content,re.S)
            raw=json.loads(fenced.group(1) if fenced else content)
        else: raw=json.loads(raw.get('message',{}).get('content','{}'))
        if not isinstance(raw,dict) or not isinstance(raw.get('actions'),list) or len(raw['actions'])>12: return None
        allowed={'list_workspace','read_file','search_workspace','run_tests','apply_patch'}
        if any(not isinstance(a,dict) or a.get('tool') not in allowed or not isinstance(a.get('arguments',{}),dict) for a in raw['actions']): return None
        return raw
class RiskClient:
    def __init__(self): self.url=os.environ.get('XERXES_RISK_URL','').rstrip('/')
    def status(self):
        if not self.url: return {'state':'unconfigured','url':None}
        try:
            with urllib.request.urlopen(self.url+'/api/v1/health',timeout=3) as res: return {'state':'ready',**json.loads(res.read())}
        except Exception as exc: return {'state':'unavailable','reason':type(exc).__name__}
    def score(self,metadata):
        if not self.url: return None
        req=urllib.request.Request(self.url+'/api/v1/risk/score',data=json.dumps(metadata).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=5) as res: return json.loads(res.read())
class AgentEngine:
    def __init__(self,workspace,db_path,grant_path=None): self.workspace=workspace; self.grants=GrantStore(grant_path or str(Path(db_path).with_name('grants.json'))); self.policy=Policy(workspace,self.grants); self.model=LocalModel(); self.risk=RiskClient(); self.db_path=db_path; self._init_db()
    def _db(self): c=sqlite3.connect(self.db_path); c.row_factory=sqlite3.Row; return c
    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True,exist_ok=True); c=self._db(); c.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY,prompt TEXT,data TEXT,created_at TEXT,updated_at TEXT)'); c.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,timestamp TEXT,action TEXT,arguments TEXT,authorization TEXT,policy_reason TEXT,risk TEXT,result TEXT,exit_code INTEGER,verification TEXT)'); c.commit(); c.close()
    def _audit(self,task,action,args,auth,reason,risk=None,result=None,code=None,verification=None):
        c=self._db(); c.execute('INSERT INTO audit(task_id,timestamp,action,arguments,authorization,policy_reason,risk,result,exit_code,verification) VALUES (?,?,?,?,?,?,?,?,?,?)',(task,datetime.now(timezone.utc).isoformat(),action,json.dumps(args),auth,reason,json.dumps(risk or {}),json.dumps(result,default=str) if result is not None else None,code,verification)); c.commit(); c.close()
    def audit(self,limit): c=self._db(); r=c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT ?',(limit,)).fetchall(); c.close(); return [dict(x) for x in r]
    def settings(self): return {'workspace':str(self.workspace),'model':self.model.status(),'risk_service':self.risk.status(),'permission_profile':'workspace-scoped; destructive/protected actions denied','privacy':'only structured action metadata may be sent to LinuxONE','capability_scope':'approved workspace and explicit grants','grants':self.grants.list()}
    def _get(self,tid): c=self._db(); r=c.execute('SELECT data FROM tasks WHERE id=?',(tid,)).fetchone(); c.close(); return json.loads(r[0]) if r else None
    def get_task(self,tid): return self._get(tid)
    def create_task(self,prompt):
        plan=self.model.plan(prompt) or self._diagnostic_plan(prompt); t={'id':str(uuid.uuid4()),'prompt':prompt,'status':'planned','model_status':self.model.status(),'plan':plan,'results':[],'created_at':datetime.now(timezone.utc).isoformat()}; c=self._db(); c.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',(t['id'],prompt,json.dumps(t),t['created_at'],t['created_at'])); c.commit(); c.close(); return t
    def _diagnostic_plan(self,prompt):
        p=prompt.lower(); actions=[]
        if any(x in p for x in ['inspect','list','tree','architecture']): actions.append({'tool':'list_workspace','arguments':{}})
        if any(x in p for x in ['search','find','grep']): actions.append({'tool':'search_workspace','arguments':{'query':'TODO'}})
        if 'build' in p: actions.append({'tool':'run_tests','arguments':{'command':'npm run build'}})
        elif any(x in p for x in ['test','pytest']): actions.append({'tool':'run_tests','arguments':{'command':'pytest backend/tests -q'}})
        if not actions: actions=[{'tool':'list_workspace','arguments':{}}]
        return {'mode':'diagnostic','summary':'Local model unavailable; deterministic diagnostic plan used. Configure XERXES_LOCAL_MODEL_URL for model planning.','actions':actions}
    def tool_catalog(self): return [{'name':'list_workspace','risk':'low'},{'name':'read_file','risk':'low'},{'name':'search_workspace','risk':'low'},{'name':'run_tests','risk':'medium'},{'name':'apply_patch','risk':'high; approval required'}]
    def list_tree(self): return self.policy.list_tree()
    def _run(self,tid,a):
        name=a.get('tool'); args=a.get('arguments') or {}; d=self.policy.check(name,args); meta={'action_category':name,'inside_workspace':d.allowed,'reversible':name in {'list_workspace','read_file','search_workspace'},'network':False,'approval':False,'data_sensitivity':'workspace-metadata','privilege':'user'}; risk=None
        try: risk=self.risk.score(meta)
        except Exception as exc: risk={'state':'unavailable','reason':type(exc).__name__}
        if not d.allowed: self._audit(tid,name,args,'denied',d.reason,risk,verification='not_run'); return {'tool':name,'status':'blocked','reason':d.reason,'risk':risk}
        if name=='list_workspace': result=self.policy.list_tree()
        elif name=='read_file': result=self.policy.read_file(args.get('path',''))
        elif name=='search_workspace': result=self.policy.search(args.get('query',''))
        elif name=='run_tests':
            proc=subprocess.run(self.policy.command(args.get('command','')),cwd=self.workspace,capture_output=True,text=True,timeout=60,shell=False); result={'stdout':proc.stdout[-12000:],'stderr':proc.stderr[-12000:],'exit_code':proc.returncode}; self._audit(tid,name,args,'allowed',d.reason,risk,result,proc.returncode,'passed' if proc.returncode==0 else 'failed'); return {'tool':name,'status':'completed' if proc.returncode==0 else 'failed','result':result,'risk':risk}
        elif name=='apply_patch':
            path=self.policy.resolve(args['path']); before=path.read_text(encoding='utf-8'); old=args['old_text']; new=args['new_text']
            if before.count(old)!=1: raise ValueError('patch context must match exactly once')
            path.write_text(before.replace(old,new,1),encoding='utf-8'); after=path.read_text(encoding='utf-8')
            result={'path':args['path'],'changed':before!=after,'diff':'\n'.join(difflib.unified_diff(before.splitlines(),after.splitlines(),fromfile=args['path'],tofile=args['path'])),'verified':after==before.replace(old,new,1)}
        else: result={'message':'tool not enabled in this vertical slice'}
        self._audit(tid,name,args,'allowed',d.reason,risk,result,0,'verified'); return {'tool':name,'status':'completed','result':result,'risk':risk}
    def execute(self,tid,approve=False):
        t=self._get(tid)
        if not t: raise KeyError(tid)
        t['status']='executing'; t['results']=[]
        for a in t['plan'].get('actions',[]):
            if a.get('tool')=='apply_patch' and not approve: t['results'].append({'tool':'apply_patch','status':'approval_required'}); t['status']='approval_required'; break
            t['results'].append(self._run(tid,a))
            if t['results'][-1]['status'] in {'blocked','failed'}: t['status']=t['results'][-1]['status']; break
        else: t['status']='completed'
        c=self._db(); c.execute('UPDATE tasks SET data=?,updated_at=? WHERE id=?',(json.dumps(t),datetime.now(timezone.utc).isoformat(),tid)); c.commit(); c.close(); return t
    def cancel(self,tid):
        t=self._get(tid)
        if not t: raise KeyError(tid)
        t['status']='cancelled'; c=self._db(); c.execute('UPDATE tasks SET data=?,updated_at=? WHERE id=?',(json.dumps(t),datetime.now(timezone.utc).isoformat(),tid)); c.commit(); c.close(); self._audit(tid,'cancel',{},'allowed','user cancellation',verification='verified'); return t
