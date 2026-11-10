import type {ActionSource, BusinessRequest, ToolRegistrar} from './contracts.ts';
import {typedRequestId} from './request-identity.ts';
import {BusinessRequestError, BusinessResultUnknown, RequestNotSubmitted} from './transport.ts';

export function registerSourceTasks(pi: ToolRegistrar, {request, readActionSource, taskId, hasPreview = () => false, markPreview = () => {}}: {
  request: BusinessRequest; readActionSource: () => ActionSource; taskId?: string;
  hasPreview?: () => boolean; markPreview?: () => void;
}) {
  const attempts = new Map<string, {paramsKey: string; payload: Promise<Record<string, unknown>>}>();
  function locator() {
    const source = readActionSource();
    if (taskId || !source.turnId) throw new Error('原片新建只用于真实首页输入，已有作品请沿原任务修改');
    return {turnId: source.turnId, input: source.inputId ?? source.turnId,
      requestId: typedRequestId(undefined, source.turnId, `source-task:${source.inputId ?? source.turnId}`)};
  }
  const render = (receipt: any) => {
    const details = {kind: 'source_task_created', receipt,
      instruction: '这是已持久创建的原片任务回执，尚不是成片。首页本回合回报此回执后结束；宿主会把原要求自动接续到此任务，届时提供镜头、图层、制作及检查工具，不需用户再次确认。不要在首页搜索无关图片工具代替任务编辑或要求用户重新下制作指令。后续沿此任务、原素材和设备许可执行。'};
    return {content: [{type: 'text', text: JSON.stringify(details)}], details};
  };
  pi.registerTool({name: 'read_source_task', label: '核对原片任务',
    description: '回读本轮原片创建的固定请求；未知结果先查这里，不换请求身份重建作品。404仅表示当前尚无已提交回执。',
    parameters: {type: 'object', properties: {request_id: {type: 'string', format: 'uuid',
      description: '原失败或回执里的固定UUID；用户已追加输入或重启后仍使用原值。'}}, additionalProperties: false},
    execute: async (_id: string, params: {request_id?: string} = {}) => {
      const requestId = params.request_id ?? locator().requestId;
      try { return render(await request(`/api/source-tasks/${encodeURIComponent(requestId)}`)); }
      catch (error) {
        if (!(error instanceof BusinessRequestError) || error.httpStatus !== 404) throw error;
        const details = {request_id: requestId, status: 'not_found',
          instruction: '尚未查到原回执；若要续交，沿本轮同请求与原参数，不能换ID。'};
        return {content: [{type: 'text', text: JSON.stringify(details)}], details};
      }
    }});
  pi.registerTool({name: 'create_source_task', label: '从原片创建作品',
    description: '用户要求用已导入视频继续剪辑时，读取实际资产并从明确原片区间创建一个可编辑任务。整片不传区间则使用实际完整时长；保留原声无需选题、知识库或生成声音。只讨论不创建，已有任务不可另建；一个已消费输入只绑定一个原片请求，未知先read_source_task。',
    parameters: {type: 'object', properties: {
      asset_id: {type: 'string', minLength: 1}, name: {type: 'string', minLength: 1, maxLength: 200},
      aspect_ratio: {type: 'string', enum: ['9:16', '16:9', '1:1']}, preserve_audio: {type: 'boolean'},
      source_start_seconds: {type: 'number', minimum: 0}, source_end_seconds: {type: 'number', exclusiveMinimum: 0},
    }, required: ['asset_id', 'name', 'aspect_ratio', 'preserve_audio'], additionalProperties: false},
    execute: async (_id: string, params: {asset_id: string; name: string; aspect_ratio: string; preserve_audio: boolean;
      source_start_seconds?: number; source_end_seconds?: number}) => {
      const original = locator();
      const paramsKey = JSON.stringify([params.asset_id, params.name, params.aspect_ratio, params.preserve_audio,
        params.source_start_seconds ?? 0, params.source_end_seconds ?? null]);
      let attempt = attempts.get(original.input);
      if (attempt && attempt.paramsKey !== paramsKey) throw new Error('原片请求已绑定原参数，先查询原结果，不能用同UUID改绑另一区间或要求');
      if (hasPreview() && !attempt) throw new Error('本轮已有新建或修改动作，先核对原结果，未另建原片任务');
      if (!attempt) {
        attempt = {paramsKey, payload: (async () => {
          const frozen = await request(`/api/source-tasks/${original.requestId}/input`);
          if (frozen !== null) {
            const saved = frozen.payload;
            if (saved.request_id !== original.requestId || saved.asset_id !== params.asset_id || saved.name !== params.name
                || saved.aspect_ratio !== params.aspect_ratio || saved.preserve_audio !== params.preserve_audio
                || saved.source_start_seconds !== (params.source_start_seconds ?? 0)
                || params.source_end_seconds !== undefined && saved.source_end_seconds !== params.source_end_seconds)
              throw new Error('原片请求已有持久输入，不能改绑新的参数；先查询原回执');
            return saved;
          }
          const asset = await request(`/api/production/materials/${encodeURIComponent(params.asset_id)}`);
          if (locator().input !== original.input) throw new Error('用户输入已变化，没有创建原片任务');
          const end = params.source_end_seconds ?? asset.duration_seconds;
          if (asset.media_type !== 'video' || !Number.isFinite(end) || end <= 0)
            throw new Error('原视频实际时长不可用，未猜测区间或创建任务');
          return {...params, request_id: original.requestId, expected_asset_updated_at: asset.updated_at,
            source_start_seconds: params.source_start_seconds ?? 0, source_end_seconds: end};
        })()};
        attempts.set(original.input, attempt);
      }
      let payload;
      try { payload = await attempt.payload; }
      catch (error) { if (attempts.get(original.input) === attempt) attempts.delete(original.input); throw error; }
      if (locator().input !== original.input) throw new Error('用户输入已变化，没有提交旧原片请求');
      try {
        markPreview();
        return render(await request(`/api/source-tasks?assistant_turn_id=${encodeURIComponent(original.turnId)}`,
          {method: 'POST', body: JSON.stringify(payload)}));
      } catch (error) {
        if (error instanceof BusinessRequestError || error instanceof RequestNotSubmitted) throw error;
        throw new BusinessResultUnknown({error: 'source_task_result_unknown', stage: 'source_creation',
          submission_effect: 'unknown', request_id: original.requestId, query_url: `/api/source-tasks/${original.requestId}`,
          reason: '原片创建结果待核对，保留原请求；不能另建作品冒充重试。', repair_action: '调用read_source_task核对原回执。'});
      }
    }});
}
