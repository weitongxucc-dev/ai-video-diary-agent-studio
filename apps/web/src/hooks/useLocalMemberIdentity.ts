"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ensureLocalMemberIdentity, type LocalMemberIdentity, type LocalMemberRole } from "@/lib/local-member-auth";

export type LocalMemberIdentityState = {
  token: string | null;
  role: LocalMemberRole | null;
  ready: boolean;
  error: string | null;
  /**
   * 手动重新拉取身份（例如加入空间、令牌刚落盘之后）。
   * 形状对既有消费方保持兼容：AppShell 只解构 `role`，新增字段不影响它。
   */
  refresh: () => void;
};

/**
 * 已挂载的 `useLocalMemberIdentity` 实例登记表。
 *
 * 身份 hook 挂在 AppShell 里，而「加入空间」发生在另一个组件树（成员页），
 * 后者拿不到前者的 `refresh`。这里用模块级订阅把两者接起来：成员页在令牌落盘后
 * 调 `requestLocalMemberIdentityRefresh()`，AppShell 里的 hook 收到通知重新拉
 * whoami，角色随之收敛。这样不用改 AppShell，也不用整页 `location.reload()`。
 */
const refreshListeners = new Set<() => void>();

export function requestLocalMemberIdentityRefresh(): void {
  for (const listener of [...refreshListeners]) listener();
}

/**
 * 挂载时拉一次 whoami，暴露 { token, role, ready, refresh }。
 *
 * `ready=false` 期间调用方不要渲染身份相关 UI，避免首屏闪 owner 视图
 * （令牌在 localStorage，SSR 读不到，首帧必然先按 owner 渲染）。
 *
 * 失败（含 401 凭据无效）时如实暴露 error，不静默当作 owner 放行。
 */
export function useLocalMemberIdentity(): LocalMemberIdentityState {
  const [state, setState] = useState<Omit<LocalMemberIdentityState, "refresh">>({ token: null, role: null, ready: false, error: null });
  const mountedRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const identity: LocalMemberIdentity = await ensureLocalMemberIdentity();
      if (!mountedRef.current) return;
      setState({ token: identity.member_token, role: identity.role, ready: true, error: null });
    } catch (cause: unknown) {
      if (!mountedRef.current) return;
      setState({ token: null, role: null, ready: true, error: cause instanceof Error ? cause.message : "本地成员身份读取失败" });
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    const listener = () => { void load(); };
    refreshListeners.add(listener);
    return () => {
      mountedRef.current = false;
      refreshListeners.delete(listener);
    };
  }, [load]);

  const refresh = useCallback(() => { void load(); }, [load]);

  return { ...state, refresh };
}
