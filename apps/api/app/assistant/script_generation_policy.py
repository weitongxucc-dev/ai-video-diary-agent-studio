"""The draft-generation action's finite tool and business-operation boundary."""
SCRIPT_GENERATION_TOOL_OPERATIONS = {
    'read_current_task': 'read_task_workspace',
    'read_preference_memory': 'read_creator_memory',
    'find_knowledge': 'read_assistant_script_context',
    'find_materials': 'search_task_materials',
    'read_material_evidence': 'read_material_evidence',
    'read_script_sections': 'read_script_sections',
    'read_execution_capabilities': 'read_execution_capabilities',
    'start_script_window': 'start_script_window',
    'patch_script_sections': 'patch_script_sections',
    'read_script_window_request': 'read_script_window_request',
}
SCRIPT_GENERATION_TOOLS = tuple(SCRIPT_GENERATION_TOOL_OPERATIONS)
# read_current_task also loads the same task's execution summary.
SCRIPT_GENERATION_OPERATIONS = frozenset(SCRIPT_GENERATION_TOOL_OPERATIONS.values()) | {'read_creation_execution'}


def is_script_generation(binding):
    return binding.get('intent_kind') == 'script_generation' or binding.get('generation_request_id') is not None
