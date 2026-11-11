"use client";
import { Button } from "./ui/button";
import Link from "next/link";
import { isLocalAuthMode } from "@/lib/auth/routing";
import { CompanionConnection } from "./CompanionConnection";
import { useEffect, useState } from "react";
import { listLocalMembers, membersErrorMessage, type LocalMember } from "@/lib/api";
import { fetchLocalMemberIdentity, type LocalMemberIdentity } from "@/lib/local-member-auth";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";

export function SettingsCreators({ selectedMemberId }: { selectedMemberId?: string }) {
  if (!isLocalAuthMode()) return <section aria-label="创作者资料" className="settings-creators"><CompanionConnection /><p><Link href="/profile">创作偏好</Link></p></section>;
  return <><LocalSettingsCreators selectedMemberId={selectedMemberId} /><CompanionConnection /></>;
}

function LocalSettingsCreators({ selectedMemberId }: { selectedMemberId?: string }) {
  const identityState = useLocalMemberIdentity();
  const [identity, setIdentity] = useState<LocalMemberIdentity | null>(null);
  const [members, setMembers] = useState<LocalMember[]>([]);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true; setIdentity(null); setMembers([]); setError(null);
    if (!identityState.ready || identityState.error) return;
    void Promise.all([fetchLocalMemberIdentity(), listLocalMembers()]).then(([current, list]) => {
      if (active) { setIdentity(current);setMembers(list.members); }
    }).catch(cause => { if (active) setError(membersErrorMessage(cause)); });
    return () => { active = false; };
  }, [identityState.ready, identityState.error, identityState.token, retry]);
  if (!identityState.ready) return <p role="status">正在确认当前身份…</p>;
  if (identityState.error || error) return <div><p role="alert">暂时无法读取创作者资料。</p><Button variant="outline" type="button" onClick={() => { setRetry(value => value + 1); identityState.refresh(); }}>重新确认身份</Button><details><summary>读取详情</summary><p>{identityState.error ?? error}</p></details></div>;
  if (!identity) return <p role="status">正在读取创作者资料…</p>;
  const selected = selectedMemberId ? members.find(item => item.member_id === selectedMemberId) : null;
  return <section aria-label="创作者资料" className="settings-creators">
    <p>当前身份：<strong>{identity.name}</strong> · {identity.role === "owner" ? "主账号" : "成员"}</p>
    {selected ? <p>所选名册资料：{selected.name} · {selected.role === "owner" ? "主账号" : "成员"}。当前身份仍为{identity.name}。</p> : selectedMemberId ? <p>所选成员未在当前名册返回。</p> : null}
    <ul>{members.map(member => <li key={member.member_id}>{member.name} · {member.role === "owner" ? "主账号" : "成员"}</li>)}</ul>
    <p>本机保留五个成员名额，当前已登记{members.length}人。</p>
    <p><Link href="/profile">创作偏好</Link></p>
    <Link href="/members">{identity.role === "owner" ? "管理成员与共享空间" : "查看共享空间与加入方式"}</Link>
  </section>;
}
