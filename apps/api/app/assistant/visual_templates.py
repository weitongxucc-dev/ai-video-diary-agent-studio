"""Task Agent adapter over the same template use case and transaction owner."""
from dataclasses import asdict

from app.creation.video_template_use_case import VideoTemplateUseCase
from app.schemas import VisualTemplateCopyRequest, VideoTemplateVersionRequest
from app.creation.visual_template_contracts import VisualTemplate

VISUAL_TEMPLATE_OPERATIONS = frozenset({'read_video_templates', 'read_visual_template', 'save_visual_template', 'update_visual_template'})


def visual_template_operation(repository, actor, operation, args, *, write_guard):
    case = VideoTemplateUseCase(repository)
    workspace = actor.workspace_id
    user = actor.user_id or actor.member_id
    if operation == 'read_video_templates':
        return {'items': [asdict(item) for item in [*case.builtin_templates(workspace),
            *repository.video_templates.list_video_templates(workspace, viewer_user_id=user)]]}
    if operation == 'save_visual_template':
        payload = VisualTemplateCopyRequest.model_validate(args['payload'])
        identity, version = payload.source_template_id, payload.source_template_version
    elif operation == 'update_visual_template':
        payload = VideoTemplateVersionRequest.model_validate(args['payload'])
        identity, version = args['template_id'], payload.expected_version
    else:
        identity, version = args['template_id'], args['version']
    previous = case.builtin_template(workspace, identity, version)
    if previous is None:
        previous = repository.video_templates.get_video_template(workspace, identity, version)
        case.require_readable(previous, identity, reader_user_id=user, local_diagnostic=False)
    if operation == 'read_visual_template':
        return {**asdict(previous), 'visual_schema': VisualTemplate.model_json_schema()}
    if operation == 'update_visual_template':
        return asdict(case.append_version(previous, expected_version=version, name=payload.name,
            style_instruction=payload.style_instruction, visual=payload.visual, user_id=user, write_guard=write_guard))
    return asdict(case.save_visual_copy(previous, template_id=f'template_{payload.request_id.hex}',
        name=payload.name.strip(), visual=payload.visual, user_id=user, write_guard=write_guard))
