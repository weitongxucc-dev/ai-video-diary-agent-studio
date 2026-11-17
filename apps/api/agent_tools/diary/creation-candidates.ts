import type {BusinessRequest, ToolRegistrar} from './contracts.ts';

export function registerCreationCandidates(pi: ToolRegistrar, request: BusinessRequest, taskId?: string) {
  pi.registerTool({
    name: 'search_creation_candidates',
    label: '跨库查找创作候选',
    description: '用同一句描述查找素材、知识、人物和声音，各域独立返回候选、固定版本、证据与限制。后台只查询原目标固定范围，not_in_goal_scope不表示库为空。候选不是采用许可，不写任务或时间线；不要把不同域的排序当作统一质量分。',
    parameters: {type: 'object', properties: {
      query: {type: 'string', minLength: 1, maxLength: 120},
      limit_per_domain: {type: 'integer', minimum: 1, maximum: 20},
    }, required: ['query'], additionalProperties: false},
    execute: async (_id: string, params: {query: string; limit_per_domain?: number}) => {
      const query = new URLSearchParams({q: params.query, limit_per_domain: String(params.limit_per_domain ?? 5)});
      if (taskId) query.set('task_id', taskId);
      const details = await request(`/api/creation-candidates/search?${query}`);
      if (details.schema !== 'candidate-discovery/v1') throw new Error('创作候选查询契约版本不一致');
      return {content: [{type: 'text', text: JSON.stringify(details)}], details};
    },
  });
  pi.registerTool({
    name: 'read_creation_candidate',
    label: '回读固定创作候选',
    description: '按跨库查询返回的domain/object_id/version回读候选、证据、限制及关联任务反馈。素材修订变化会拒绝，人物/声音/知识按固定版本读取；旧版本可读不代表仍允许采用。不修改作品，不根据文件名猜ID或版本。',
    parameters: {type: 'object', properties: {
      domain: {type: 'string', enum: ['material', 'knowledge', 'person', 'voice']},
      object_id: {type: 'string', minLength: 1}, version: {type: 'string', minLength: 1},
    }, required: ['domain', 'object_id', 'version'], additionalProperties: false},
    execute: async (_id: string, params: {domain: string; object_id: string; version: string}) => {
      const query = new URLSearchParams({version: params.version});
      if (taskId) query.set('task_id', taskId);
      const details = await request(`/api/creation-candidates/${encodeURIComponent(params.domain)}/${encodeURIComponent(params.object_id)}?${query}`);
      if (details.schema !== 'candidate-readback/v1') throw new Error('创作候选回查契约版本不一致');
      return {content: [{type: 'text', text: JSON.stringify(details)}], details};
    },
  });
}
