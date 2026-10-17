"""Single source of truth for user-visible Xerxes capabilities.

Handlers remain behind the deterministic Policy layer; this registry is
metadata and does not grant model permissions by itself.
"""

CAPABILITIES = [
    {'name': 'list_workspace', 'label': 'Find files', 'state': 'available', 'scope': 'approved workspace', 'risk': 'low', 'approval': False},
    {'name': 'search_workspace', 'label': 'Search files', 'state': 'available', 'scope': 'approved workspace', 'risk': 'low', 'approval': False},
    {'name': 'read_file', 'label': 'Read a document', 'state': 'available', 'scope': 'approved workspace', 'risk': 'low', 'approval': False},
    {'name': 'apply_patch', 'label': 'Organize or modify files', 'state': 'available', 'scope': 'approved workspace', 'risk': 'high', 'approval': True},
    {'name': 'create_pdf', 'label': 'Create a PDF', 'state': 'available', 'scope': 'approved workspace or granted folder', 'risk': 'medium', 'approval': True},
    {'name': 'launch_application', 'label': 'Open an application', 'state': 'unavailable', 'scope': 'not configured', 'risk': 'medium', 'approval': True},
]


def catalog():
    return [dict(item) for item in CAPABILITIES]
