import os, platform
from flask import Flask, jsonify, request
from flask_cors import CORS
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.metrics import confusion_matrix, precision_score, recall_score
from sklearn.model_selection import train_test_split

app=Flask(__name__); CORS(app, origins=os.environ.get('XERXES_RISK_CORS','http://127.0.0.1:5000').split(','))
MODEL_VERSION='synthetic-risk-logreg-v1'
FEATURES=['action_category','inside_workspace','reversible','network','approval','data_sensitivity','privilege']
def synthetic():
    rows=[]
    for action in ['list_workspace','read_file','search_workspace','run_tests','apply_patch','delete','network_transfer','install']:
        for inside in [True,False]:
            for reversible in [True,False]:
                network=action=='network_transfer'; approval=action in {'apply_patch','delete','install'}; sensitive=not inside or action=='network_transfer'; privilege=action in {'install','delete'}
                risky=bool(network or privilege or sensitive or (not reversible and not approval))
                rows.append(({ 'action_category':action,'inside_workspace':inside,'reversible':reversible,'network':network,'approval':approval,'data_sensitivity':'sensitive' if sensitive else 'workspace-metadata','privilege':'elevated' if privilege else 'user'},int(risky)))
    return rows
TRAIN=synthetic()
TRAIN_ROWS, TEST_ROWS = train_test_split(TRAIN, test_size=.25, random_state=7, stratify=[y for _, y in TRAIN])
MODEL=make_pipeline(DictVectorizer(),LogisticRegression(max_iter=500,random_state=7)); MODEL.fit([x for x,_ in TRAIN_ROWS],[y for _,y in TRAIN_ROWS])
TEST_LABELS=[y for _, y in TEST_ROWS]
TEST_PREDICTIONS=MODEL.predict([x for x,_ in TEST_ROWS])
EVALUATION={
    'train_samples':len(TRAIN_ROWS),
    'test_samples':len(TEST_ROWS),
    'precision':round(float(precision_score(TEST_LABELS, TEST_PREDICTIONS, zero_division=0)),4),
    'recall':round(float(recall_score(TEST_LABELS, TEST_PREDICTIONS, zero_division=0)),4),
    'confusion_matrix':confusion_matrix(TEST_LABELS, TEST_PREDICTIONS, labels=[0,1]).tolist(),
}
def validate(data):
    if not isinstance(data,dict): return 'JSON object required'
    missing=[x for x in FEATURES if x not in data]
    if missing:return 'missing fields: '+', '.join(missing)
    if not isinstance(data['inside_workspace'],bool) or not isinstance(data['reversible'],bool) or not isinstance(data['network'],bool) or not isinstance(data['approval'],bool): return 'boolean fields are invalid'
    if data['privilege'] not in {'user','elevated'}: return 'privilege is invalid'
    return None
@app.get('/api/v1/health')
def health(): return jsonify({'status':'ok','service':'xerxes-risk-service','architecture':platform.machine(),'model_version':MODEL_VERSION})
@app.get('/api/v1/model/info')
def info(): return jsonify({'model_version':MODEL_VERSION,'algorithm':'scikit-learn LogisticRegression with DictVectorizer','training':'deterministic synthetic action dataset','samples':len(TRAIN),'class_distribution':{'low':sum(1-y for _,y in TRAIN),'high':sum(y for _,y in TRAIN)},'evaluation':EVALUATION,'limitations':'Prototype advisory model; local deterministic policy remains authoritative.'})
@app.post('/api/v1/risk/score')
def score():
    data=request.get_json(silent=True); problem=validate(data)
    if problem:return jsonify({'error':{'message':problem}}),400
    probability=float(MODEL.predict_proba([data])[0][1]); category='high' if probability>=.5 else 'low'
    return jsonify({'model_version':MODEL_VERSION,'category':category,'risk_score':round(probability,4),'advisory':True,'local_policy_remains_authoritative':True})
if __name__=='__main__': app.run(host='127.0.0.1',port=int(os.environ.get('PORT',5001)),debug=False)
