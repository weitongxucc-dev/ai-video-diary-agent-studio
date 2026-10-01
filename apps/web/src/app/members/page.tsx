"use client";

import { AppShell } from "@/components/AppShell";
import { MembersAndSpacesWorkspace } from "@/components/MembersAndSpacesWorkspace";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";

export const dynamic = "force-dynamic";

/**
 * 身份未确认或读取失败时保留工作台外壳，不渲染成员管理内容。
 * 失败时显示实际错误并重试身份查询；未知角色按只读视图呈现。
 * 角色展示控制不代替后台权限校验。
 */
export default function MembersPage() {
  const { role, ready, error, refresh } = useLocalMemberIdentity();

  if (!ready) {
    return <AppShell><p role="status">正在确认身份…</p></AppShell>;
  }

  if (error) {
    return (
      <AppShell>
        <section role="alert">
          <strong>身份确认失败：{error}</strong>
          <p>为避免按错误身份显示成员管理控件，暂不显示成员管理内容。请重试。</p>
          <button type="button" onClick={refresh}>重试</button>
        </section>
      </AppShell>
    );
  }

  // ready 且无错误：role 可能仍是 null（异常路径），此时不谎称 owner，按只读呈现。
  return <MembersAndSpacesWorkspace role={role ?? "member"} />;
}
