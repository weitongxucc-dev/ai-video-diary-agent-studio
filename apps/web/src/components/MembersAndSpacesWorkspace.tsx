"use client";

import {
  AlertCircle, KeyRound, LoaderCircle, Plus, RefreshCw, UserPlus, UserRound, Users,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  addLocalMember, createLocalInviteCode, joinLocalMember, listLocalMembers,
  membersErrorMessage, removeLocalMember, setLocalMemberRole, type LocalMember,
} from "@/lib/api";
import { storeLocalMemberToken } from "@/lib/local-member-auth";
import { requestLocalMemberIdentityRefresh } from "@/hooks/useLocalMemberIdentity";
import styles from "./MembersAndSpacesWorkspace.module.css";

type Role = "owner" | "member";
const roleLabel: Record<Role, string> = { owner: "所有者", member: "成员" };

export function MembersAndSpacesWorkspace({ role = "owner" }: { role?: Role }) {
  const [members, setMembers] = useState<LocalMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [inviteCode, setInviteCode] = useState<string | null>(null);
  const isOwner = role === "owner";

  async function refresh() {
    setLoading(true); setError(null);
    try { setMembers((await listLocalMembers()).members); }
    catch (cause) { setError(membersErrorMessage(cause)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = String(new FormData(event.currentTarget).get("name") ?? "").trim();
    if (!name) return;
    setBusy(true); setNotice(null);
    try {
      const saved = await addLocalMember({ name });
      setNotice(`已添加成员 ${saved.name}。`); event.currentTarget.reset(); await refresh();
    } catch (cause) { setError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function removeMember(member: LocalMember) {
    setBusy(true); setNotice(null);
    try { await removeLocalMember(member.member_id); setNotice(`已移除成员 ${member.name}。`); await refresh(); }
    catch (cause) { setError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function toggleRole(member: LocalMember) {
    const next: Role = member.role === "owner" ? "member" : "owner";
    setBusy(true); setNotice(null);
    try { const updated = await setLocalMemberRole(member.member_id, next); setNotice(`${updated.name} 已设为${roleLabel[updated.role]}。`); await refresh(); }
    catch (cause) { setError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function generateInviteCode() {
    setBusy(true); setNotice(null);
    try { const response = await createLocalInviteCode(); setInviteCode(response.invite_code); setNotice("已生成本地邀请码，分享给要加入的成员。"); }
    catch (cause) { setError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function join(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const code = String(data.get("code") ?? "").trim();
    const name = String(data.get("name") ?? "").trim();
    if (!code || !name) return;
    setBusy(true); setNotice(null); setError(null);
    try {
      const joined = await joinLocalMember({ code, name });
      // 令牌必须在 join 这一刻落盘：它是唯一能拿到本人 token 的时机。不存的话，
      // 后续请求全部匿名，方案 A 下按 owner 放行——「加了人但没生效」。
      const token = typeof joined.member_token === "string" ? joined.member_token.trim() : "";
      if (!token) {
        // 后端已建成员却没回 token：不能假装成功，如实报出来。当前仍是匿名(owner)。
        setError("已加入空间，但本地服务没有返回成员令牌，当前仍以匿名(所有者)身份访问。请重启本机服务后重试。");
        return;
      }
      storeLocalMemberToken(token);
      requestLocalMemberIdentityRefresh(); // 令牌刚落盘，让权限显示收敛
      setNotice(`${joined.name} 已加入空间。`);
      event.currentTarget.reset(); await refresh();
    }
    catch (cause) { setError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  return (
    <AppShell>
      <div className={styles.page}>
        {error ? <section className={styles.error} role="alert"><AlertCircle size={18}/><div><strong>{error}</strong><p>请检查本机服务后重试。</p></div><button onClick={() => void refresh()} type="button">重试</button></section> : null}
        {notice ? <p className={styles.notice} role="status">{notice}</p> : null}

        <div className={styles.viewBar}>
          <button className={styles.secondaryButton} disabled={loading || busy} onClick={() => void refresh()} type="button"><RefreshCw size={15}/>刷新</button>
        </div>

        {loading ? <div className={styles.loading}><LoaderCircle className={styles.spinning} size={20}/>正在读取成员…</div> : null}
        {!loading && error ? <section className={styles.serviceEmpty}><AlertCircle size={22}/><div><strong>成员列表暂不可读取</strong><p>服务恢复后可继续查看和管理成员。</p></div><button onClick={() => void refresh()} type="button">重新读取</button></section> : null}

        {!loading && !error ? (
          <>
            <section className={styles.panel} aria-label="空间成员">
              <header className={styles.sectionHeader}>
                <div><span>空间成员</span><h2>{members.length} 位成员</h2><p>角色只有「所有者」与「成员」两档；成员只读，不能加人或改角色。</p></div>
              </header>
              {members.length ? (
                <ul className={styles.memberList}>
                  {members.map((member) => (
                    <li className={styles.memberRow} key={member.member_id}>
                      <span className={styles.memberIcon}><UserRound size={17}/></span>
                      <div className={styles.memberMeta}><strong>{member.name}</strong><small>加入于 {new Date(member.created_at).toLocaleDateString("zh-CN")}</small></div>
                      <span className={`${styles.badge} ${member.role === "owner" ? styles.ownerBadge : styles.memberBadge}`}>{roleLabel[member.role]}</span>
                      {isOwner ? <span className={styles.memberActions}><button disabled={busy} onClick={() => void toggleRole(member)} type="button">{member.role === "owner" ? "设为成员" : "设为所有者"}</button><button disabled={busy} onClick={() => void removeMember(member)} type="button">移除</button></span> : null}
                    </li>
                  ))}
                </ul>
              ) : <div className={styles.empty}><Users size={19}/><div><strong>还没有成员</strong><p>使用「添加成员」或分享邀请码，让新成员加入。</p></div></div>}
            </section>

            {isOwner ? (
              <section className={styles.panel} aria-label="所有者操作">
                <header className={styles.sectionHeader}><div><span>所有者操作</span><h2>添加成员与邀请</h2><p>直接添加成员，或生成一次性邀请码分享给新成员。</p></div></header>
                <form className={styles.inlineForm} onSubmit={addMember}>
                  <label><span>成员名称</span><input name="name" required placeholder="例如：小王"/></label>
                  <button className={styles.primaryButton} disabled={busy} type="submit"><Plus size={16}/>添加成员</button>
                </form>
                <div className={styles.inviteRow}>
                  {inviteCode ? <div className={styles.inviteCode}><span>邀请码</span><code>{inviteCode}</code><small>一次性；被使用或重启后失效。</small></div> : <p className={styles.hint}>还没有邀请码。</p>}
                  <button className={styles.secondaryButton} disabled={busy} onClick={() => void generateInviteCode()} type="button"><KeyRound size={15}/>{inviteCode ? "重新生成" : "生成邀请码"}</button>
                </div>
              </section>
            ) : null}

            <section className={styles.panel} aria-label="加入空间">
              <header className={styles.sectionHeader}><div><span>加入空间</span><h2>输入邀请码加入</h2><p>拿到邀请码后，输入你的名称加入本机空间。</p></div></header>
              <form className={styles.inlineForm} onSubmit={join}>
                <label><span>邀请码</span><input name="code" required placeholder="粘贴邀请码"/></label>
                <label><span>你的名称</span><input name="name" required placeholder="例如：你的名字"/></label>
                <button className={styles.primaryButton} disabled={busy} type="submit"><UserPlus size={16}/>加入</button>
              </form>
            </section>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
