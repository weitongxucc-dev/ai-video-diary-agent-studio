"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import { QrCode, RefreshCw, Smartphone } from 'lucide-react';
import { isLocalAuthMode } from '@/lib/auth/routing';
import { createClient } from '@/lib/supabase/client';
import { CompanionError, createCompanionPairing, readCompanionContext, type CompanionContext } from '@/lib/api/companion';
import type { CompanionInvitationResult, CompanionStatus } from '@/lib/directoryPicker';
import { Button } from './ui/button';
import { AppSelect, AppSelectOption } from './AppSelect';
import styles from './CompanionConnection.module.css';

type Bridge = Required<Pick<NonNullable<Window['electronAPI']>, 'companionStatus' | 'configureCompanion' | 'disableCompanion' | 'companionInvitation'>>;
type PairingCode = { dataUrl: string; expiresAt: string; expired: boolean };
function desktopBridge(): Bridge | null {
  const bridge = window.electronAPI;
  return bridge && ['companionStatus', 'configureCompanion', 'disableCompanion', 'companionInvitation'].every(key => typeof bridge[key as keyof typeof bridge] === 'function') ? bridge as Bridge : null;
}
function assertStatus(status: CompanionStatus, context: CompanionContext) {
  if (status.device_id !== context.device_id || !Array.isArray(status.addresses) || typeof status.enabled !== 'boolean' || (status.enabled && (!status.endpoint || !status.endpoint.startsWith('https://')))) {
    throw new CompanionError('桌面连接状态与已核验的主机不一致，请重新读取。');
  }
}
function checkedCode(result: CompanionInvitationResult, ticket: Awaited<ReturnType<typeof createCompanionPairing>>, status: CompanionStatus): PairingCode {
  const payload = result.payload;
  if (payload.type !== 'yaohe-companion' || payload.version !== 1 || payload.purpose !== 'pair' || payload.token !== ticket.token || payload.expires_at !== ticket.expires_at || payload.workspace_id !== ticket.workspace_id || payload.device_id !== ticket.device_id || payload.endpoint !== status.endpoint || !result.qr_data_url.startsWith('data:image/png;base64,')) {
    throw new CompanionError('二维码回执与当前配对不一致，没有显示二维码。');
  }
  return { dataUrl: result.qr_data_url, expiresAt: ticket.expires_at, expired: Date.parse(ticket.expires_at) <= Date.now() };
}

export function CompanionConnection() {
  if (isLocalAuthMode()) return <section className={styles.section} aria-label="手机连接"><h2>手机连接</h2><p>本地开发模式不能配对手机。请使用正式账号版本并完成主机登记。</p></section>;
  return <FormalCompanionConnection />;
}

