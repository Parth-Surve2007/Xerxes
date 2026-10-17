"""Single source of truth for user-visible Xerxes capabilities.

Handlers remain behind the deterministic Policy layer; this registry is
metadata and does not grant model permissions by itself.
"""

CAPABILITIES = [
    {'name': 'list_workspace', 'label': 'Find files', 'state': 'available', 'scope': 'approved workspace', 'risk': 'low', 'approval': False, 'arguments': {}, 'returns': 'workspace entries'},
    {'name': 'search_workspace', 'label': 'Search files', 'state': 'available', 'scope': 'approved workspace', 'risk': 'low', 'approval': False, 'arguments': {'query': 'string, 1-100 chars', 'path': 'optional approved root alias or path', 'mode': 'content or filename'}, 'returns': 'candidate file entries; may require user selection'},
    {'name': 'read_file', 'label': 'Read a document', 'state': 'available', 'scope': 'approved workspace', 'risk': 'low', 'approval': False, 'arguments': {'path': 'actual path returned by search or explicit user path'}, 'returns': 'path and text content'},
    {'name': 'apply_patch', 'label': 'Organize or modify files', 'state': 'available', 'scope': 'approved workspace', 'risk': 'high', 'approval': True, 'arguments': {'path': 'approved file path', 'old_text': 'exact text', 'new_text': 'replacement text'}, 'returns': 'verified diff'},
    {'name': 'create_pdf', 'label': 'Create a PDF', 'state': 'available', 'scope': 'approved workspace or granted folder', 'risk': 'medium', 'approval': True, 'arguments': {'filename': 'PDF filename', 'destination': 'approved folder or desktop alias', 'text': 'non-empty text', 'title': 'optional title'}, 'returns': 'verified output path', 'depends_on': ['read_file.result.content']},
    {'name': 'launch_application', 'label': 'Open an application', 'state': 'unavailable', 'scope': 'not configured', 'risk': 'medium', 'approval': True},
]


def catalog():
    return [dict(item) for item in CAPABILITIES]

def planner_contract():
    return [{'name': item['name'], 'arguments': item.get('arguments', {}), 'returns': item.get('returns'), 'depends_on': item.get('depends_on', []), 'approval': item['approval']} for item in CAPABILITIES if item['state']=='available']
