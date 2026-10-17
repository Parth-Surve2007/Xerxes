import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('xerxes_risk_app', Path(__file__).resolve().parent/'app.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); app=module.app
def test_health_and_info():
    c=app.test_client(); assert c.get('/api/v1/health').get_json()['model_version']=='synthetic-risk-logreg-v1'; assert c.get('/api/v1/model/info').get_json()['samples']==32
def test_score_and_validation():
    c=app.test_client(); low={'action_category':'read_file','inside_workspace':True,'reversible':True,'network':False,'approval':False,'data_sensitivity':'workspace-metadata','privilege':'user'}; high={**low,'action_category':'network_transfer','network':True,'data_sensitivity':'sensitive'}; assert c.post('/api/v1/risk/score',json=low).get_json()['category']=='low'; assert c.post('/api/v1/risk/score',json=high).get_json()['category']=='high'; assert c.post('/api/v1/risk/score',json={}).status_code==400
