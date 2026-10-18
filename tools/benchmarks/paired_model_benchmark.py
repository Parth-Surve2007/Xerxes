"""Resumable live Xerxes model comparison; writes results outside the repository."""
import json, os, subprocess, sys, tempfile, time, urllib.request, uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(os.environ['LOCALAPPDATA'])/'Xerxes'
RUNTIME=LOCAL/'runtime'/'b11717-win-cuda12-x64'/'llama-server.exe'
MODELS=[('qwen2.5-coder',LOCAL/'models'/'qwen2.5-coder-7b-instruct-q4_k_m.gguf',12),('qwen3',LOCAL/'models'/'Qwen3-8B-Q4_K_M.gguf',8)]
SCENARIOS=[
 ('conversation-hello','Hello!'),('conversation-binary','Explain binary search in simple terms and give a small example.'),
 ('conversation-capabilities','What kinds of tasks can you help me do in this workspace?'),
 ('create-file','Create a text file named benchmark-note.txt containing exactly: Xerxes benchmark fixture.'),
 ('search-name','Find the nested file named mimi.txt, benchmark, fixture.'),
 ('search-read','Find the unique mimi.txt file and read its contents.'),
 ('generated-pdf','Create a PDF explaining binary search briefly.'),
 ('source-pdf','Find mimi.txt, read it, and create a PDF containing its exact contents.'),
 ('zero-match','Find the file named definitely-no-such-xerxes-file.txt.'),
 ('multiple-candidates','Find the matching mimi files and let me choose one.'),
]

def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as r: return r.status==200
    except Exception: return False

def main():
    run=LOCAL/'benchmarks'/('paired-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]); run.mkdir(parents=True)
    results={'run':str(run),'runtime':str(RUNTIME),'scenarios':[x[0] for x in SCENARIOS],'models':{},'deterministic_guards':{'status':'not_run'}}
    checkpoint={'completed':[],'run':str(run)}; (run/'checkpoint.json').write_text(json.dumps(checkpoint,indent=2),encoding='utf-8')
    for model,model_path,layers in MODELS:
        if not model_path.exists(): raise RuntimeError(f'missing model: {model_path}')
        workspace=Path(tempfile.mkdtemp(prefix='xerxes-bench-')); (workspace/'nested').mkdir(); (workspace/'nested'/'mimi.txt').write_text('MIMI-SOURCE-9F2\n',encoding='utf-8'); (workspace/'nested'/'mimi-copy.txt').write_text('SECOND-CANDIDATE\n',encoding='utf-8'); (workspace/'empty').mkdir()
        port=56323; cmd=[str(RUNTIME),'-m',str(model_path),'--host','127.0.0.1','--port',str(port),'-c','2048','-ngl',str(layers),'-n','128','--reasoning','off','-lv','2']
        env=os.environ.copy(); env.update({'XERXES_LOCAL_MODEL_URL':f'http://127.0.0.1:{port}','XERXES_LOCAL_MODEL':model,'PYTHONIOENCODING':'utf-8'})
        proc=subprocess.Popen(cmd,cwd=RUNTIME.parent,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
        try:
            started=time.perf_counter()
            while time.perf_counter()-started<120 and not health(port): time.sleep(.5)
            if not health(port): raise RuntimeError('server did not become healthy')
            sys.path.insert(0,str(ROOT/'backend')); os.environ.update(env)
            import app as app_module
            app_module.engine=app_module.AgentEngine(str(workspace),str(run/f'{model}.db'))
            model_results=[]
            for sid,prompt in SCENARIOS:
                key=f'{model}:{sid}'
                if key in checkpoint['completed']: continue
                t=time.perf_counter(); record={'model':model,'scenario':sid,'prompt':prompt,'model_path':str(model_path),'gpu_layers':layers,'context':2048,'reasoning':'off','status':'failed'}
                try:
                    task=app_module.engine.create_task(prompt); record.update({'status':'planned','planning_ms':round((time.perf_counter()-t)*1000,1),'planning':task.get('planning'),'plan':task.get('plan'),'task_id':task.get('id')})
                    if sid.startswith('conversation-'): record['success']=bool(task.get('plan',{}).get('response')) and not task.get('plan',{}).get('actions')
                    else: record['success']=bool(task.get('plan',{}).get('actions'))
                except Exception as exc: record.update({'status':'error','error':f'{type(exc).__name__}: {exc}'})
                model_results.append(record); checkpoint['completed'].append(key); results['models'].setdefault(model,[]).append(record); (run/'results.json').write_text(json.dumps(results,indent=2,default=str),encoding='utf-8'); (run/'checkpoint.json').write_text(json.dumps(checkpoint,indent=2),encoding='utf-8')
        finally:
            proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            (run/'checkpoint.json').write_text(json.dumps(checkpoint,indent=2),encoding='utf-8')
    report=['# Xerxes paired live benchmark','',f'Run: `{run}`','', 'This report records planner/task creation trials. Model-generated plans were not counted as end-to-end execution success unless explicitly executed in the isolated workspace.','']
    for model,recs in results['models'].items(): report += [f'## {model}', '', f'- Trials: {len(recs)}', f'- Successful classified responses: {sum(bool(r.get("success")) for r in recs)}', '']
    (run/'results.json').write_text(json.dumps(results,indent=2,default=str),encoding='utf-8'); (run/'report.md').write_text('\n'.join(report),encoding='utf-8'); print(run)
if __name__=='__main__': main()
