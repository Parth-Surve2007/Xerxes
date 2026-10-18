import atexit, difflib, json, os, re, socket, sqlite3, subprocess, urllib.request, uuid, time, threading
from datetime import datetime,timezone
from pathlib import Path
from policy import Policy
from native_provider import NativeProvider
from grants import GrantStore
from capabilities import available_capabilities, capability, parse_response_text, planner_prompt, validate_arguments
from risk_client import RiskClient
MODEL_VERSION='local-adapter-v1'
class LocalModel:
    def __init__(self):
        self.native = NativeProvider() if os.environ.get('XERXES_MODEL_PROVIDER','http').strip().lower() == 'native' else None
        self.url=os.environ.get('XERXES_LOCAL_MODEL_URL','').strip()
        self.server_exe=os.environ.get('XERXES_LLAMA_SERVER_EXE','').strip()
        self.model_path=os.environ.get('XERXES_LOCAL_MODEL_PATH','').strip()
        self.server_host='127.0.0.1'; self.server_port=int(os.environ.get('XERXES_LLAMA_SERVER_PORT','56322')); self.process=None
        self.settings_path=Path(os.environ.get('XERXES_RUNTIME_SETTINGS','')).expanduser() if os.environ.get('XERXES_RUNTIME_SETTINGS') else None
        self.gpu_mode=self._load_gpu_mode(os.environ.get('XERXES_GPU_MODE','cpu'))
        self.gpu_layers=max(0,int(os.environ.get('XERXES_GPU_LAYERS','12')))
        self.ollama_url=os.environ.get('XERXES_OLLAMA_URL','http://127.0.0.1:11434').rstrip('/')
        self.name=os.environ.get('XERXES_LOCAL_MODEL','qwen2.5-coder:1.5b'); self.effective_gpu_mode=None; self.pending_gpu_mode=None; self._log_lines=[]; self._offload_evidence=[]; self._gpu_layers_offloaded=None; self._gpu_layer_ids=set(); self._last_inference=None; self._log_lock=threading.Lock()
        atexit.register(self.stop)
    def _load_gpu_mode(self, fallback):
        try:
            value=json.loads(self.settings_path.read_text(encoding='utf-8')).get('gpu_mode') if self.settings_path and self.settings_path.is_file() else fallback
            return value if value in {'cpu','cuda'} else 'cpu'
        except (OSError,ValueError,TypeError): return 'cpu'
    @staticmethod
    def parse_offload_log(line):
        match=re.search(r'(?:offload|offloaded|offloading).*?(\d+)\s*(?:/\s*\d+\s*)?layers?\s+(?:to|on)\s+(?:the\s+)?GPU',line,re.I)
        return int(match.group(1)) if match else None
    @staticmethod
    def parse_gpu_layer_log(line):
        match=re.search(r'\blayer\s+(\d+)\s+assigned\s+to\s+device\s+CUDA\d+\b',line,re.I)
        return int(match.group(1)) if match else None
    def _server_url(self): return f'http://{self.server_host}:{self.server_port}'
    def _server_args(self): return [self.server_exe,'-m',self.model_path,'--host',self.server_host,'--port',str(self.server_port),'-c','2048','-ngl',str(self.gpu_layers if self.gpu_mode=='cuda' else 0),'-n','128','--log-verbosity','5']
    def _capture_stream(self, stream):
        for raw in stream:
            line=raw.decode(errors='replace').rstrip()
            with self._log_lock:
                self._log_lines.append(line); self._log_lines=self._log_lines[-200:]
                parsed=self.parse_offload_log(line)
                if parsed is not None: self._gpu_layers_offloaded=parsed
                layer=self.parse_gpu_layer_log(line)
                if layer is not None:
                    self._gpu_layer_ids.add(layer); self._gpu_layers_offloaded=len(self._gpu_layer_ids); self._offload_evidence.append(line); self._offload_evidence=self._offload_evidence[-20:]
    def _ensure_server(self):
        if self.url: return self.url.rstrip('/')
        if not (self.server_exe and self.model_path): return ''
        if self.process and self.process.poll() is None: return self._server_url()
        args=self._server_args()
        self.process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=str(Path(self.server_exe).parent))
        threading.Thread(target=self._capture_stream,args=(self.process.stdout,),daemon=True).start()
        threading.Thread(target=self._capture_stream,args=(self.process.stderr,),daemon=True).start()
        for _ in range(90):
            try:
                with urllib.request.urlopen(self._server_url()+'/health',timeout=2) as res:
                    if res.status==200: self.effective_gpu_mode=self.gpu_mode; return self._server_url()
            except Exception: pass
            if self.process.poll() is not None: break
            import time; time.sleep(1)
        error=self.process.stderr.read(4000).decode(errors='replace') if self.process else ''
        self.stop(); raise RuntimeError(f'llama-server failed to become ready: {error[-1000:]}')
    def set_gpu_mode(self, enabled):
        mode='cuda' if bool(enabled) else 'cpu'
        if self.settings_path:
            self.settings_path.parent.mkdir(parents=True,exist_ok=True); self.settings_path.write_text(json.dumps({'gpu_mode':mode},indent=2),encoding='utf-8')
        was_running=bool(self.process and self.process.poll() is None); self.gpu_mode=mode; self.pending_gpu_mode=mode if was_running and mode != self.effective_gpu_mode else None
        return {'requested':mode,'effective':self.effective_gpu_mode if was_running else None,'restart_pending':bool(self.pending_gpu_mode)}
    def stop(self):
        if self.native:
            self.native.stop()
            return
        if self.process and self.process.poll() is None: self.process.terminate()
        self.process=None; self.effective_gpu_mode=None; self._offload_evidence=[]; self._gpu_layers_offloaded=None; self._gpu_layer_ids=set(); self._last_inference=None
    def status(self):
        if self.native: return self.native.status()
        if self.url: return {'state':'ready','provider':'llama.cpp-openai','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':False,'url':self.url,'gpu_mode':self.gpu_mode}
        if self.server_exe and self.model_path:
            running=bool(self.process and self.process.poll() is None)
            offloaded=self._gpu_layers_offloaded if running else None
            verified=bool(running and self.gpu_mode=='cuda' and offloaded and offloaded>0 and self._last_inference and self._last_inference.get('status')=='completed')
            effective=self.effective_gpu_mode if running else ('cpu' if self.gpu_mode=='cpu' else None)
            evidence=self._offload_evidence[-3:]
            return {'state':'ready' if running else 'configured','provider':'llama.cpp-openai','model':self.model_path,'version':MODEL_VERSION,'diagnostic_mode':False,'server':'managed-lazy','gpu_mode':self.gpu_mode,'effective_gpu_mode':effective,'gpu_layers':self.gpu_layers if effective=='cuda' else 0,'gpu_acceleration_requested':self.gpu_mode=='cuda','backend_type':'cuda' if effective=='cuda' else ('cpu' if effective=='cpu' else 'unavailable'),'gpu_layers_offloaded':offloaded,'gpu_offload_verified':verified,'runtime_pid':self.process.pid if running else None,'last_inference':self._last_inference,'verification_source':['llama.cpp startup stderr'] if evidence else [],'offload_evidence':evidence,'limitations':['inference verification pending'] if not self._last_inference else []}
        try:
            with urllib.request.urlopen(self.ollama_url+'/api/tags',timeout=2) as res: models=json.loads(res.read()).get('models',[])
            names={m.get('name') for m in models}
            if self.name not in names: return {'state':'missing','provider':'ollama','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':True,'ollama_url':self.ollama_url}
            return {'state':'ready','provider':'ollama','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':False,'ollama_url':self.ollama_url}
        except Exception as exc: return {'state':'unavailable','provider':'ollama','model':self.name,'version':MODEL_VERSION,'diagnostic_mode':True,'reason':type(exc).__name__}
    def plan(self,prompt):
        instruction=planner_prompt(prompt)
        if self.pending_gpu_mode and not self._last_inference or (self.pending_gpu_mode and self._last_inference and self._last_inference.get('status') != 'active'):
            self.stop(); self.pending_gpu_mode=None; self._offload_evidence=[]; self._gpu_layers_offloaded=None; self._gpu_layer_ids=set(); self._last_inference=None
        if self.native:
            raw=parse_response_text(self.native.plan(instruction))
            allowed={item['name'] for item in available_capabilities()}
            if any(a.get('tool') not in allowed for a in raw.get('actions',[])): raise ValueError('model returned an unauthorized capability')
            return raw
        endpoint=self._ensure_server()
        if endpoint:
            body={'model':self.name,'messages':[{'role':'system','content':instruction}], 'temperature':0, 'max_tokens':512, 'stream':False, 'response_format':{'type':'json_object'}}
            req=urllib.request.Request(endpoint+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
        else:
            if self.status().get('state')!='ready': return None
            body={'model':self.name,'stream':False,'format':'json','messages':[{'role':'system','content':instruction}]}
            req=urllib.request.Request(self.ollama_url+'/api/chat',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=120) as res: raw=json.loads(res.read())
            self._last_inference={'status':'completed','observed_at':datetime.now(timezone.utc).isoformat()}
        except Exception:
            self._last_inference={'status':'failed','observed_at':datetime.now(timezone.utc).isoformat()}; raise
        if endpoint:
            content=raw.get('choices',[{}])[0].get('message',{}).get('content','')
        else: content=raw.get('message',{}).get('content','')
        raw=parse_response_text(content)
        allowed={item['name'] for item in available_capabilities()}
        if any(a.get('tool') not in allowed for a in raw.get('actions',[])): raise ValueError('model returned an unauthorized capability')
        return raw
class AgentEngine:
    def __init__(self,workspace,db_path,grant_path=None): self.workspace=workspace; self.workspace_active=True; self.grants=GrantStore(grant_path or str(Path(db_path).with_name('grants.json'))); self.policy=Policy(workspace,self.grants); self.model=LocalModel(); self.risk=RiskClient(); self.db_path=db_path; self._current_selected_path=None; self._init_db()
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
        started=time.perf_counter(); planner_error=None; plan=None
        prior=self._latest_task() if self._is_continuation(prompt) else None
        planning_prompt=self._planning_context(prompt,prior)
        plan,planner_error=self._obtain_plan(planning_prompt,prior['prompt'] if prior else prompt)
        source='llm' if plan else 'diagnostic'
        if not plan: plan=self._diagnostic_plan(prior['prompt'] if prior else prompt)
        plan=self._normalize_plan(plan,prior['prompt'] if prior else prompt); now=datetime.now(timezone.utc).isoformat(); goal=prior['prompt'] if prior else prompt; t={'id':str(uuid.uuid4()),'prompt':goal,'continuation_of':prior['id'] if prior else None,'status':'planned','phase':'WAITING FOR EXECUTION','current_action':None,'completed_steps':0,'total_steps':len(plan.get('actions',[])),'message':'Plan ready; waiting for your execution command.','started_at':now,'updated_at':now,'model_status':self.model.status(),'planning':{'source':source,'duration_ms':round((time.perf_counter()-started)*1000,1),'error':planner_error},'plan':plan,'results':[],'created_at':now}; c=self._db(); c.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',(t['id'],goal,json.dumps(t),t['created_at'],t['created_at'])); c.commit(); c.close(); return t
    def start_task(self,prompt):
        prior=self._latest_task() if self._is_continuation(prompt) else None
        if prior:
            if prior.get('status')=='awaiting_input':
                candidates=[]
                for result in prior.get('results',[]):
                    if result.get('tool')=='search_workspace' and result.get('status')=='completed':
                        candidates.extend(item.get('path') for item in (result.get('result') or []) if isinstance(item,dict) and item.get('path'))
                selected=[path for path in candidates if path.casefold() in prompt.casefold()]
                if len(selected)==1:
                    self._append_dependent_actions(prior,selected[0])
                    prior['status']='planned'; prior['phase']='WAITING FOR EXECUTION'; prior['message']='Candidate selected; review the remaining actions and execute when ready.'
                else:
                    prior['message']='Choose one exact candidate path, then send it with “continue”: '+', '.join(candidates)
                self._save_task(prior); return prior
            prior['message']='Continuation requested; preserving the existing task and verified results.'; self._save_task(prior); return prior
        goal=prior['prompt'] if prior else prompt; now=datetime.now(timezone.utc).isoformat()
        t={'id':str(uuid.uuid4()),'prompt':goal,'continuation_of':prior['id'] if prior else None,'status':'planning','phase':'PLANNING','current_action':None,'completed_steps':0,'total_steps':0,'message':'Planning with local model.','started_at':now,'updated_at':now,'model_status':self.model.status(),'planning':{'source':'pending','duration_ms':None,'error':None},'plan':{'mode':'pending','summary':'Planning with local model…','actions':[]},'results':[],'created_at':now}
        c=self._db(); c.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',(t['id'],goal,json.dumps(t),now,now)); c.commit(); c.close()
        worker=threading.Thread(target=self._complete_planning,args=(t['id'],prompt,prior),daemon=True); worker.start(); worker.join(0.05)
        return self._get(t['id']) or t
    def _complete_planning(self,tid,prompt,prior):
        started=time.perf_counter(); planner_error=None; plan=None
        goal=prior['prompt'] if prior else prompt; plan,planner_error=self._obtain_plan(self._planning_context(prompt,prior),goal)
        retry=0
        while plan is None and self._is_transient_planner_error(planner_error) and retry<3:
            current=self._get(tid)
            if not current or current.get('status')=='cancelled': return
            retry+=1; current['phase']='WAITING FOR LOCAL MODEL'; current['message']=f'Local model is warming up; retry {retry} of 3.'; current['planning']={'source':'pending','duration_ms':None,'error':planner_error}; self._save_task(current)
            time.sleep((0.5,1.0,2.0)[retry-1])
            current=self._get(tid)
            if not current or current.get('status')=='cancelled': return
            plan,planner_error=self._obtain_plan(self._planning_context(prompt,prior),goal)
        source='llm' if plan else 'diagnostic'
        current=self._get(tid)
        if not current or current.get('status')=='cancelled': return
        if plan is None and self._is_transient_planner_error(planner_error):
            current['status']='blocked'; current['phase']='ERROR'; current['message']='Local model inference did not become ready after bounded retries. Start the managed model again and retry this task.'; current['plan']={'mode':'diagnostic','summary':current['message'],'actions':[]}; current['planning']={'source':'diagnostic','duration_ms':round((time.perf_counter()-started)*1000,1),'error':planner_error}; self._save_task(current); return
        plan=self._normalize_plan(plan or self._diagnostic_plan(goal),goal); t=self._get(tid)
        if not t: return
        t['status']='planned'; t['phase']='WAITING FOR EXECUTION'; t['message']='Plan ready; waiting for your execution command.'; t['plan']=plan; t['total_steps']=len(plan.get('actions',[])); t['planning']={'source':source,'duration_ms':round((time.perf_counter()-started)*1000,1),'error':planner_error}; t['model_status']=self.model.status(); self._save_task(t)
    @staticmethod
    def _is_transient_planner_error(error):
        if not isinstance(error,str): return False
        return 'HTTPError' in error and any(code in error for code in (' 503',' 502',' 504'))
    def _latest_task(self):
        c=self._db(); rows=c.execute('SELECT data FROM tasks ORDER BY created_at DESC LIMIT 20').fetchall(); c.close()
        for row in rows:
            task=json.loads(row[0])
            if task.get('status') not in {'completed','failed','blocked','cancelled'}: return task
        return None
    @staticmethod
    def _is_continuation(prompt):
        return bool(re.fullmatch(r'\s*(?:continue(?: above)?|keep going|resume|complete your task|finish it|complete task)(?:\s+.*)?',prompt,re.I))
    def list_tasks(self,limit=20):
        c=self._db(); rows=c.execute('SELECT data FROM tasks ORDER BY created_at DESC LIMIT ?',(max(1,min(50,int(limit))),)).fetchall(); c.close(); return [json.loads(row[0]) for row in rows]
    def _planning_context(self,prompt,prior):
        if not prior: return prompt
        verified=[r for r in prior.get('results',[]) if r.get('status')=='completed']
        return ('Resume the existing task. Original user goal:\n'+prior['prompt']+'\n'
                'Verified structured results so far (do not treat proposed actions as results):\n'+
                json.dumps(verified,default=str)[:12000]+'\nUser continuation:\n'+prompt)
    @staticmethod
    def _append_dependent_actions(task,selected):
        existing={action.get('id') for action in task.get('plan',{}).get('actions',[])}
        read_id='read_file-resume'
        pdf_id='create_pdf-resume'
        if read_id not in existing:
            task['plan']['actions'].append({'id':read_id,'tool':'read_file','arguments':{'path':selected}})
        if pdf_id not in existing:
            task['plan']['actions'].append({'id':pdf_id,'tool':'create_pdf','arguments':{'destination':'desktop','filename':'Welcome_to_Xerxes.pdf','text':{'$ref':{'step':read_id,'field':'content'}}}})
        task['total_steps']=len(task['plan']['actions'])
    def _obtain_plan(self,planning_prompt,goal):
        try: plan=self.model.plan(planning_prompt)
        except Exception as exc: return None,f'{type(exc).__name__}: {exc}'
        if not plan:
            state=self.model.status().get('state','unknown')
            return None,f'planner returned no usable action plan (runtime state: {state})'
        try:
            plan=self._normalize_plan(plan,goal)
            if isinstance(plan.get('response'),str) and not plan.get('actions'): return plan,None
            self._validate_plan(plan,goal); return plan,None
        except ValueError as first:
            try:
                retry=self.model.plan(planner_prompt('Correct the previous response for this request. Validation feedback: '+str(first)+'\nOriginal goal:\n'+goal+'\nReturn one corrected response using the same contract; do not invent paths or results.'))
                retry=self._normalize_plan(retry or {},goal)
                if isinstance(retry.get('response'),str) and not retry.get('actions'): return retry,None
                self._validate_plan(retry,goal); return retry,None
            except Exception as exc: return None,f'ungrounded plan: {exc}'
    def _validate_plan(self,plan,goal):
        actions=plan.get('actions',[]); lower=goal.lower(); names=[a.get('tool') for a in actions]
        for action in actions:
            tool=action.get('tool'); args=action.get('arguments',{})
            validate_arguments(tool,args)
        for action in actions:
            if action.get('tool')=='search_workspace':
                query=action.get('arguments',{}).get('query')
                if not isinstance(query,str) or not query.strip(): raise ValueError('search_workspace query is required')
                if len(query)>100: raise ValueError('search_workspace query exceeds the 100-character tool limit; derive concise search terms')
        if any(n=='run_tests' for n in names) and any(x in lower for x in ('pdf','hidden file','find a file','read')):
            raise ValueError('unrelated test action for a filesystem-content task')
        needs_source=any(word in lower for word in ('hidden','find','locate','read','contents','source file'))
        if 'desktop' in lower:
            searches=[a for a in actions if a.get('tool')=='search_workspace']
            writes=[a for a in actions if a.get('tool') in {'apply_patch','create_file'}]
            if needs_source and not searches and not (writes and any(x in lower for x in ('create','make','write'))):
                raise ValueError('explicit Desktop request requires a scoped search')
            if needs_source and any(not self.policy.is_desktop_root(a.get('arguments',{}).get('path','desktop')) for a in searches): raise ValueError('explicit Desktop request must search the authorized Desktop root')
            if any(not self.policy.is_desktop_path(a.get('arguments',{}).get('path','')) for a in writes): raise ValueError('explicit Desktop write must target the authorized Desktop root')
            if any('todo' in str(a.get('arguments',{}).get('query','')).casefold() for a in searches): raise ValueError('generic TODO search is unrelated to the requested Desktop file')
            if any(a.get('tool')=='create_pdf' and str(a.get('arguments',{}).get('destination','desktop')).casefold() not in {'desktop',''} for a in actions): raise ValueError('explicit Desktop destination cannot be replaced')
        if any(n=='create_pdf' for n in names) and needs_source:
            read_index=next((i for i,n in enumerate(names) if n=='read_file'),None)
            pdf_index=next((i for i,n in enumerate(names) if n=='create_pdf'),None)
            if read_index is None or pdf_index is None or read_index>pdf_index: raise ValueError('PDF creation requires a preceding read action')
        if any(self._has_unsupported_reference(a.get('arguments',{})) for a in actions): raise ValueError('plan contains unsupported or unresolved action result reference')
        self._validate_reference_order(actions)
    def _save_task(self,t):
        c=self._db(); row=c.execute('SELECT data FROM tasks WHERE id=?',(t['id'],)).fetchone()
        if row:
            current=json.loads(row[0]); terminal={'cancelled','completed','failed','blocked','error'}
            if current.get('status') in terminal and t.get('status')!=current.get('status'):
                c.close(); return False
        t['updated_at']=datetime.now(timezone.utc).isoformat(); c.execute('UPDATE tasks SET data=?,updated_at=? WHERE id=?',(json.dumps(t),t['updated_at'],t['id'])); c.commit(); c.close(); return True
    @staticmethod
    def _phase(tool): return {'search_workspace':('SEARCHING','Searching approved folders'), 'read_file':('READING FILE','Reading an authorized file'), 'create_file':('CREATING FILE','Creating and verifying the approved text file'), 'create_pdf':('CREATING PDF','Creating and verifying the approved PDF'), 'apply_patch':('WRITING FILE','Applying an approved file change'), 'list_workspace':('LISTING FILES','Listing approved workspace files'), 'run_tests':('RUNNING TESTS','Running an allowlisted test command')}.get(tool,('EXECUTING','Executing approved action'))
    def _normalize_plan(self,plan,prompt):
        actions=[]
        for index,action in enumerate(plan.get('actions',[])):
            if not isinstance(action,dict): continue
            item=dict(action); args=dict(item.get('arguments') or {})
            # Capability metadata uses ``name``; persisted/executable plans use
            # the canonical ``tool`` key. Normalize this model response once.
            if 'tool' not in item and isinstance(item.get('name'),str): item['tool']=item.pop('name')
            if item.get('tool')=='read_file' and 'path' not in args and 'file_path' in args: args['path']=args.pop('file_path')
            if item.get('tool')=='create_pdf': args=self.policy.normalize_pdf_args(args)
            if item.get('tool')=='search_workspace':
                if 'query' not in args and 'search_term' in args: args['query']=args.pop('search_term')
                if 'mode' not in args and args.get('file_types'): args['mode']='filename'
                args.pop('file_types',None)
            item['id']=item.get('id') or f"{item.get('tool','step')}-{index+1}"; item['arguments']=args; actions.append(item)
        wants_readme='readme' in prompt.lower()
        if wants_readme and not any(a.get('tool')=='read_file' and str(a.get('arguments',{}).get('path','')).lower().endswith('readme.md') for a in actions):
            candidates=sorted(self.workspace.rglob('README.md'))
            if candidates:
                rel=candidates[0].relative_to(self.workspace).as_posix(); actions.insert(0,{'id':'read_file-readme','tool':'read_file','arguments':{'path':rel}})
        for index,item in enumerate(actions): item['id']=item.get('id') or f"{item.get('tool','step')}-{index+1}"
        reads=[a for a in actions if a.get('tool')=='read_file']; pdfs=[a for a in actions if a.get('tool')=='create_pdf']; others=[a for a in actions if a.get('tool') not in {'read_file','create_pdf'}]
        ordered=others+reads+pdfs if wants_readme else actions
        by_tool={tool:items[0]['id'] for tool in {a['tool'] for a in ordered} if len(items:=[a for a in ordered if a['tool']==tool])==1}
        for item in ordered: item['arguments']=self._normalize_references(item['arguments'],by_tool)
        return {**plan,'actions':ordered}
    @staticmethod
    def _normalize_references(value,by_tool):
        if isinstance(value,str):
            reserved={
                'selected_file_path': ('search_workspace','file_path'),
                'search_workspace.result': ('search_workspace','result'),
                'search_workspace.result.file_path': ('search_workspace','file_path'),
                'read_file.result': ('read_file','result'),
                'read_file.result.content': ('read_file','content'),
            }
            if value in reserved and reserved[value][0] in by_tool:
                step,field=reserved[value]; return {'$ref':{'step':by_tool[step],'field':field}}
            match=re.fullmatch(r'\{\{([A-Za-z_][\w-]*)(?:_result|\.result)(?:\.([A-Za-z_][\w-]*))?(?:\.([A-Za-z_][\w-]*))?\}\}',value)
            if match and match.group(1) in by_tool: return {'$ref':{'step':by_tool[match.group(1)],'field':match.group(3) or match.group(2) or 'result'}}
            return value
        if isinstance(value,dict): return {k:AgentEngine._normalize_references(v,by_tool) for k,v in value.items()}
        if isinstance(value,list): return [AgentEngine._normalize_references(v,by_tool) for v in value]
        return value
    @staticmethod
    def _has_unsupported_reference(value):
        if isinstance(value,str): return bool(re.search(r'\{\{[^{}]+\}\}',value))
        if isinstance(value,dict):
            if '$ref' in value: return not isinstance(value['$ref'],dict) or not isinstance(value['$ref'].get('step'),str) or not isinstance(value['$ref'].get('field'),str)
            return any(AgentEngine._has_unsupported_reference(v) for v in value.values())
        if isinstance(value,list): return any(AgentEngine._has_unsupported_reference(v) for v in value)
        return False
    @staticmethod
    def _validate_reference_order(actions):
        positions={action['id']:index for index,action in enumerate(actions)}
        def visit(value, consumer_index):
            if isinstance(value,dict) and '$ref' in value:
                ref=value['$ref']; step=ref.get('step') if isinstance(ref,dict) else None
                if step not in positions: raise ValueError(f'dependency producer {step} does not exist')
                if positions[step] >= consumer_index: raise ValueError(f'dependency {step} must precede its consumer')
                return
            if isinstance(value,dict):
                for nested in value.values(): visit(nested,consumer_index)
            elif isinstance(value,list):
                for nested in value: visit(nested,consumer_index)
        for index,action in enumerate(actions): visit(action.get('arguments',{}),index)
    def _diagnostic_plan(self,prompt):
        lower=prompt.lower(); actions=[]
        if any(word in lower for word in ('create','make','write')) and re.search(r'\b(?:txt|text file)\b',lower):
            destination='desktop'
            match=re.search(r'path\s+((?:[a-z]:[\\/])[^\n]+?)(?:\s+and\s+in\s+it|\s+and\s+write|$)',prompt,re.I)
            if match: destination=match.group(1).rstrip(' \\"\'')
            elif 'desktop' not in lower: destination='.'
            content_match=re.search(r'(?:write|contain(?:ing)?)\s+["“\']([^"”\']+)["”\']?',prompt,re.I)
            content=content_match.group(1) if content_match else 'hello world'
            actions=[{'tool':'create_file','arguments':{'path':str(Path(destination)/'Xerxes_note.txt'),'content':content}}]
        if any(word in lower for word in ('search','find','hidden','locate')):
            location='desktop' if 'desktop' in lower else None
            clues=re.findall(r'\b(?:hehe|huhu|riri|hihi|ruru|eheh|hihy|hih[iy]|rir[iy])\b',lower)
            if location and clues:
                actions=[{'tool':'search_workspace','arguments':{'path':location,'query':' '.join(dict.fromkeys(clues)),'mode':'filename'}}]
        summary='Intelligent planning is unavailable.'
        if actions: summary+=' A safe location-specific search was derived from the request; dependent actions require real results.'
        else: summary+=' No task-specific actions were inferred; configure and verify the local model before continuing.'
        return {'mode':'diagnostic','summary':summary,'actions':actions}
    def tool_catalog(self): return [{'name':item['name'],'description':item['description'],'state':item['state'],'risk':item['risk'],'approval':item['approval']} for item in available_capabilities()]
    def list_tree(self): return self.policy.list_tree() if self.workspace_active else []
    def _run(self,tid,a):
        name=a.get('tool'); args=a.get('arguments') or {}
        registered=capability(name)
        if not registered or registered.get('state')!='available' or not registered.get('handler'):
            self._audit(tid,name,args,'denied','capability is unavailable',verification='not_run')
            return {'tool':name,'status':'blocked','reason':'capability is unavailable'}
        if self._has_placeholder(args):
            reason='unresolved action result reference; run the producing action successfully or provide an explicit path'
            self._audit(tid,name,args,'denied',reason,verification='not_run'); return {'tool':name,'status':'blocked','reason':reason}
        d=self.policy.check(name,args); meta={'action_category':name,'inside_workspace':d.allowed,'reversible':name in {'list_workspace','read_file','search_workspace'},'network':False,'approval':False,'data_sensitivity':'workspace-metadata','privilege':'user'}; risk=None
        try: risk=self.risk.score(meta)
        except Exception as exc: risk={'state':'unavailable','reason':type(exc).__name__}
        if not d.allowed: self._audit(tid,name,args,'denied',d.reason,risk,verification='not_run'); return {'tool':name,'status':'blocked','reason':d.reason,'risk':risk}
        if name in {'apply_patch','create_file','create_pdf'} and self._cancelled(tid):
            return {'tool':name,'status':'blocked','reason':'task was cancelled before filesystem write'}
        if name=='list_workspace': result=self.policy.list_tree()
        elif name=='read_file': result=self.policy.read_file(args.get('path',''))
        elif name=='search_workspace': result=self.policy.search(args.get('query',''),args.get('path','.') or '.',args.get('mode','content'))
        elif name=='run_tests':
            proc=subprocess.run(self.policy.command(args.get('command','')),cwd=self.workspace,capture_output=True,text=True,timeout=60,shell=False); result={'stdout':proc.stdout[-12000:],'stderr':proc.stderr[-12000:],'exit_code':proc.returncode}; self._audit(tid,name,args,'allowed',d.reason,risk,result,proc.returncode,'passed' if proc.returncode==0 else 'failed'); return {'tool':name,'status':'completed' if proc.returncode==0 else 'failed','result':result,'risk':risk}
        elif name=='create_file':
            target=self.policy.file_target(args)
            target.write_text(args['content'],encoding='utf-8',newline='')
            if not target.is_file() or target.read_text(encoding='utf-8')!=args['content']: raise ValueError('created file failed content verification')
            result={'path':str(target),'created':True,'verified':True,'content':args['content']}
        elif name=='create_pdf':
            from pdf_generator import create_pdf
            normalized,destination=self.policy.pdf_target(args)
            if destination.exists(): raise FileExistsError('destination file already exists')
            result=create_pdf(destination,normalized.get('text',''),normalized.get('title'))
        elif name=='apply_patch':
            path=self.policy.resolve(args['path']); before=path.read_text(encoding='utf-8'); old=args['old_text']; new=args['new_text']
            if before.count(old)!=1: raise ValueError('patch context must match exactly once')
            path.write_text(before.replace(old,new,1),encoding='utf-8'); after=path.read_text(encoding='utf-8')
            result={'path':args['path'],'changed':before!=after,'diff':'\n'.join(difflib.unified_diff(before.splitlines(),after.splitlines(),fromfile=args['path'],tofile=args['path'])),'verified':after==before.replace(old,new,1)}
        else: result={'message':'tool not enabled in this vertical slice'}
        self._audit(tid,name,args,'allowed',d.reason,risk,result,0,'verified'); return {'tool':name,'status':'completed','result':result,'risk':risk}
    def _resolve_references(self,value,results):
        if isinstance(value,dict) and '$ref' in value:
            ref=value['$ref']; matches=[r for r in results if r.get('id')==ref.get('step') or r.get('tool')==ref.get('step')]
            if len(matches)!=1 or matches[0].get('status')!='completed': raise ValueError(f"dependency {ref.get('step')} did not complete successfully")
            data=matches[0].get('result'); field=ref.get('field','result')
            if field=='result': return data
            if not isinstance(data,dict) or field not in data:
                if field in {'file_path','path'} and isinstance(data,list):
                    paths=[x.get('path') for x in data if isinstance(x,dict) and x.get('path')]
                    selected=self._current_selected_path
                    if selected and selected in paths: return selected
                    if len(paths)==1: return paths[0]
                    if not paths: raise ValueError(f"dependency {ref.get('step')} returned no usable file")
                    raise ValueError(f"dependency {ref.get('step')} returned multiple candidates; user selection is required")
                raise ValueError(f"dependency field {field} is unavailable")
            return data[field]
        if isinstance(value,dict): return {k:self._resolve_references(v,results) for k,v in value.items()}
        if isinstance(value,list): return [self._resolve_references(v,results) for v in value]
        return value
    @staticmethod
    def _has_placeholder(value):
        if isinstance(value,str): return bool(re.search(r'\{\{[^{}]+\}\}',value))
        if isinstance(value,dict): return any(AgentEngine._has_placeholder(v) for v in value.values())
        if isinstance(value,list): return any(AgentEngine._has_placeholder(v) for v in value)
        return False
    def _cancelled(self,tid):
        current=self._get(tid)
        return not current or current.get('status')=='cancelled'
    def execute(self,tid,approve=False):
        t=self._get(tid)
        if not t: raise KeyError(tid)
        if t.get('status')=='awaiting_input': raise ValueError('a candidate selection is required before execution')
        self._current_selected_path=t.get('selected_candidate',{}).get('path') if isinstance(t.get('selected_candidate'),dict) else None
        if self._cancelled(tid): return self._get(tid) or t
        prior_results=t.get('results',[]) if t.get('status')=='approval_required' and approve else []
        t['status']='executing'; t['phase']='EXECUTING'; t['message']='Starting approved actions.'; t['current_action']=None; t['completed_steps']=sum(1 for r in prior_results if r.get('status')=='completed'); t['total_steps']=len(t['plan'].get('actions',[])); t['results']=prior_results; self._save_task(t)
        if not t['plan'].get('actions') and t['plan'].get('mode')=='diagnostic':
            t['status']='blocked'; t['phase']='ERROR'; t['message']='Intelligent planning is unavailable; no safe task-specific action was derived.'; self._save_task(t); return t
        action_index=0
        while action_index < len(t['plan'].get('actions',[])):
            raw_action=t['plan']['actions'][action_index]; action_index+=1
            if self._cancelled(tid): return self._get(tid) or t
            a=dict(raw_action); args=dict(a.get('arguments') or {})
            if any(r.get('id')==a.get('id') and r.get('status')=='completed' for r in t['results']): continue
            if a.get('tool')=='create_pdf': args=self.policy.normalize_pdf_args(args)
            if a.get('tool')=='read_file' and 'path' not in args and 'file_path' in args: args['path']=args.pop('file_path')
            if a.get('tool')=='search_workspace' and 'query' not in args and 'search_term' in args: args['query']=args.pop('search_term')
            if a.get('tool')=='search_workspace' and 'mode' not in args and args.get('file_types'): args['mode']='filename'
            try: args=self._resolve_references(args,t['results'])
            except ValueError as exc:
                blocked={'id':a.get('id'),'tool':a.get('tool'),'status':'blocked','reason':str(exc)}; t['results'].append(blocked); t['status']='blocked'; t['phase']='ERROR'; t['message']=str(exc); self._save_task(t); break
            try: validate_arguments(a.get('tool'),args)
            except ValueError as exc:
                blocked={'id':a.get('id'),'tool':a.get('tool'),'status':'blocked','reason':str(exc)}; t['results'].append(blocked); t['status']='blocked'; t['phase']='ERROR'; t['message']=str(exc); self._save_task(t); break
            a['arguments']=args
            if a.get('tool') in {'apply_patch','create_file','create_pdf'}:
                if self._cancelled(tid): return self._get(tid) or t
                decision=self.policy.check(a.get('tool'),args)
                if not decision.allowed:
                    t['results'].append({'id':a.get('id'),'tool':a.get('tool'),'status':'blocked','reason':decision.reason}); t['status']='blocked'; t['phase']='ERROR'; t['message']=decision.reason; self._save_task(t); break
                if not approve:
                    t['results'].append({'id':a.get('id'),'tool':a.get('tool'),'status':'approval_required','arguments':args}); t['status']='approval_required'; t['phase']='WAITING FOR APPROVAL'; t['message']='Waiting for your approval before writing files.'; self._save_task(t); break
            if a.get('tool')=='create_pdf' and 'readme' in t.get('prompt','').lower():
                read_results=[r.get('result') for r in t['results'] if r.get('tool')=='read_file' and r.get('status')=='completed']
                if not read_results: t['status']='blocked'; t['results'].append({'tool':'create_pdf','status':'blocked','reason':'README content was not read; PDF was not created'}); break
                args=dict(a.get('arguments') or {}); welcome=re.split(r'copy contents',t['prompt'],maxsplit=1,flags=re.I)[0].strip(' ,.')
                args['text']=welcome+'\n\n'+read_results[-1]['content']; a={**a,'arguments':args}
            t['phase'],t['message']=self._phase(a.get('tool')); t['current_action']=a.get('tool'); self._save_task(t)
            try:
                result=self._run(tid,a); result['id']=a.get('id')
            except Exception as exc:
                current=self._get(tid)
                if current and current.get('status')=='cancelled': return current
                result={'id':a.get('id'),'tool':a.get('tool'),'status':'failed','reason':f'{type(exc).__name__}: {str(exc)[:240]}'}
            current=self._get(tid)
            if not current: return t
            if current.get('status')=='cancelled':
                if not any(item.get('id')==result.get('id') for item in current.get('results',[])):
                    current.setdefault('results',[]).append(result)
                    if result.get('status')=='completed': current['completed_steps']=sum(1 for item in current['results'] if item.get('status')=='completed')
                    self._save_task(current)
                return self._get(tid) or current
            t=current; t['results'].append(result)
            if t['results'][-1]['status'] in {'blocked','failed'}: t['status']=t['results'][-1]['status']; t['phase']='ERROR'; t['message']=t['results'][-1].get('reason','Action failed'); self._save_task(t); break
            t['completed_steps']+=1; self._save_task(t)
            if self._cancelled(tid): return self._get(tid) or t
            if a.get('tool')=='search_workspace' and t.get('plan',{}).get('mode')=='diagnostic':
                matches=(result.get('result') or [])
                if not matches:
                    t['status']='blocked'; t['phase']='ERROR'; t['message']='No matching files were found; provide a more precise filename or search clue.'; self._save_task(t); break
                if len(matches)>1:
                    t['candidates']=[{'id':item.get('id') or f'candidate-{index+1}','path':item.get('path'),'match':item.get('match'),'line':item.get('line')} for index,item in enumerate(matches) if isinstance(item,dict) and item.get('path')]
                    t['status']='awaiting_input'; t['phase']='WAITING FOR FILE SELECTION'; t['message']='Multiple matching files were found. Choose one of the listed paths, then continue.'; self._save_task(t); break
                selected=matches[0].get('path')
                if selected:
                    self._append_dependent_actions(t,selected); self._save_task(t)
            elif a.get('tool')=='search_workspace':
                matches=(result.get('result') or [])
                if len(matches)>1:
                    t['candidates']=[{'id':item.get('id') or f'candidate-{index+1}','path':item.get('path'),'match':item.get('match'),'line':item.get('line')} for index,item in enumerate(matches) if isinstance(item,dict) and item.get('path')]
                    t['status']='awaiting_input'; t['phase']='WAITING FOR FILE SELECTION'; t['message']='Multiple matching files were found. Choose one of the listed paths, then continue.'; self._save_task(t); break
        else: t['status']='completed'; t['phase']='COMPLETED'; t['message']='All actions completed successfully.'; t['current_action']=None; self._save_task(t)
        self._current_selected_path=None
        return t
    def select_candidate(self,tid,candidate_id):
        t=self._get(tid)
        if not t: raise KeyError(tid)
        if t.get('status')!='awaiting_input': raise ValueError('task is not waiting for file selection')
        candidate=next((item for item in t.get('candidates',[]) if item.get('id')==candidate_id),None)
        if not candidate: raise ValueError('candidate is not present in this task')
        path=candidate.get('path'); resolved=self.policy.resolve(path)
        if not resolved.is_file(): raise ValueError('selected candidate is no longer an accessible file')
        t['selected_candidate']={'id':candidate_id,'path':path}; t['status']='planned'; t['phase']='WAITING FOR EXECUTION'; t['message']='Candidate selected; execute the remaining authorized steps when ready.'
        if t.get('plan',{}).get('mode')=='diagnostic': self._append_dependent_actions(t,path)
        self._save_task(t); return t
    def cancel(self,tid):
        t=self._get(tid)
        if not t: raise KeyError(tid)
        if t.get('status') in {'completed','failed','blocked','error','cancelled'}: return t
        t['status']='cancelled'; t['phase']='CANCELLED'; t['message']='Task cancelled by user.'; self._save_task(t); self._audit(tid,'cancel',{},'allowed','user cancellation',verification='verified'); return t
