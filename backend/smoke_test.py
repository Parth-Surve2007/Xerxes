import json, os, sys
from urllib.request import Request,urlopen
from urllib.error import HTTPError
BASE=os.environ.get('API_BASE_URL','http://127.0.0.1:5000/api/v1').rstrip('/')
def call(path,method='GET',payload=None):
    req=Request(BASE+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'},method=method)
    with urlopen(req,timeout=8) as r:return json.loads(r.read())
def expect(path,status,method='GET',payload=None):
    try: call(path,method,payload)
    except HTTPError as e:
        if e.code==status:return
        raise
    raise RuntimeError(f'{path} unexpectedly succeeded')
try:
    health=call('/health'); tree=call('/workspace/tree'); tools=call('/tools'); task=call('/tasks','POST',{'prompt':'Inspect this project and build it'}); result=call('/tasks/'+task['id']+'/execute','POST',{}); audit=call('/audit'); expect('/tasks/unknown',404)
    if health.get('product')!='xerxes' or not tree.get('items') or not tools.get('tools') or result.get('status')!='completed' or not audit.get('items'): raise RuntimeError('incomplete agent response')
    print(json.dumps({'status':'pass','architecture':health.get('architecture'),'model':health.get('model'),'risk_service':health.get('risk_service'),'task_status':result.get('status'),'audit_count':len(audit['items'])}))
except Exception as exc:
    print(json.dumps({'status':'fail','error':str(exc)})); sys.exit(1)
