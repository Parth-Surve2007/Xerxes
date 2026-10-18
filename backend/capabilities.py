"""Authoritative capability contracts; metadata never grants Policy access."""
import copy
import json
import re
_S={'type':'string'}
CAPABILITIES=[
 {'name':'list_workspace','label':'Find files','description':'List approved workspace roots.','state':'available','scope':'approved workspace','risk':'low','approval':False,'handler':'list_workspace','input_schema':{'type':'object','properties':{},'additionalProperties':False},'output_schema':{'type':'array'}},
 {'name':'search_workspace','label':'Search files','description':'Search approved workspace roots.','state':'available','scope':'approved workspace','risk':'low','approval':False,'handler':'search_workspace','input_schema':{'type':'object','properties':{'query':{'type':'string','minLength':1,'maxLength':100},'path':_S,'mode':{'type':'string','enum':['content','filename']}},'required':['query'],'additionalProperties':False},'output_schema':{'type':'array'}},
 {'name':'read_file','label':'Read a document','description':'Read an authorized file.','state':'available','scope':'approved workspace','risk':'low','approval':False,'handler':'read_file','input_schema':{'type':'object','properties':{'path':_S},'required':['path'],'additionalProperties':False},'output_schema':{'type':'object','required':['path','content']}},
 {'name':'create_file','label':'Create a text file','description':'Create a new text file with exact user-provided contents.','state':'available','scope':'approved workspace or granted folder','risk':'high','approval':True,'handler':'create_file','input_schema':{'type':'object','properties':{'path':_S,'content':_S},'required':['path','content'],'additionalProperties':False},'output_schema':{'type':'object','required':['path','created','verified','content']}},
 {'name':'run_tests','label':'Run tests','description':'Run an allowlisted test command.','state':'available','scope':'approved project workspace','risk':'medium','approval':False,'handler':'run_tests','input_schema':{'type':'object','properties':{'command':_S},'required':['command'],'additionalProperties':False},'output_schema':{'type':'object','required':['stdout','stderr','exit_code']}},
 {'name':'apply_patch','label':'Organize or modify files','description':'Apply an exact approved file change.','state':'available','scope':'approved workspace','risk':'high','approval':True,'handler':'apply_patch','input_schema':{'type':'object','properties':{'path':_S,'old_text':_S,'new_text':_S},'required':['path','old_text','new_text'],'additionalProperties':False},'output_schema':{'type':'object','required':['path','changed','verified']}},
 {'name':'create_pdf','label':'Create a PDF','description':'Create and verify a PDF.','state':'available','scope':'approved workspace or granted folder','risk':'medium','approval':True,'handler':'create_pdf','input_schema':{'type':'object','properties':{'filename':_S,'destination':_S,'text':_S,'title':_S},'required':['filename','text'],'additionalProperties':False},'output_schema':{'type':'object'},'depends_on':['read_file.result.content']},
 {'name':'launch_application','label':'Open an application','description':'Application launching is not configured.','state':'unavailable','reason':'no registered handler','scope':'not configured','risk':'medium','approval':True,'handler':None},
]
def capability(name): return next((x for x in CAPABILITIES if x['name']==name),None)
def available_capabilities(): return [x for x in CAPABILITIES if x['state']=='available' and x.get('handler')]
def catalog(): return copy.deepcopy(CAPABILITIES)
def planner_contract(): return [{k:copy.deepcopy(x[k]) for k in ('name','description','input_schema','output_schema','scope','approval')}|({'depends_on':x['depends_on']} if x.get('depends_on') else {}) for x in available_capabilities()]

def planner_prompt(user_prompt):
    contract=json.dumps(planner_contract(),separators=(',',':'))
    return ('You are Xerxes. Return only one JSON object, with no markdown. '
            'For ordinary conversation, return {"type":"conversation","response":"...","actions":[]} '
            'with nonempty assistant text and no tools. For workspace work, return '
            '{"type":"plan","actions":[...]} with the minimum registered actions. '
            'Use exact tool names and typed arguments from these contracts: '+contract+' '
            'Each action object must contain only a tool name and its arguments (or the supported input alias); '
            'never copy description, input_schema, output_schema, scope, approval, handler, or permission metadata into an action. '
            'Never invent paths, file contents, results, or completed actions. Use only supported result references. '
            'Ask for selection when search returns multiple candidates; stop honestly when there are no matches. '
            'A PDF destination is independent of its source: writing to Desktop does not require a search. '
            'END CAPABILITY CONTRACT. Output exactly one JSON object now. '
            'The only permitted action keys are tool and arguments; do not output the contract itself. '
            'User request:\n'+user_prompt)

