import os, platform
from pathlib import Path
from flask import Flask, jsonify, request
from flask_cors import CORS
from agent import AgentEngine
from capabilities import catalog

app=Flask(__name__); app.config['MAX_CONTENT_LENGTH']=512*1024
CORS(app, origins=os.environ.get('XERXES_CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','))
DB_PATH=os.environ.get('XERXES_DATABASE',str(Path(__file__).resolve().parent/'xerxes.db'))
os.environ.setdefault('XERXES_RUNTIME_SETTINGS',str(Path(DB_PATH).with_name('runtime-settings.json')))
configured_workspace=os.environ.get('XERXES_WORKSPACE','').strip()
WORKSPACE=Path(configured_workspace or Path(__file__).resolve().parents[1]).resolve(); ACTIVE_WORKSPACE=WORKSPACE if configured_workspace else None; engine=AgentEngine(WORKSPACE,DB_PATH); engine.workspace_active=bool(configured_workspace)
MAX_TASK_PROMPT=32*1024
def err(message,code=400): return jsonify({'error':{'message':message}}),code
@app.get('/api/v1/health')
def health(): return jsonify({'status':'ok','product':'xerxes','architecture':platform.machine(),'workspace':str(ACTIVE_WORKSPACE) if ACTIVE_WORKSPACE else None,'model':engine.model.status(),'risk_service':engine.risk.status()})
@app.get('/api/v1/settings')
def settings():
    data=engine.settings(); data['workspace']=str(ACTIVE_WORKSPACE) if ACTIVE_WORKSPACE else None; return jsonify(data)
@app.post('/api/v1/runtime/gpu')
def set_gpu():
    payload=request.get_json(silent=True) or {}
    if not isinstance(payload.get('enabled'),bool): return err('enabled must be boolean')
    return jsonify(engine.model.set_gpu_mode(payload['enabled']))
@app.get('/api/v1/audit')
def audit(): return jsonify({'items':engine.audit(min(100,max(1,int(request.args.get('limit',50)))) )})
@app.get('/api/v1/workspace/tree')
def tree():
    try:
        if ACTIVE_WORKSPACE is None: return jsonify({'root':None,'items':[]})
        return jsonify({'root':str(ACTIVE_WORKSPACE),'items':engine.list_tree()})
    except ValueError as exc: return err(str(exc),403)
@app.post('/api/v1/workspace')
def set_workspace():
    token=os.environ.get('XERXES_GRANT_TOKEN','')
    if not token or request.headers.get('X-Xerxes-Grant-Token') != token: return err('trusted workspace mediation required',403)
    try:
        candidate=Path((request.get_json(silent=True) or {}).get('path','')).resolve()
        if not candidate.is_dir(): raise ValueError('workspace folder is invalid')
        global ACTIVE_WORKSPACE
        engine.workspace=candidate; engine.policy.root=candidate; engine.workspace_active=True; ACTIVE_WORKSPACE=candidate
        return jsonify({'workspace':str(candidate)})
    except (ValueError,OSError) as exc: return err(str(exc),400)
@app.post('/api/v1/tasks')
def create_task():
    payload=request.get_json(silent=True) or {}
    prompt=payload.get('prompt','')
    if not isinstance(prompt,str): return err('prompt must be text')
    prompt=prompt.strip()
    if not prompt: return err('prompt is required')
    if len(prompt)>MAX_TASK_PROMPT: return err(f'prompt exceeds the {MAX_TASK_PROMPT}-character task limit')
    try: return jsonify(engine.start_task(prompt)),202
    except (RuntimeError, ValueError) as exc: return err(str(exc),503 if isinstance(exc,RuntimeError) else 422)
@app.get('/api/v1/tasks/<task_id>')
def get_task(task_id):
    task=engine.get_task(task_id); return jsonify(task) if task else err('task not found',404)
@app.get('/api/v1/tasks')
def list_tasks():
    try: return jsonify({'items':engine.list_tasks(request.args.get('limit',20))})
    except (TypeError,ValueError): return err('invalid task limit')
@app.post('/api/v1/tasks/<task_id>/execute')
def execute_task(task_id):
    try: return jsonify(engine.execute(task_id,bool((request.get_json(silent=True) or {}).get('approve',False))))
    except KeyError: return err('task not found',404)
    except ValueError as exc: return err(str(exc),403)
@app.post('/api/v1/tasks/<task_id>/cancel')
def cancel_task(task_id):
    try: return jsonify(engine.cancel(task_id))
    except KeyError: return err('task not found',404)
@app.post('/api/v1/tasks/<task_id>/select')
def select_task_candidate(task_id):
    try:
        payload=request.get_json(silent=True) or {}
        return jsonify(engine.select_candidate(task_id,payload.get('candidate_id','')))
    except KeyError: return err('task not found',404)
    except ValueError as exc: return err(str(exc),403)
@app.get('/api/v1/tools')
def tools(): return jsonify({'tools':engine.tool_catalog()})
@app.get('/api/v1/capabilities')
def capabilities(): return jsonify({'capabilities':catalog(),'workspace':str(ACTIVE_WORKSPACE) if ACTIVE_WORKSPACE else None,'grants':engine.grants.list()})
@app.get('/api/v1/grants')
def grants(): return jsonify({'grants':engine.grants.list()})
@app.post('/api/v1/grants')
def add_grant():
    token=os.environ.get('XERXES_GRANT_TOKEN','');
    if not token or request.headers.get('X-Xerxes-Grant-Token') != token: return err('trusted grant mediation required',403)
    try: return jsonify(engine.grants.add((request.get_json(silent=True) or {}).get('path',''))),201
    except (ValueError,OSError) as exc: return err(str(exc),400)
@app.delete('/api/v1/grants/<grant_id>')
def revoke_grant(grant_id):
    token=os.environ.get('XERXES_GRANT_TOKEN','');
    if not token or request.headers.get('X-Xerxes-Grant-Token') != token: return err('trusted grant mediation required',403)
    try: engine.grants.revoke(grant_id); return jsonify({'revoked':grant_id})
    except KeyError: return err('grant not found',404)
if __name__=='__main__': app.run(host='127.0.0.1',port=int(os.environ.get('PORT',5000)),debug=False,threaded=True)
