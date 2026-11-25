import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ status: vi.fn(), prepare: vi.fn(), read: vi.fn(), finalize: vi.fn() }));
vi.mock('@/lib/api/device-preparation', () => ({
  getDeviceResourceStatus: api.status, prepareCpuResources: api.prepare,
  getDevicePreparation: api.read, finalizeDevicePreparation: api.finalize,
}));
import { DeviceResourcePreparationPanel } from '../DeviceResourcePreparationPanel';
import { ApiRequestError } from '@/lib/api/transport';

const scope = 'a'.repeat(64), otherScope = 'b'.repeat(64);
const key = (owner = scope) => `yaohe:cpu-preparation:v1:${owner}`;
const uuid = '23a3fb3d-8ca3-462e-973b-e92bd4de99b1';
const payload = { request_id: uuid, kind: 'cpu', shape: { width_px: 1080, height_px: 1920,
  frame_rate: 30, duration_seconds: 17, clip_count: 6, audio_input_count: 2, sample_rate: 48000, channels: 2 } };
const status = { platform: 'darwin', cpu_preparation_supported: true, preparation_configured: true,
  actor_scope: scope, profile_status: 'unprepared', revision: null, available_operations: [] };
const queued = { id: 'original-work', plan_id: 'original-plan', state: 'waiting', wait_reason: null };
const done = { ...queued, state: 'done' };
const cutId = 'a4'.repeat(16);
const cutPayload = { ...payload, shape: { ...payload.shape, measure_mp4_cut: true } };
const cutQueued = { ...queued, id: cutId, mp4_cut_measurement: 'unknown' };

beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear();
  vi.spyOn(crypto, 'randomUUID').mockReturnValue(uuid);
  api.status.mockResolvedValue(status);
  api.prepare.mockResolvedValue(queued);
  api.read.mockResolvedValue(queued);
});

it('shows the actual unsupported platform and never submits preparation', async () => {
  api.status.mockResolvedValue({ ...status, platform: 'win32', cpu_preparation_supported: false });
  render(<DeviceResourcePreparationPanel/>);
  expect(await screen.findByText(/当前系统尚不支持基础媒体准备/)).toBeVisible();
  expect(screen.getByRole('button', { name: '开始准备基础媒体' })).toBeDisabled();
  expect(api.prepare).not.toHaveBeenCalled();
});

it('requires a configured desktop and keeps server status authoritative over saved finalization', async () => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload, workId: queued.id, finalized: true }));
  api.status.mockResolvedValue({ ...status, preparation_configured: false });
  api.read.mockResolvedValue(done);
  render(<DeviceResourcePreparationPanel/>);
  expect(await screen.findByText(/请从桌面应用打开设置/)).toBeVisible();
  expect(screen.getByText('基础媒体尚未准备')).toBeVisible();
  expect(screen.queryByText('基础媒体准备已记录')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '重新准备基础媒体' })).toBeDisabled();
});

it('persists the complete input before POST and retries an unknown result with exactly the same UUID and shape', async () => {
  api.prepare.mockImplementationOnce(async (submitted, owner) => {
    expect(JSON.parse(localStorage.getItem(key())!)).toEqual({ scope, payload: submitted });
    expect(owner).toBe(scope);
    throw new Error('连接中断，提交结果未知');
  }).mockResolvedValueOnce(queued);
  render(<DeviceResourcePreparationPanel/>);
  await screen.findByText('基础媒体尚未准备');
  expect(screen.getByRole('checkbox', { name: '同时测量 MP4 截取' })).not.toBeChecked();
  fireEvent.change(screen.getByRole('spinbutton', { name: '处理时长（秒）' }), { target: { value: '17' } });
  fireEvent.click(screen.getByRole('button', { name: '开始准备基础媒体' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('spinbutton', { name: '处理时长（秒）' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '继续核对原请求' }));
  await screen.findByText('已排队，等待设备');
  expect(api.prepare).toHaveBeenNthCalledWith(1, payload, scope);
  expect(api.prepare).toHaveBeenNthCalledWith(2, payload, scope);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it('starts cutoff measurement only on explicit submit and reopens the same recorded receipt', async () => {
  const recorded = { ...cutQueued, state: 'done', mp4_cut_measurement: 'recorded' };
  api.prepare.mockResolvedValue(cutQueued);
  api.read.mockResolvedValue(recorded);
  api.finalize.mockResolvedValue({ ...recorded, profile_published: true, normal_work_started: false });
  const first = render(<DeviceResourcePreparationPanel/>);
  await screen.findByText('基础媒体尚未准备');
  const checkbox = screen.getByRole('checkbox', { name: '同时测量 MP4 截取' });
  fireEvent.click(checkbox);
  fireEvent.change(screen.getByRole('spinbutton', { name: '处理时长（秒）' }), { target: { value: '0.5' } });
  expect(screen.getByRole('button', { name: '开始准备基础媒体' })).toBeDisabled();
  expect(api.prepare).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('spinbutton', { name: '处理时长（秒）' }), { target: { value: '17' } });
  fireEvent.click(screen.getByRole('button', { name: '开始准备基础媒体' }));
  await screen.findByText('本次包含 MP4 截取测量，等待原准备回执。');
  expect(api.prepare).toHaveBeenCalledWith(cutPayload, scope);
  expect(checkbox).toBeChecked();
  expect(checkbox).toBeDisabled();
  expect(screen.queryByRole('button', { name: '复制截取准备回执编号' })).not.toBeInTheDocument();
  first.unmount();

  render(<DeviceResourcePreparationPanel/>);
  await screen.findByText('MP4 截取测量已记录。');
  expect(screen.getByText(cutId)).toBeVisible();
  expect(screen.getByText('提交截取时会核对素材、区间和当前设备条件。')).toBeVisible();
  expect(api.read).toHaveBeenCalledWith(cutId, scope);
  fireEvent.click(screen.getByRole('button', { name: '核验并启用' }));
  await screen.findByText(/已取得核验启用回执/);
  expect(api.finalize).toHaveBeenCalledWith(cutId, scope);
  expect(api.prepare).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('checkbox', { name: '同时测量 MP4 截取' })).not.toBeChecked();
  expect(screen.getByRole('button', { name: '复制截取准备回执编号' })).toBeEnabled();
});

it.each([
  { submitted: payload, result: { ...done, mp4_cut_measurement: 'not_requested' }, label: '本次未请求 MP4 截取测量。' },
  { submitted: cutPayload, result: { ...done, id: cutId }, label: 'MP4 截取测量回执状态未知。' },
  { submitted: cutPayload, result: { ...cutQueued, state: 'failed' }, label: '本次未取得 MP4 截取测量回执。' },
])('shows $label without a copyable receipt number', async ({ submitted, result, label }) => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload: submitted, workId: result.id }));
  api.read.mockResolvedValue(result);
  render(<DeviceResourcePreparationPanel/>);
  expect(await screen.findByText(label)).toBeVisible();
  expect(screen.queryByRole('button', { name: '复制截取准备回执编号' })).not.toBeInTheDocument();
  expect(api.prepare).not.toHaveBeenCalled();
});

