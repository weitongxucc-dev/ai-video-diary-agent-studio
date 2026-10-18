import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi, type Mock } from 'vitest';
import type { CompanionContext } from '@/lib/api/companion';
const mocks = vi.hoisted(() => ({ local: false, read: vi.fn(), pair: vi.fn(), authChange: null as null | ((event: string, session: unknown) => void), unsubscribe: vi.fn(), localMembers: vi.fn(), localIdentity: vi.fn() }));
vi.mock('@/lib/auth/routing', () => ({ isLocalAuthMode: () => mocks.local }));
vi.mock('@/lib/api/companion', async original => ({ ...await original<typeof import('@/lib/api/companion')>(), readCompanionContext: mocks.read, createCompanionPairing: mocks.pair }));
vi.mock('@/lib/supabase/client', () => ({ createClient: () => ({ auth: { onAuthStateChange: (callback: typeof mocks.authChange) => { mocks.authChange = callback; return { data: { subscription: { unsubscribe: mocks.unsubscribe } } }; } } }) }));
vi.mock('@/lib/api', () => ({ listLocalMembers: mocks.localMembers, membersErrorMessage: () => 'unavailable' }));
vi.mock('@/lib/local-member-auth', () => ({ fetchLocalMemberIdentity: mocks.localIdentity }));
vi.mock('@/hooks/useLocalMemberIdentity', () => ({ useLocalMemberIdentity: () => { throw new Error('formal settings must not load local identity'); } }));
import { CompanionConnection } from '../CompanionConnection';
import { SettingsCreators } from '../SettingsCreators';
const context: CompanionContext = { device_id: 'device-a', workspace_id: 'workspace-a', member_id: 'member-a', name: '当前账号', role: 'owner', accessToken: 'formal-jwt' };
const disabled = { enabled: false, endpoint: null, addresses: ['192.168.1.2'], device_id: 'device-a' };
const enabled = { ...disabled, enabled: true, endpoint: 'https://192.168.1.2:9001' };
type Bridge = Required<Pick<NonNullable<Window['electronAPI']>, 'companionStatus' | 'configureCompanion' | 'disableCompanion' | 'companionInvitation'>>;
let bridge: { [Key in keyof Bridge]: Mock<Bridge[Key]> };
function ticket() { return { token: 'short-ticket', expires_at: new Date(Date.now() + 600_000).toISOString(), workspace_id: 'workspace-a', device_id: 'device-a' }; }
beforeEach(() => {
  vi.clearAllMocks(); mocks.local = false; mocks.authChange = null;
  mocks.read.mockResolvedValue(context); mocks.pair.mockImplementation(async () => ticket());
  bridge = { companionStatus: vi.fn<Bridge['companionStatus']>().mockResolvedValue(disabled), configureCompanion: vi.fn<Bridge['configureCompanion']>().mockResolvedValue(enabled), disableCompanion: vi.fn<Bridge['disableCompanion']>().mockResolvedValue(disabled), companionInvitation: vi.fn<Bridge['companionInvitation']>(async input => ({ qr_data_url: 'data:image/png;base64,cXItZml4dHVyZQ==', payload: { type: 'yaohe-companion', version: 1, ...input, device_id: 'device-a', endpoint: enabled.endpoint, tls_ca_pem: 'fixture-public-ca', auth: { supabase_url: 'https://cloud.example', publishable_key: 'sb_publishable_fixture' } } })) };
  window.electronAPI = bridge;
});
afterEach(() => { cleanup(); delete window.electronAPI; vi.restoreAllMocks(); });
it('enables only after fresh formal identity and shows the actual native pairing QR', async () => {
  render(<CompanionConnection />);
  fireEvent.click(await screen.findByRole('button', { name: '开启并生成配对码' }));
  const qr = await screen.findByRole('img', { name: '当前账号的手机配对二维码' });
  expect(qr).toHaveAttribute('src', 'data:image/png;base64,cXItZml4dHVyZQ==');
  expect(bridge.configureCompanion).toHaveBeenCalledWith({ address: '192.168.1.2', accessToken: 'formal-jwt', workspaceId: 'workspace-a' });
  expect(mocks.read).toHaveBeenLastCalledWith(context); expect(mocks.pair).toHaveBeenCalledWith(context);
  expect(bridge.companionInvitation.mock.calls[0][0]).toMatchObject({ purpose: 'pair', token: 'short-ticket', workspace_id: 'workspace-a' });
  expect(document.body.textContent).not.toContain('short-ticket'); expect(document.body.textContent).not.toContain('formal-jwt');
  expect(screen.getByText(/手机连接已开启/)).toBeVisible();
});
it('formal creator settings never call local member APIs or hooks', async () => {
  render(<SettingsCreators />);
  expect(await screen.findByText(/当前账号：/)).toHaveTextContent('当前账号');
  expect(mocks.localMembers).not.toHaveBeenCalled(); expect(mocks.localIdentity).not.toHaveBeenCalled();
});
it('local mode and ordinary browser never invoke a native configuration or formal pairing', async () => {
  mocks.local = true; const view = render(<CompanionConnection />);
  expect(screen.getByText(/本地开发模式不能配对手机/)).toBeVisible(); expect(mocks.read).not.toHaveBeenCalled();
  view.unmount(); mocks.local = false; delete window.electronAPI;
  render(<CompanionConnection />);
  expect(await screen.findByText(/请在曜核桌面版的此设置页/)).toBeVisible(); expect(mocks.read).not.toHaveBeenCalled(); expect(bridge.configureCompanion).not.toHaveBeenCalled();
});
it('a device member cannot enable the gateway even if they own a workspace', async () => {
  mocks.read.mockResolvedValue({ ...context, role: 'member' });
  render(<CompanionConnection />);
  expect(await screen.findByText(/手机连接由设备主账号开启或关闭/)).toBeVisible();
  expect(screen.queryByRole('button', { name: '开启并生成配对码' })).not.toBeInTheDocument(); expect(bridge.configureCompanion).not.toHaveBeenCalled();
});
it('rejects a native device mismatch before showing or configuring another host', async () => {
  bridge.companionStatus.mockResolvedValue({ ...disabled, device_id: 'other-device' }); render(<CompanionConnection />);
  expect(await screen.findByRole('alert')).toHaveTextContent('主机不一致'); expect(screen.queryByRole('button', { name: '开启并生成配对码' })).not.toBeInTheDocument();
});
it('sign-out discards a late ticket before it reaches the native QR method', async () => {
  bridge.companionStatus.mockResolvedValue(enabled);
  let resolve!: (value: ReturnType<typeof ticket>) => void; mocks.pair.mockReturnValue(new Promise(done => { resolve = done; }));
  render(<CompanionConnection />); fireEvent.click(await screen.findByRole('button', { name: '生成手机配对码' }));
  await waitFor(() => expect(mocks.pair).toHaveBeenCalled());
  act(() => mocks.authChange?.('SIGNED_OUT', null));
  await act(async () => resolve(ticket()));
  expect(bridge.companionInvitation).not.toHaveBeenCalled(); expect(screen.queryByRole('img')).not.toBeInTheDocument(); expect(screen.getByRole('alert')).toHaveTextContent('登录账号已变化');
});
it('disables the real gateway, removes the QR and offers setup again', async () => {
  bridge.companionStatus.mockResolvedValue(enabled); render(<CompanionConnection />);
  fireEvent.click(await screen.findByRole('button', { name: '生成手机配对码' })); await screen.findByRole('img');
  fireEvent.click(screen.getByRole('button', { name: '关闭手机连接' }));
  expect(await screen.findByRole('button', { name: '开启并生成配对码' })).toBeVisible(); expect(bridge.disableCompanion).toHaveBeenCalledOnce(); expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
it('expires the visible code and requires an explicit regeneration', async () => {
  bridge.companionStatus.mockResolvedValue(enabled);
  mocks.pair.mockImplementation(async () => ({ ...ticket(), expires_at: new Date(Date.now() + 200).toISOString() }));
  render(<CompanionConnection />); fireEvent.click(await screen.findByRole('button', { name: '生成手机配对码' })); await screen.findByRole('img');
  expect(await screen.findByText('配对码已过期，请重新生成。')).toBeVisible(); expect(screen.queryByRole('img')).not.toBeInTheDocument(); expect(mocks.pair).toHaveBeenCalledOnce();
});
