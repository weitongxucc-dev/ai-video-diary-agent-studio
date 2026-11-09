"use client";

import {
  AlertCircle, KeyRound, LoaderCircle, MoreHorizontal, Plus, RefreshCw, UserPlus, UserRound, Users,
} from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "./HomeCardDialog";
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
  const [addName, setAddName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [joinName, setJoinName] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [addNameError, setAddNameError] = useState<string | null>(null);
  const [joinErrors, setJoinErrors] = useState<{ code?: string; name?: string; submit?: string }>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [activeMember, setActiveMember] = useState<LocalMember | null>(null);
  const [removeTarget, setRemoveTarget] = useState<LocalMember | null>(null);
  const actionAnchor = useRef<HTMLButtonElement>(null);
  const formId = useId();
  const isOwner = role === "owner";
  const ownerCount = members.filter(member => member.role === "owner").length;
  const lastOwner = activeMember?.role === "owner" && ownerCount === 1;

  async function refresh() {
    setLoading(true); setError(null);
    try { setMembers((await listLocalMembers()).members); }
    catch (cause) { setError(membersErrorMessage(cause)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    setAddError(null);
    setAddNameError(name ? null : "请填写成员名称。");
    if (!name) { form.querySelector<HTMLInputElement>('[name="name"]')?.focus(); return; }
    setBusy(true); setNotice(null); setActionError(null);
    try {
      const saved = await addLocalMember({ name });
      setNotice(`已添加成员 ${saved.name}。`); form.reset(); setAddName(""); await refresh();
    } catch (cause) { setAddError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function removeMember(member: LocalMember) {
    setBusy(true); setNotice(null); setActionError(null);
    try { await removeLocalMember(member.member_id); setRemoveTarget(null); setActiveMember(null); setNotice(`已移除成员 ${member.name}。`); await refresh(); }
    catch (cause) { setActionError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function toggleRole(member: LocalMember) {
    const next: Role = member.role === "owner" ? "member" : "owner";
    setActiveMember(null); setBusy(true); setNotice(null); setActionError(null);
    try { const updated = await setLocalMemberRole(member.member_id, next); setNotice(`${updated.name} 已设为${roleLabel[updated.role]}。`); await refresh(); }
    catch (cause) { setActionError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function generateInviteCode() {
    setBusy(true); setNotice(null); setActionError(null);
    try { const response = await createLocalInviteCode(); setInviteCode(response.invite_code); setNotice("已生成本地邀请码，分享给要加入的成员。"); }
    catch (cause) { setActionError(membersErrorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function join(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const code = String(data.get("code") ?? "").trim();
    const name = String(data.get("name") ?? "").trim();
    setJoinErrors({ code: code ? undefined : "请填写邀请码。", name: name ? undefined : "请填写你的名称。" });
    if (!code || !name) { form.querySelector<HTMLInputElement>(!code ? '[name="code"]' : '[name="name"]')?.focus(); return; }
    setBusy(true); setNotice(null); setActionError(null);
    try {
      const joined = await joinLocalMember({ code, name });
      // 令牌必须在 join 这一刻落盘：它是唯一能拿到本人 token 的时机。不存的话，
      // 后续请求全部匿名，方案 A 下按 owner 放行——「加了人但没生效」。
      const token = typeof joined.member_token === "string" ? joined.member_token.trim() : "";
      if (!token) {
        // 后端已建成员却没回 token：不能假装成功，如实报出来。当前仍是匿名(owner)。
        setJoinErrors({ submit: "已加入空间，但本地服务没有返回成员令牌，当前仍以匿名(所有者)身份访问。请重启本机服务后重试。" });
        return;
      }
      storeLocalMemberToken(token);
      requestLocalMemberIdentityRefresh(); // 令牌刚落盘，让权限显示收敛
      setNotice(`${joined.name} 已加入空间。`);
      form.reset(); setJoinCode(""); setJoinName(""); await refresh();
    }
    catch (cause) { setJoinErrors({ submit: membersErrorMessage(cause) }); }
    finally { setBusy(false); }
  }

  return (
    <AppShell>
      <div className={styles.page}>
        {error ? <section className={styles.error} role="alert"><AlertCircle size={18}/><div><strong>{error}</strong><p>请检查本机服务后重试。</p></div><button onClick={() => void refresh()} type="button">重试</button></section> : null}
        {notice ? <p className={styles.notice} role="status">{notice}</p> : null}
        {actionError && !removeTarget ? <p className={styles.fieldError} role="alert">{actionError}</p> : null}

        <div className={styles.viewBar}>
          <button className={styles.secondaryButton} disabled={loading || busy} onClick={() => void refresh()} type="button"><RefreshCw size={15}/>刷新</button>
        </div>

        {loading ? <div className={styles.loading}><LoaderCircle className={styles.spinning} size={20}/>正在读取成员…</div> : null}
        {!loading && error ? <section className={styles.serviceEmpty}><AlertCircle size={22}/><div><strong>成员列表暂不可读取</strong><p>服务恢复后可继续查看和管理成员。</p></div><button onClick={() => void refresh()} type="button">重新读取</button></section> : null}

        {!loading && !error ? (
          <>
            <section className={styles.panel} aria-label="空间成员">
              <header className={styles.sectionHeader}>
                <div><h2>{members.length} 位成员</h2><p>{isOwner ? "所有者可添加、邀请和管理成员；至少保留一位所有者。" : "你可以查看成员；添加、邀请和角色管理由所有者操作。"}</p></div>
              </header>
              {members.length ? (
                <ul className={styles.memberList}>
                  {members.map((member) => (
                    <li className={styles.memberRow} key={member.member_id}>
                      <span className={styles.memberIcon}><UserRound size={17}/></span>
                      <div className={styles.memberMeta}><strong>{member.name}</strong><small>加入于 {new Date(member.created_at).toLocaleDateString("zh-CN")}</small></div>
                      <span className={`${styles.badge} ${member.role === "owner" ? styles.ownerBadge : styles.memberBadge}`}>{roleLabel[member.role]}</span>
                      {isOwner ? <span className={styles.memberActions}><button aria-label={`管理成员 ${member.name}`} aria-haspopup="dialog" aria-expanded={activeMember?.member_id === member.member_id} title={`管理成员 ${member.name}`} disabled={busy} onClick={event => { actionAnchor.current = event.currentTarget; setActiveMember(member); }} type="button"><MoreHorizontal size={16}/></button></span> : null}
                    </li>
                  ))}
                </ul>
              ) : <div className={styles.empty}><Users size={19}/><div><strong>还没有成员</strong><p>使用「添加成员」或分享邀请码，让新成员加入。</p></div></div>}
            </section>

            {isOwner ? (
              <section className={styles.panel} aria-label="所有者操作">
                <header className={styles.sectionHeader}><div><h2>添加成员与邀请</h2><p>直接添加成员，或生成一次性邀请码分享给新成员。</p></div></header>
                <form className={styles.inlineForm} onSubmit={addMember} noValidate aria-label="添加成员">
                  <label><span>成员名称<span aria-hidden="true">（必填）</span></span><input aria-label="成员名称" name="name" required placeholder="例如：小王" aria-invalid={!!addNameError} value={addName} aria-describedby={`${formId}-add-name`} onChange={event => { setAddName(event.target.value); setAddNameError(null); setAddError(null); }}/><small className={addNameError ? styles.fieldError : styles.hint} id={`${formId}-add-name`} role={addNameError ? "alert" : undefined}>{addNameError ?? "填写名称后可添加成员。"}</small></label>
                  <button className={styles.primaryButton} disabled={busy || !addName.trim()} type="submit"><Plus size={16}/>添加成员</button>
                  {addError ? <p className={styles.formError} role="alert">{addError}</p> : null}
                </form>
                <div className={styles.inviteRow}>
                  {inviteCode ? <div className={styles.inviteCode}><span>邀请码</span><code>{inviteCode}</code><small>一次性；被使用或重启后失效。</small></div> : <p className={styles.hint}>还没有邀请码。</p>}
                  <button className={styles.secondaryButton} disabled={busy} onClick={() => void generateInviteCode()} type="button"><KeyRound size={15}/>{inviteCode ? "重新生成" : "生成邀请码"}</button>
                </div>
              </section>
            ) : null}

            <section className={styles.panel} aria-label="加入空间">
              <header className={styles.sectionHeader}><div><h2>输入邀请码加入</h2><p>拿到邀请码后，输入你的名称加入本机空间。</p></div></header>
              <form className={styles.inlineForm} onSubmit={join} noValidate aria-label="加入空间">
                <label><span>邀请码<span aria-hidden="true">（必填）</span></span><input aria-label="邀请码" name="code" required placeholder="粘贴邀请码" aria-invalid={!!joinErrors.code} value={joinCode} aria-describedby={`${formId}-join-code`} onChange={event => { setJoinCode(event.target.value); setJoinErrors(value => ({ ...value, code: undefined, submit: undefined })); }}/><small className={joinErrors.code ? styles.fieldError : styles.hint} id={`${formId}-join-code`} role={joinErrors.code ? "alert" : undefined}>{joinErrors.code ?? "使用所有者提供的一次性邀请码。"}</small></label>
                <label><span>你的名称<span aria-hidden="true">（必填）</span></span><input aria-label="你的名称" name="name" required placeholder="例如：你的名字" aria-invalid={!!joinErrors.name} value={joinName} aria-describedby={`${formId}-join-name`} onChange={event => { setJoinName(event.target.value); setJoinErrors(value => ({ ...value, name: undefined, submit: undefined })); }}/><small className={joinErrors.name ? styles.fieldError : styles.hint} id={`${formId}-join-name`} role={joinErrors.name ? "alert" : undefined}>{joinErrors.name ?? "填写邀请码和名称后可加入。"}</small></label>
                <button className={styles.primaryButton} disabled={busy || !joinCode.trim() || !joinName.trim()} type="submit"><UserPlus size={16}/>加入</button>
                {joinErrors.submit ? <p className={styles.formError} role="alert">{joinErrors.submit}</p> : null}
              </form>
            </section>
          </>
        ) : null}
        {activeMember ? <HomeCardDialog kind="selection" title={`管理成员 ${activeMember.name}`} anchor={actionAnchor} onClose={() => { setActiveMember(null); setRemoveTarget(null); }}>
          <div className={styles.actionMenu}>
            <button disabled={busy || lastOwner} onClick={() => void toggleRole(activeMember)} type="button">{activeMember.role === "owner" ? "设为成员" : "设为所有者"}</button>
            <button disabled={busy || lastOwner} onClick={() => { setActionError(null); setRemoveTarget(activeMember); }} type="button">移除</button>
            {lastOwner ? <p>至少保留一位所有者，暂不能降为成员或移除。</p> : null}
          </div>
        {removeTarget ? <HomeCardDialog kind="confirmation" title="移除成员" onClose={() => { setRemoveTarget(null); setActionError(null); }} footer={<><button data-card-cancel disabled={busy} onClick={() => { setRemoveTarget(null); setActionError(null); }} type="button">取消</button><button disabled={busy} onClick={() => void removeMember(removeTarget)} type="button">确认移除</button></>}>
            <p>移除成员「{removeTarget.name}」？该成员的本机成员令牌将失效，已有内容不会由此自动删除。</p>
            {actionError ? <p className={styles.fieldError} role="alert">{actionError}</p> : null}
        </HomeCardDialog> : null}
        </HomeCardDialog> : null}
      </div>
    </AppShell>
  );
}