it('clears the previous member’s measurement receipt when the preparation scope changes', async () => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload: cutPayload, workId: cutId, finalized: true }));
  api.read.mockResolvedValue({ ...cutQueued, state: 'done', mp4_cut_measurement: 'recorded' });
  render(<DeviceResourcePreparationPanel/>);
  await screen.findByText('MP4 截取测量已记录。');
  api.read.mockClear();
  api.status.mockResolvedValue({ ...status, actor_scope: otherScope });
  fireEvent.click(screen.getByRole('button', { name: '重新读取状态' }));
  await waitFor(() => expect(screen.queryByText('MP4 截取测量已记录。')).not.toBeInTheDocument());
  expect(screen.getByRole('checkbox', { name: '同时测量 MP4 截取' })).not.toBeChecked();
  expect(api.read).not.toHaveBeenCalled();
  expect(api.prepare).not.toHaveBeenCalled();
});

it('restores unknown submissions after refresh without automatically submitting a replacement', async () => {
  const original={...payload,shape:{...payload.shape,overlay_count:3,template_count:1,native_command:true}};
  localStorage.setItem(key(), JSON.stringify({ scope, payload:original }));
  render(<DeviceResourcePreparationPanel/>);
  await screen.findByRole('button', { name: '继续核对原请求' });
  expect(screen.getByRole('spinbutton', { name: '处理时长（秒）' })).toHaveValue(17);
  expect(screen.getByRole('spinbutton', { name: '图文与字幕总层数' })).toHaveValue(3);
  expect(screen.getByRole('spinbutton', { name: '动画模板层数' })).toHaveValue(1);
  expect(screen.getByRole('checkbox', { name: '同时准备程序化制作' })).toBeChecked();
  expect(api.prepare).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '继续核对原请求' }));
  await screen.findByText('已排队，等待设备');
  expect(api.prepare).toHaveBeenCalledWith(original, scope);
  expect(crypto.randomUUID).not.toHaveBeenCalled();
});

it('observes a saved queue identity and enables only after verified finalization and a fresh profile read', async () => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload, workId: queued.id }));
  api.read.mockResolvedValue(done);
  api.finalize.mockImplementation(async () => {
    api.status.mockResolvedValue({ ...status, profile_status: 'available', revision: 1,
      available_operations: ['cpu_render', 'cpu_audio', 'clip_preview'] });
    return { ...done, profile_published: true, normal_work_started: false, revision: 1 };
  });
  render(<DeviceResourcePreparationPanel/>);
  fireEvent.click(await screen.findByRole('button', { name: '核验并启用' }));
  expect(await screen.findByText('基础媒体准备已记录')).toBeVisible();
  expect(screen.getByText(/已有作品没有自动开始制作/)).toBeVisible();
  expect(api.read).toHaveBeenCalledWith(queued.id, scope);
  expect(api.finalize).toHaveBeenCalledWith(queued.id, scope);
  expect(api.prepare).not.toHaveBeenCalled();
});

