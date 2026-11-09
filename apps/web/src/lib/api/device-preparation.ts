import { request } from './transport';

export type DeviceResourceStatus = {
  platform: string;
  cpu_preparation_supported: boolean;
  preparation_configured: boolean;
  actor_scope: string;
  profile_status: 'unprepared' | 'available' | 'invalid';
  revision: number | null;
  available_operations: string[];
};

export type CpuPreparationShape = {
  width_px: number;
  height_px: number;
  frame_rate: number;
  duration_seconds: number;
  clip_count: number;
  audio_input_count: number;
  sample_rate: number;
  channels: number;
};

export type CpuPreparationRequest = {
  request_id: string;
  kind: 'cpu';
  shape: CpuPreparationShape;
};

export type DevicePreparationReceipt = {
  id: string;
  plan_id: string;
  state: string;
  wait_reason: string | null;
};

export type DevicePreparationFinalization = DevicePreparationReceipt & {
  profile_published: boolean;
  normal_work_started: false;
  revision?: number;
};

const root = '/api/device/resource-preparations';
const scopeHeaders = (scope: string) => ({ 'X-Device-Preparation-Scope': scope });

export async function getDeviceResourceStatus(): Promise<DeviceResourceStatus> {
  const value = await request<DeviceResourceStatus>(root);
  if (!/^[0-9a-f]{64}$/.test(value.actor_scope)
      || typeof value.cpu_preparation_supported !== 'boolean' || typeof value.preparation_configured !== 'boolean'
      || !['unprepared', 'available', 'invalid'].includes(value.profile_status)
      || !Array.isArray(value.available_operations)) {
    throw new Error('设备状态回执不完整，尚不能确认当前身份或准备条件。');
  }
  return value;
}

function receipt(value: DevicePreparationReceipt, expected?: string): DevicePreparationReceipt {
  if (!value || typeof value.id !== 'string' || !value.id || typeof value.plan_id !== 'string'
      || typeof value.state !== 'string' || expected !== undefined && value.id !== expected) {
    throw new Error('原设备准备回执身份无法核对，请保留原请求继续查询。');
  }
  return value;
}

export async function prepareCpuResources(payload: CpuPreparationRequest, scope: string): Promise<DevicePreparationReceipt> {
  return receipt(await request(root, { method: 'POST', headers: scopeHeaders(scope), body: JSON.stringify(payload) }));
}

export async function getDevicePreparation(id: string, scope: string): Promise<DevicePreparationReceipt> {
  return receipt(await request(`${root}/${encodeURIComponent(id)}`, { headers: scopeHeaders(scope) }), id);
}

export async function finalizeDevicePreparation(id: string, scope: string): Promise<DevicePreparationFinalization> {
  const value = await request<DevicePreparationFinalization>(`${root}/${encodeURIComponent(id)}/finalize`, {
    method: 'POST', headers: scopeHeaders(scope), body: '{}',
  });
  receipt(value, id);
  return value;
}