def parse_response_text(text):
    if not isinstance(text,str) or not text.strip(): raise ValueError('model returned empty output')
    fenced=re.search(r'```(?:json)?\s*(.*?)\s*```',text,re.S)
    candidate=fenced.group(1).strip() if fenced else text.strip()
    try: raw=json.loads(candidate)
    except (TypeError,json.JSONDecodeError) as exc:
        starts=[index for index,char in enumerate(candidate) if char in '[{']
        recovered=None
        for index in starts:
            try: recovered,_=json.JSONDecoder().raw_decode(candidate[index:]); break
            except json.JSONDecodeError: continue
        if recovered is None: raise ValueError(f'model returned invalid JSON: {type(exc).__name__}') from exc
        raw=recovered
    if isinstance(raw,list): raw={'type':'plan','actions':raw}
    if not isinstance(raw,dict): raise ValueError('model response must be a JSON object')
    actions=raw.get('actions',[])
    response=raw.get('response')
    if not isinstance(actions,list) or len(actions)>12: raise ValueError('model response has an invalid actions array')
    if response is not None and (not isinstance(response,str) or not response.strip()): raise ValueError('conversation response must contain nonempty text')
    if response is None and not actions: raise ValueError('model response contains neither text nor actions')
    for action in actions:
        if not isinstance(action,dict): raise ValueError('model response contains an invalid action')
        unknown=set(action)-{'tool','name','arguments','input','id'}
        if unknown: raise ValueError(f'model action contains unsupported field: {sorted(unknown)[0]}')
        if 'tool' not in action and isinstance(action.get('name'),str): action['tool']=action.pop('name')
        if 'input' in action:
            if not isinstance(action['input'],dict): raise ValueError('model action input must be an object')
            if 'arguments' in action:
                if action['arguments'] != action['input']: raise ValueError('model action input conflicts with arguments')
                action.pop('input')
            else:
                action['arguments']=action.pop('input')
        if not isinstance(action.get('tool'),str) or not isinstance(action.get('arguments',{}),dict): raise ValueError('model action must contain tool and object arguments')
    if 'type' not in raw: raw['type']='conversation' if isinstance(response,str) and not actions else 'plan'
    if raw['type'] not in {'conversation','plan'}: raise ValueError('model response has an invalid type')
    if raw['type']=='conversation' and actions: raise ValueError('conversation response cannot contain actions')
    if raw['type']=='plan' and response is not None: raise ValueError('plan response cannot contain conversation text')
    return raw
def validate_arguments(name,args):
    item=capability(name)
    if not item or item['state']!='available' or not item.get('handler'): raise ValueError(f'unsupported tool: {name}')
    if not isinstance(args,dict): raise ValueError(f'arguments for {name} must be an object')
    schema=item['input_schema']; unknown=set(args)-set(schema.get('properties',{}))
    if schema.get('additionalProperties') is False and unknown: raise ValueError(f'unsupported arguments for {name}: {sorted(unknown)[0]}')
    missing=[k for k in schema.get('required',[]) if k not in args]
    if missing: raise ValueError(f'{name} requires {missing[0]}')
    for key,value in args.items():
        rule=schema.get('properties',{}).get(key,{})
        if isinstance(value,dict) and set(value)=={'$ref'} and isinstance(value['$ref'],dict):
            ref=value['$ref']
            if set(ref) != {'step','field'} or not all(isinstance(ref[key],str) and ref[key].strip() for key in ('step','field')):
                raise ValueError(f'{name}.{key} has an invalid reference')
            continue
        if rule.get('type')=='string' and not isinstance(value,str): raise ValueError(f'{name}.{key} must be a string')
        if isinstance(value,str) and len(value)<rule.get('minLength',0): raise ValueError(f'{name}.{key} is too short')
        if isinstance(value,str) and len(value)>rule.get('maxLength',2**31): raise ValueError(f'{name}.{key} is too long')
        if 'enum' in rule and value not in rule['enum']: raise ValueError(f'{name}.{key} has an invalid value')
    return True
