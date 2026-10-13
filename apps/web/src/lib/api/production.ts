import type { ProductionFoundation } from '../types/production';
import { request } from './transport';
export function getProductionFoundation(): Promise<ProductionFoundation> {
    return request<ProductionFoundation>("/api/production/foundation");
}
