"use client";

import { useEffect, useState } from "react";
import { ensureLocalMemberIdentity, type LocalMemberIdentity, type LocalMemberRole } from "@/lib/local-member-auth";

export type LocalMemberIdentityState = {
  token: string | null;
  role: LocalMemberRole | null;
  ready: boolean;
  error: string | null;
};

/**
 * 挂载时拉一次 whoami，暴露 { token, role, ready }。
 *
 * `ready=false` 期间调用方不要渲染身份相关 UI，避免首屏闪 owner 视图
 * （令牌在 localStorage，SSR 读不到，首帧必然先按 owner 渲染）。
 *
 * 失败（含 401 凭据无效）时如实暴露 error，不静默当作 owner 放行。
 */
export function useLocalMemberIdentity(): LocalMemberIdentityState {
  const [state, setState] = useState<LocalMemberIdentityState>({ token: null, role: null, ready: false, error: null });

  useEffect(() => {
    let active = true;
    ensureLocalMemberIdentity()
      .then((identity: LocalMemberIdentity) => {
        if (!active) return;
        setState({ token: identity.member_token, role: identity.role, ready: true, error: null });
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setState({ token: null, role: null, ready: true, error: cause instanceof Error ? cause.message : "本地成员身份读取失败" });
      });
    return () => {
      active = false;
    };
  }, []);

  return state;
}
