"use client";

import { MembersAndSpacesWorkspace } from "@/components/MembersAndSpacesWorkspace";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";

export const dynamic = "force-dynamic";

/**
 * 成员页按真实身份渲染。此前这里不传 `role`，而组件默认 `role="owner"`，
 * 导致 member 走 URL 直达也会看到 owner 专属控件（添加成员/移除/改角色/邀请码），
 * 点下去在后端 403——界面说的和后端做的是两回事。
 *
 * 诚实边界：
 * - `ready === false`（首帧，令牌在 localStorage、SSR 读不到）时**不渲染任何身份相关 UI**，
 *   只显示一条「正在确认身份…」的加载态，避免 member 首帧闪一下 owner 视图。
 * - 这里的只读视图只让界面不说谎，**不构成权限隔离**：方案 A 下 member 不带
 *   `X-Local-Member-Token` 仍能以 owner 通过后端，真正的约束在后端 403。
 * - 身份读取失败（ready=true 且 error）时如实展示错误并允许重试，**不静默按 owner 放行**。
 */
export default function MembersPage() {
  const { role, ready, error, refresh } = useLocalMemberIdentity();

  if (!ready) {
    return <p role="status">正在确认身份…</p>;
  }

  if (error) {
    return (
      <section role="alert">
        <strong>身份确认失败：{error}</strong>
        <p>为避免按错误身份显示成员管理控件，本页暂不渲染。请重试。</p>
        <button type="button" onClick={refresh}>重试</button>
      </section>
    );
  }

  // ready 且无错误：role 可能仍是 null（异常路径），此时不谎称 owner，按只读呈现。
  return <MembersAndSpacesWorkspace role={role ?? "member"} />;
}