function FormalCompanionConnection() {
  const [surface, setSurface] = useState<'checking' | 'desktop' | 'browser'>('checking');
  const [identity, setIdentity] = useState<CompanionContext | null>(null);
  const identityRef = useRef<CompanionContext | null>(null);
  const [status, setStatus] = useState<CompanionStatus | null>(null);
  const [address, setAddress] = useState('');
  const [code, setCode] = useState<PairingCode | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef(0), mounted = useRef(false);
  const current = (generation: number) => mounted.current && operation.current === generation;
  const adopt = (value: CompanionContext | null) => { identityRef.current = value; setIdentity(value); };

  const load = useCallback(async () => {
    const bridge = desktopBridge();
    if (!bridge) return;
    const generation = ++operation.current;
    setBusy('正在核验账号与主机'); setError(null); setCode(null); adopt(null); setStatus(null);
    try {
      const [context, actual] = await Promise.all([readCompanionContext(), bridge.companionStatus()]);
      if (!current(generation)) return;
      assertStatus(actual, context);
      adopt(context); setStatus(actual);
      setAddress(previous => actual.addresses.includes(previous) ? previous : actual.addresses.length === 1 ? actual.addresses[0] : '');
    } catch (cause) {
      if (current(generation)) setError(cause instanceof CompanionError ? cause.message : '无法读取桌面连接，请确认桌面服务正常后重试。');
    } finally { if (current(generation)) setBusy(null); }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const bridge = desktopBridge();
    setSurface(bridge ? 'desktop' : 'browser');
    if (!bridge) return () => { mounted.current = false; ++operation.current; };
    void load();
    let unsubscribe: (() => void) | undefined;
    try {
      const { data } = createClient().auth.onAuthStateChange((event, session) => {
        if (event === 'SIGNED_OUT' || (identityRef.current && session?.user.id !== identityRef.current.member_id)) {
          ++operation.current; adopt(null); setCode(null); setStatus(null); setBusy(null);
          setError('登录账号已变化，请重新核验后配对手机。');
        }
      });
      unsubscribe = () => data.subscription.unsubscribe();
    } catch { /* The context read reports missing formal login configuration. */ }
    return () => { mounted.current = false; ++operation.current; unsubscribe?.(); };
  }, [load]);

  useEffect(() => {
    if (!code || code.expired) return;
    const timer = window.setTimeout(() => setCode(value => value?.expiresAt === code.expiresAt ? { ...value, expired: true } : value), Math.max(0, Date.parse(code.expiresAt) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [code]);

  async function perform(action: 'enable' | 'pair' | 'disable') {
    const bridge = desktopBridge(), expected = identityRef.current;
    if (busy || !bridge || !expected) return;
    const generation = ++operation.current;
    setBusy(action === 'disable' ? '正在关闭手机连接' : '正在核验并生成配对码'); setCode(null); setError(null);
    try {
      const context = await readCompanionContext(expected);
      if (!current(generation)) return;
      let actual = await bridge.companionStatus();
      if (!current(generation)) return;
      assertStatus(actual, context);
      if (action === 'disable' || action === 'enable') {
        if (context.role !== 'owner') throw new CompanionError('只有已核验的设备主账号可以开启或关闭手机连接。');
        if (action === 'enable') {
          if (actual.enabled || !actual.addresses.includes(address)) throw new CompanionError('局域网地址或连接状态已变化，请重新读取。');
          actual = await bridge.configureCompanion({ address, accessToken: context.accessToken, workspaceId: context.workspace_id });
        } else {
          actual = await bridge.disableCompanion();
        }
        if (!current(generation)) return;
        assertStatus(actual, context);
        if (actual.enabled !== (action === 'enable')) throw new CompanionError('桌面尚未确认连接变更，请重新读取。');
      }
      adopt(context); setStatus(actual);
      if (action !== 'disable') {
        if (!actual.enabled) throw new CompanionError('请先由设备主账号开启手机连接。');
        const ticket = await createCompanionPairing(context);
        if (!current(generation)) return;
        const result = await bridge.companionInvitation({ purpose: 'pair', token: ticket.token, expires_at: ticket.expires_at, workspace_id: ticket.workspace_id });
        if (!current(generation)) return;
        setCode(checkedCode(result, ticket, actual));
      }
    } catch (cause) {
      if (current(generation)) setError(cause instanceof CompanionError ? cause.message : '桌面尚未确认这次操作，请重新读取连接状态后重试。');
    } finally { if (current(generation)) setBusy(null); }
  }

  return <section className={styles.section} aria-label="手机连接" aria-busy={Boolean(busy)}>
    <div className={styles.heading}><h2><Smartphone aria-hidden size={18} />手机连接</h2>{surface === 'desktop' ? <Button variant="ghost" disabled={Boolean(busy)} onClick={() => void load()}><RefreshCw aria-hidden />重新读取</Button> : null}</div>
    {surface === 'checking' ? <p role="status">正在确认桌面入口…</p> : surface === 'browser' ? <p>请在曜核桌面版的此设置页开启手机连接。普通浏览器不能生成这台电脑的可信配对码。</p> : <>
      {identity ? <p>当前账号：<strong>{identity.name ?? '已验证账号'}</strong> · {identity.role === 'owner' ? '设备主账号' : '设备成员'}</p> : null}
      {busy ? <p role="status">{busy}…</p> : null}
      {error ? <p className={styles.error} role="alert">{error}</p> : null}
      {identity && status ? <>
        <p>{status.enabled ? '手机连接已开启。请用同一账号在手机上登录并扫码配对。' : '将手机与电脑接入同一局域网，再开启连接。'}</p>
        {status.enabled ? <p className={styles.endpoint}>电脑地址：{status.endpoint}</p> : identity.role === 'owner' ? <label className={styles.address}>局域网地址<AppSelect aria-label="局域网地址" value={address} disabled={Boolean(busy) || status.addresses.length === 0} onValueChange={setAddress}><AppSelectOption value="" disabled>选择手机所在网络的电脑地址</AppSelectOption>{status.addresses.map(value => <AppSelectOption key={value} value={value}>{value}</AppSelectOption>)}</AppSelect></label> : null}
        {!status.enabled && status.addresses.length === 0 ? <p>没有可用的局域网地址，请检查电脑网络后重新读取。</p> : null}
        {identity.role !== 'owner' ? <p>手机连接由设备主账号开启或关闭。已开启时，你可以生成自己账号的配对码。</p> : null}
        <div className={styles.actions}>
          {!status.enabled && identity.role === 'owner' ? <Button disabled={Boolean(busy) || !address} onClick={() => void perform('enable')}><QrCode aria-hidden />开启并生成配对码</Button> : null}
          {status.enabled ? <Button disabled={Boolean(busy)} onClick={() => void perform('pair')}><QrCode aria-hidden />{code ? '重新生成配对码' : '生成手机配对码'}</Button> : null}
          {status.enabled && identity.role === 'owner' ? <Button variant="outline" disabled={Boolean(busy)} onClick={() => void perform('disable')}>关闭手机连接</Button> : null}
        </div>
        {code ? <div className={styles.pairing}>{code.expired ? <p role="status">配对码已过期，请重新生成。</p> : <><img className={styles.qr} src={code.dataUrl} width={240} height={240} alt="当前账号的手机配对二维码" /><div><h3>用曜核手机版扫描</h3><p>有效至 {new Date(code.expiresAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}。手机仍需使用同一账号登录。</p><p>配对后可发送素材与录音、查看有权访问的作品。关闭连接后，手机无法再通过此地址访问电脑。</p></div></>}</div> : null}
      </> : null}
    </>}
  </section>;
}