it('retains the original completed request when publication is not confirmed', async () => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload, workId: queued.id }));
  api.read.mockResolvedValue(done);
  api.finalize.mockResolvedValue({ ...done, profile_published: false, normal_work_started: false });
  render(<DeviceResourcePreparationPanel/>);
  fireEvent.click(await screen.findByRole('button', { name: '核验并启用' }));
  await screen.findByRole('alert');
  expect(JSON.parse(localStorage.getItem(key())!).finalized).not.toBe(true);
  expect(screen.getByRole('button', { name: '核验并启用' })).toBeEnabled();
  expect(api.prepare).not.toHaveBeenCalled();
});

it('does not reuse an earlier identity request when the current identity changes before submission', async () => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload }));
  render(<DeviceResourcePreparationPanel/>);
  const resume = await screen.findByRole('button', { name: '继续核对原请求' });
  api.status.mockResolvedValue({ ...status, actor_scope: otherScope });
  fireEvent.click(resume);
  await screen.findByRole('alert');
  expect(api.prepare).not.toHaveBeenCalled();
  expect(api.read).not.toHaveBeenCalled();
  expect(localStorage.getItem(key(otherScope))).toBeNull();
  expect(JSON.parse(localStorage.getItem(key())!).payload).toEqual(payload);
});

it('does not submit when the original input cannot be persisted', async () => {
  render(<DeviceResourcePreparationPanel/>);
  await screen.findByText('基础媒体尚未准备');
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError'); });
  fireEvent.click(screen.getByRole('button', { name: '开始准备基础媒体' }));
  await screen.findByRole('alert');
  expect(api.prepare).not.toHaveBeenCalled();
  write.mockRestore();
});

it('keeps failed preparation bound until it is explicitly finalized without starting new work', async () => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload, workId: queued.id }));
  const failed = { ...queued, state: 'failed', wait_reason: 'device_preparation_sampling_incomplete',
    failure_reason: '资源采样不完整，未启用新的资源档。', repair_action: '收束原准备后重新准备相同规格。' };
  api.read.mockResolvedValue(failed);
  api.finalize.mockResolvedValue({ ...failed, profile_published: false, normal_work_started: false });
  render(<DeviceResourcePreparationPanel/>);
  expect(await screen.findByText(failed.failure_reason)).toBeVisible();
  expect(screen.getByText(failed.repair_action)).toBeVisible();
  fireEvent.click(await screen.findByRole('button', { name: '收束原准备' }));
  await screen.findByText(/原准备已收束/);
  expect(api.prepare).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '重新准备基础媒体' })).toBeEnabled();
  const nextId = '3df004ea-d5c9-4ef8-ab73-c1c5e0507eb3';
  vi.mocked(crypto.randomUUID).mockReturnValueOnce(nextId);
  fireEvent.change(screen.getByRole('spinbutton', { name: '处理时长（秒）' }), { target: { value: '23' } });
  fireEvent.click(screen.getByRole('button', { name: '重新准备基础媒体' }));
  await screen.findByText('已排队，等待设备');
  expect(api.prepare).toHaveBeenCalledWith({ ...payload, request_id: nextId,
    shape: { ...payload.shape, duration_seconds: 23 } }, scope);
});

it('permits explicit input correction only after a known 422 rejection', async () => {
  api.prepare.mockRejectedValueOnce(new ApiRequestError(422, '{"detail":"规格格式不匹配"}'));
  render(<DeviceResourcePreparationPanel/>);
  await screen.findByText('基础媒体尚未准备');
  fireEvent.click(screen.getByRole('button', { name: '开始准备基础媒体' }));
  const correct = await screen.findByRole('button', { name: '修改准备规格' });
  expect(screen.getByRole('spinbutton', { name: '镜头数量' })).toBeDisabled();
  fireEvent.click(correct);
  await waitFor(() => expect(screen.getByRole('spinbutton', { name: '镜头数量' })).toBeEnabled());
  expect(localStorage.getItem(key())).toBeNull();
  expect(api.prepare).toHaveBeenCalledTimes(1);
});

it('retains fixed input on a 409 refusal without assuming that submission never happened', async () => {
  api.prepare.mockRejectedValueOnce(new ApiRequestError(409, '{"detail":"原准备仍在处理"}'));
  render(<DeviceResourcePreparationPanel/>);
  await screen.findByText('基础媒体尚未准备');
  fireEvent.click(screen.getByRole('button', { name: '开始准备基础媒体' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: '继续核对原请求' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: '修改准备规格' })).not.toBeInTheDocument();
  expect(screen.getByRole('spinbutton', { name: '镜头数量' })).toBeDisabled();
  expect(JSON.parse(localStorage.getItem(key())!).payload.request_id).toBe(uuid);
});

it('keeps a known queue identity after a read failure and retries GET rather than POST', async () => {
  localStorage.setItem(key(), JSON.stringify({ scope, payload, workId: queued.id }));
  api.read.mockRejectedValueOnce(new Error('暂时离线')).mockResolvedValueOnce(queued);
  render(<DeviceResourcePreparationPanel/>);
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: '刷新原准备进度' }));
  await screen.findByText('已排队，等待设备');
  await waitFor(() => expect(api.read).toHaveBeenCalledTimes(2));
  expect(api.prepare).not.toHaveBeenCalled();
});
