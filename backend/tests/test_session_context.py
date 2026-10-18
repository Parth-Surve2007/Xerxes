import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
import app as app_module


def test_local_model_payload_contains_history_once(monkeypatch):
    seen = {}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'choices': [{'message': {'content': '{"response":"ok","actions":[]}'}}]}).encode()

    def open_request(request, **kwargs):
        seen.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(agent.urllib.request, 'urlopen', open_request)
    monkeypatch.setattr(agent.LocalModel, '_ensure_server', lambda self: 'http://local')
    model = agent.LocalModel()
    model.plan('Only the dvm named folder, not all others.', [
        {'role': 'user', 'content': 'Analyze the dvm folder on Desktop and summarize its contents.'},
        {'role': 'assistant', 'content': 'I will inspect the authorized workspace.'},
    ])
    messages = seen['messages']
    assert [m['role'] for m in messages] == ['system', 'user', 'assistant', 'user']
    assert sum(m['content'] == 'Only the dvm named folder, not all others.' for m in messages) == 1


def test_native_model_prompt_contains_history_once(monkeypatch):
    seen = []

    class Native:
        def plan(self, prompt):
            seen.append(prompt)
            return '{"response":"ok","actions":[]}'

        def stop(self):
            pass

    model = agent.LocalModel()
    model.native = Native()
    model.plan('Only the dvm named folder, not all others.', [
        {'role': 'user', 'content': 'Analyze the dvm folder on Desktop and summarize its contents.'},
    ])
    assert len(seen) == 1
    assert seen[0].count('Analyze the dvm folder on Desktop and summarize its contents.') == 1
    assert seen[0].count('Only the dvm named folder, not all others.') == 1


def test_explicit_session_context_isolated_and_linked(tmp_path):
    engine = app_module.AgentEngine(tmp_path, str(tmp_path / 'agent.db'))
    calls = []
    engine.model.plan = lambda prompt: (calls.append(prompt) or {'response': 'ack', 'actions': []})
    first = engine.start_task('Analyze the dvm folder on Desktop and summarize its contents.')
    second = engine.start_task('Only the dvm named folder, not all others.', session_id=first['id'])
    assert second['continuation_of'] == first['id']
    assert second['id'] != first['id']
    assert first['prompt'] in json.dumps(second['messages'])
    fresh = engine.start_task('Start an unrelated request')
    assert first['prompt'] not in json.dumps(fresh['messages'])


def test_session_history_is_bounded(tmp_path):
    engine = app_module.AgentEngine(tmp_path, str(tmp_path / 'agent.db'))
    engine.model.plan = lambda prompt: {'response': 'ok', 'actions': []}
    first = engine.create_task('x' * 20000)
    history = engine._history_for(first)
    assert sum(len(m['content']) for m in history) <= 16000


def test_model_context_budget_preserves_contract_and_current_request(monkeypatch):
    seen = {}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'choices': [{'message': {'content': '{"response":"ok","actions":[]}'}}]}).encode()

    def open_request(request, **kwargs):
        seen.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(agent.urllib.request, 'urlopen', open_request)
    monkeypatch.setattr(agent.LocalModel, '_ensure_server', lambda self: 'http://local')
    model = agent.LocalModel()
    model.plan('current request must remain', [
        {'role': 'user', 'content': 'old context ' * 1000},
        {'role': 'assistant', 'content': 'newer context'},
    ])
    messages = seen['messages']
    assert messages[0]['role'] == 'system'
    assert messages[-1] == {'role': 'user', 'content': 'current request must remain'}
    assert not any('old context' in message['content'] for message in messages)
