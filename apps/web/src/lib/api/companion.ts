import { isLocalAuthMode } from '../auth/routing';
import { createClient } from '../supabase/client';

export type CompanionIdentity = {
  device_id: string;
  workspace_id: string;
  member_id: string;
  name: string | null;
  role: 'owner' | 'member';
};
export type CompanionContext = CompanionIdentity & { accessToken: string };
export type CompanionPairingTicket = {
  token: string;
  expires_at: string;
  workspace_id: string;
  device_id: string;
};
export class CompanionError extends Error {}

// This formal-only path deliberately bypasses local-member identity injection.
// The same-origin proxy replaces Authorization with its verified cookie session.
async function formalRequest<T>(path: string, accessToken: string, workspaceId?: string, method = 'GET'): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method, cache: 'no-store', redirect: 'error', credentials: 'same-origin', signal: AbortSignal.timeout(30_000),
      headers: { Authorization: `Bearer ${accessToken}`, ...(workspaceId ? { 'X-Workspace-Id': workspaceId } : {}) },
    });
  } catch {
    throw new CompanionError('无法联系本地主机，请检查桌面服务后重新读取。');
  }
  if (!response.ok) {
    throw new CompanionError(response.status === 401 ? '登录已失效，请重新登录。'
      : response.status === 403 ? '当前账号尚未获此主机与工作区的许可，请完成主机登记后重试。'
      : response.status === 409 ? '连接身份或状态已变化，请重新读取。'
      : response.status === 404 ? '当前主机尚未提供手机配对，请更新桌面后重试。'
      : '主机未完成手机连接请求，请重新读取状态后重试。');
  }
  try { return await response.json() as T; }
  catch { throw new CompanionError('主机回执无法读取，请检查桌面版本。'); }
}

export async function readCompanionContext(expected?: CompanionIdentity): Promise<CompanionContext> {
  if (isLocalAuthMode()) throw new CompanionError('本地开发身份不能开启正式手机连接。');
  const client = createClient();
  const { data, error } = await client.auth.getSession();
  const account = data.session;
  if (error || !account) throw new CompanionError('请先使用正式账号登录。');
  // Reuse the existing current-workspace projection, then explicitly select it
  // for the device/member verification. Never infer device role from workspace role.
  const foundation = await formalRequest<{ profile: { workspace_id: string } }>('/api/production/foundation', account.access_token);
  const workspaceId = foundation?.profile?.workspace_id;
  if (typeof workspaceId !== 'string' || !workspaceId) throw new CompanionError('当前创作空间未确认，请重新读取。');
  const identity = await formalRequest<CompanionIdentity>('/api/companion/session', account.access_token, workspaceId);
  if (typeof identity.device_id !== 'string' || !identity.device_id || identity.workspace_id !== workspaceId || identity.member_id !== account.user.id
    || (identity.name !== null && typeof identity.name !== 'string') || !['owner', 'member'].includes(identity.role)) {
    throw new CompanionError('主机返回的账号或工作区不一致，没有建立连接。');
  }
  const latest = await client.auth.getSession();
  if (latest.error || latest.data.session?.user.id !== identity.member_id) throw new CompanionError('登录账号已变化，请重新核验。');
  if (expected && (identity.device_id !== expected.device_id || identity.workspace_id !== expected.workspace_id || identity.member_id !== expected.member_id)) {
    throw new CompanionError('账号、主机或工作区已变化，请重新核验后再操作。');
  }
  return { ...identity, accessToken: latest.data.session.access_token };
}

export async function createCompanionPairing(context: CompanionContext): Promise<CompanionPairingTicket> {
  const ticket = await formalRequest<CompanionPairingTicket>('/api/companion/pairings', context.accessToken, context.workspace_id, 'POST');
  if (ticket.device_id !== context.device_id || ticket.workspace_id !== context.workspace_id || typeof ticket.token !== 'string' || !ticket.token
    || typeof ticket.expires_at !== 'string' || !Number.isFinite(Date.parse(ticket.expires_at)) || Date.parse(ticket.expires_at) <= Date.now()) {
    throw new CompanionError('配对回执与当前主机或空间不一致，没有显示二维码。');
  }
  return ticket;
}
