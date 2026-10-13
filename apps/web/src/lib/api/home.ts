import type { HomeAggregation } from '../types/home';
import { creationPlanRequest } from './creation-transport';
/**
 * 首页整机概览。只带当前身份的成员令牌（无令牌即匿名，服务端按方案 A 放行）。
 * 不在服务端预取 owner 私有任务再由前端隐藏。
 */
export function getHomeDashboard(days: 7 | 30): Promise<HomeAggregation> {
    return creationPlanRequest<HomeAggregation>(`/api/production/home?days=${days}`);
}
