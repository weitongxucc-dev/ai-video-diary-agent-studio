"""Assistant command, runtime and business-tool capability HTTP; runtime checks and catalog remain shared owners."""
from fastapi import APIRouter, Request

def build_assistant_capability_router(*, workspace_for, member_for, checks_for, tools_for):
    router = APIRouter()

    @router.get('/api/assistant/capabilities')
    def assistant_capabilities(request: Request):
        workspace_id = workspace_for()(request)
        member_for()(request, workspace_id)
        pi_check = next((check for check in checks_for()() if check['id'] == 'pi'))
        return {'application_commands': ['/new', '/name <名称>', '/resume <会话ID>', '/help'], 'runtime': {'owner': 'pi-coding-agent-sdk', 'version': pi_check['version'], 'status': pi_check['status'], 'reason': pi_check['reason']}, 'pi_tui_commands': {'available': False, 'commands': [], 'reason': '请使用本产品的会话命令；终端专用命令不在此运行。'}, 'extension_commands': {'available': False, 'commands': [], 'reason': '创作工具由助手按任务调用。'}, 'skills': {'available': pi_check['status'] == 'ready', 'names': ['source-speech', 'narration-materials', 'local-edit', 'visual-template'], 'reason': '创作技能在任务需要时加载；具体制作能力仍依赖已连接模型和媒体工具。'}, 'prompt_templates': {'available': pi_check['status'] == 'ready', 'names': ['review-result'], 'reason': '结果回读模板由创作流程按需使用。'}, 'business_tools': {scope: list(tools) for scope, tools in tools_for().items()}}
    return router
