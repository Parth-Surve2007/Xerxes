import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capabilities import CAPABILITIES, available_capabilities, parse_response_text, planner_contract, validate_arguments


def test_registry_has_one_contract_and_handler_for_each_available_capability():
    assert {item['name'] for item in available_capabilities()} == {
        'list_workspace', 'search_workspace', 'read_file', 'create_file', 'run_tests', 'apply_patch', 'create_pdf'
    }
    for item in available_capabilities():
        assert item.get('handler')
        assert item.get('input_schema')
        assert item.get('output_schema')


def test_planner_contract_and_runtime_validation_share_registry_schema():
    contract = {item['name']: item for item in planner_contract()}
    for item in available_capabilities():
        assert contract[item['name']]['input_schema'] == item['input_schema']
    validate_arguments('run_tests', {'command': 'pytest backend/tests -q'})
    try:
        validate_arguments('run_tests', {'command': 'pytest backend/tests -q', 'extra': True})
    except ValueError:
        pass
    else:
        raise AssertionError('unsupported arguments must be rejected')


def test_unavailable_capability_is_not_executable_or_planner_advertised():
    assert next(item for item in CAPABILITIES if item['name'] == 'launch_application')['handler'] is None
    assert 'launch_application' not in {item['name'] for item in planner_contract()}
    try:
        validate_arguments('launch_application', {})
    except ValueError:
        pass
    else:
        raise AssertionError('unavailable capability must be rejected')


def test_shared_response_contract_accepts_conversation_and_plan_rejects_empty_output():
    conversation = parse_response_text('{"type":"conversation","response":"Hello there.","actions":[]}')
    assert conversation['type'] == 'conversation' and conversation['response']
    plan = parse_response_text('{"type":"plan","actions":[{"name":"list_workspace","arguments":{}}]}')
    assert plan['type'] == 'plan' and plan['actions'][0]['tool'] == 'list_workspace'
    for output in ('', '{"actions":[]}', '{"type":"conversation","response":"","actions":[]}'):
        try:
            parse_response_text(output)
        except ValueError:
            continue
        raise AssertionError('malformed or empty model output must be rejected')


def test_shared_response_contract_normalizes_unambiguous_input_alias():
    plan = parse_response_text('{"type":"plan","actions":[{"tool":"create_pdf","input":{"filename":"hello.pdf","text":"hello"}}]}')
    assert plan['actions'][0]['arguments'] == {'filename': 'hello.pdf', 'text': 'hello'}
    assert 'input' not in plan['actions'][0]


def test_shared_response_contract_rejects_conflicting_or_non_object_input():
    for output in (
        '{"actions":[{"tool":"read_file","input":{"path":"a"},"arguments":{"path":"b"}}]}',
        '{"actions":[{"tool":"read_file","input":"a"}]}',
    ):
        try:
            parse_response_text(output)
        except ValueError:
            continue
        raise AssertionError('ambiguous or non-object input must be rejected')


def test_shared_response_contract_rejects_model_metadata_inside_action():
    try:
        parse_response_text('{"actions":[{"tool":"list_workspace","input_schema":{}}]}')
    except ValueError as exc:
        assert 'unsupported field' in str(exc)
    else:
        raise AssertionError('model metadata must not be accepted as an action')


def test_string_reference_has_exact_shape_and_arbitrary_dict_is_rejected():
    validate_arguments('read_file', {'path': {'$ref': {'step': 'search-1', 'field': 'path'}}})
    for value in ({'path': {'anything': 'goes'}}, {'path': {'$ref': {'step': 'search-1'}}}):
        try:
            validate_arguments('read_file', value)
        except ValueError:
            continue
        raise AssertionError('invalid reference shape must be rejected')
