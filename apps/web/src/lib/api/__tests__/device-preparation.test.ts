import { beforeEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('../transport', () => ({ request: mock.request }));
import { finalizeDevicePreparation, getDevicePreparation, getDeviceResourceStatus, prepareCpuResources } from '../device-preparation';

beforeEach(() => { vi.resetAllMocks(); });

it('carries the expected actor scope on prepare, original read and finalize through the shared transport', async () => {
  const receipt = { id: 'work/1', plan_id: 'plan', state: 'done', wait_reason: null };
  mock.request.mockResolvedValue(receipt);
  const payload = { request_id: '23a3fb3d-8ca3-462e-973b-e92bd4de99b1', kind: 'cpu' as const,
    shape: { width_px: 720, height_px: 1280, frame_rate: 30, duration_seconds: 30, clip_count: 6, audio_input_count: 2, sample_rate: 48000, channels: 2 } };
  await prepareCpuResources(payload, 'scope');
  await getDevicePreparation(receipt.id, 'scope');
  await finalizeDevicePreparation(receipt.id, 'scope');
  expect(mock.request.mock.calls).toEqual([
    ['/api/device/resource-preparations', { method: 'POST', headers: { 'X-Device-Preparation-Scope': 'scope' }, body: JSON.stringify(payload) }],
    ['/api/device/resource-preparations/work%2F1', { headers: { 'X-Device-Preparation-Scope': 'scope' } }],
    ['/api/device/resource-preparations/work%2F1/finalize', { method: 'POST', headers: { 'X-Device-Preparation-Scope': 'scope' }, body: '{}' }],
  ]);
});

it('rejects mismatched queue receipts rather than switching the original request', async () => {
  mock.request.mockResolvedValue({ id: 'other-work', plan_id: 'plan', state: 'done' });
  await expect(getDevicePreparation('original-work', 'scope')).rejects.toThrow('身份无法核对');
});

it('does not accept a status without a usable actor scope as identity or readiness evidence', async () => {
  mock.request.mockResolvedValue({ profile_status: 'available', actor_scope: undefined });
  await expect(getDeviceResourceStatus()).rejects.toThrow('设备状态回执不完整');
});
