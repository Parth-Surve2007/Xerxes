import ast, hashlib, io, os, platform, time, uuid, zipfile
from datetime import datetime, timezone
from flask import Flask, jsonify, request

app=Flask(__name__)
MAX_ARCHIVE=2*1024*1024; MAX_FILES=50; MAX_FILE=256*1024; MAX_TOTAL=2*1024*1024; SCHEMA='python_syntax.v1'
JOBS={}

def auth():
    token=os.environ.get('XERXES_VERIFICATION_TOKEN','')
    return bool(token) and request.headers.get('X-Xerxes-Verification-Token')==token
def guard(): return None if auth() else (jsonify({'error':{'message':'verification authentication required'}}),401)
def inspect_bundle(raw):
    if len(raw)>MAX_ARCHIVE: raise ValueError('archive exceeds size limit')
    try: archive=zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile: raise ValueError('invalid ZIP archive')
    infos=archive.infolist()
    if not infos or len(infos)>MAX_FILES: raise ValueError('archive file count is invalid')
    seen=set(); sources=[]; total=0
    for info in infos:
        name=info.filename.replace('\\','/')
        if name.startswith('/') or name.startswith('../') or '/../' in name or name in seen or info.is_dir() or info.external_attr & 0xF000==0xA000: raise ValueError('unsafe or duplicate archive entry')
        seen.add(name)
        if not name.endswith('.py'): continue
        if info.file_size>MAX_FILE: raise ValueError('Python source exceeds size limit')
        total+=info.file_size
        if total>MAX_TOTAL: raise ValueError('expanded source exceeds size limit')
        data=archive.read(info)
        sources.append((name,data))
    if not sources: raise ValueError('archive contains no Python files')
    return sources
def verify(raw):
    started=time.perf_counter(); digest=hashlib.sha256(raw).hexdigest(); passed=[]; failed=[]
    for name,data in inspect_bundle(raw):
        try: ast.parse(data.decode('utf-8'),filename=name); passed.append(name)
        except (SyntaxError,UnicodeDecodeError) as exc: failed.append({'file':name,'error':str(exc)[:300]})
    return {'profile':'python_syntax','schema_version':SCHEMA,'bundle_sha256':digest,'architecture':platform.machine(),'python_version':platform.python_version(),'files_parsed':len(passed)+len(failed),'files_passed':passed,'files_failed':failed,'status':'syntax_check_passed' if not failed else 'syntax_check_failed','exit_code':0 if not failed else 1,'elapsed_ms':round((time.perf_counter()-started)*1000,1),'completed_at':datetime.now(timezone.utc).isoformat(),'cleanup':'not_applicable_static_parse'}
@app.get('/api/v1/verification/health')
def health(): return jsonify({'status':'ok','service':'xerxes-verification','architecture':platform.machine(),'python_version':platform.python_version(),'profile':'python_syntax','schema_version':SCHEMA})
@app.get('/api/v1/verification/capabilities')
def capabilities():
    denied=guard()
    if denied:return denied
    return jsonify({'profiles':[{'name':'python_syntax','description':'Static AST syntax validation; submitted code is never executed'}]})
@app.post('/api/v1/verification/jobs')
def create_job():
    denied=guard()
    if denied:return denied
    if request.form.get('profile','python_syntax')!='python_syntax' or request.form.get('command') or request.form.get('environment'): return jsonify({'error':{'message':'only python_syntax is supported'}}),400
    raw=request.files.get('archive').read() if request.files.get('archive') else b''
    try: result=verify(raw)
    except ValueError as exc:return jsonify({'error':{'message':str(exc)}}),400
    job_id=str(uuid.uuid4()); result.update({'job_id':job_id,'created_at':datetime.now(timezone.utc).isoformat()}); JOBS[job_id]=result
    return jsonify(result),202
@app.get('/api/v1/verification/jobs/<job_id>')
def get_job(job_id):
    denied=guard()
    if denied:return denied
    result=JOBS.get(job_id)
    return (jsonify(result),200) if result else (jsonify({'error':{'message':'job not found'}}),404)
if __name__=='__main__': app.run(host='127.0.0.1',port=int(os.environ.get('PORT','5002')),debug=False)
