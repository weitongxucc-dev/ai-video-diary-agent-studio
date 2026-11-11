'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { isLocalAuthMode, isPublicAuthPath } from '@/lib/auth/routing';
import { createClient } from '@/lib/supabase/client';
import { readWorkspaceSession, type WorkspaceSessionSnapshot } from '@/app/auth/workspace-session';
import { AssistantConversationProvider } from './AssistantConversationProvider';
import { Workbench } from './workbench/Workbench';
import { WorkbenchTargetProvider } from './workbench/WorkbenchTarget';
import { WorkspaceBootContext } from './WorkspaceBootContext';

function ReadyWorkbench({ children }: { children: ReactNode }) {
  return <WorkbenchTargetProvider><AssistantConversationProvider><Workbench>{children}</Workbench></AssistantConversationProvider></WorkbenchTargetProvider>;
}

export function WorkspaceSessionBoundary({ children }: { children: ReactNode }) {
  if (isLocalAuthMode()) return <ReadyWorkbench>{children}</ReadyWorkbench>;
  return <FormalWorkspaceSession>{children}</FormalWorkspaceSession>;
}

function FormalWorkspaceSession({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? '';
  const phase = isPublicAuthPath(pathname) ? 'auth' : pathname === '/onboarding' ? 'onboarding' : 'workspace';
  const [snapshot, setSnapshot] = useState<WorkspaceSessionSnapshot | null>(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const mounted = useRef(false), revision = useRef(0);
  const observedAccount = useRef<string | null>(null);
  const adopt = (value: WorkspaceSessionSnapshot | null) => { setSnapshot(value); };

  const refresh = useCallback(async () => {
    const request = ++revision.current;
    if (phaseRef.current === 'auth') { adopt(null); return; }
    try {
      const value = await readWorkspaceSession();
      if (!mounted.current || revision.current !== request) return;
      observedAccount.current = 'userId' in value ? value.userId : null;
      adopt(value);
    } catch {
      if (mounted.current && revision.current === request) adopt({ status: 'unavailable' });
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let unsubscribe: (() => void) | undefined;
    try {
      const { data } = createClient().auth.onAuthStateChange((event, session) => {
        if (event === 'INITIAL_SESSION') return;
        if (event === 'SIGNED_OUT') {
          ++revision.current; observedAccount.current = null; adopt({ status: 'signed_out' });
          queueMicrotask(() => { if (mounted.current) void refresh(); });
          return;
        }
        const accountId = session?.user.id ?? null;
        if (accountId && accountId !== observedAccount.current) {
          // SDK events can invalidate an old scope; only the server can enable a new one.
          ++revision.current; observedAccount.current = accountId; adopt(null);
          queueMicrotask(() => { if (mounted.current) void refresh(); });
        }
      });
      unsubscribe = () => data.subscription.unsubscribe();
    } catch {
      adopt({ status: 'unavailable' });
    }
    return () => { mounted.current = false; ++revision.current; unsubscribe?.(); };
  }, [refresh]);

  // Server-action sign-in and onboarding change cookies without a client SDK
  // event. Recheck those transitions, while ordinary business tabs share one epoch.
  useEffect(() => { void refresh(); }, [phase, refresh]);

  const ready = phase !== 'auth' && snapshot?.status === 'ready';
  const message = snapshot?.status === 'onboarding' ? '完成首次设置后即可使用创作助手。'
    : snapshot?.status === 'signed_out' ? '登录后即可使用创作助手。'
    : snapshot?.status === 'unavailable' ? '暂时无法确认账号与工作区，请重新核验。'
    : '正在确认账号与工作区…';
  const href = snapshot?.status === 'onboarding' ? '/onboarding' : snapshot?.status === 'signed_out' ? '/signin' : undefined;
  return <WorkspaceBootContext.Provider value={{ blocked: !ready, message, href, retry: snapshot?.status === 'unavailable' ? () => void refresh() : undefined }}>
    {ready ? <ReadyWorkbench key={`${snapshot.userId}:${snapshot.workspaceId}`}>{children}</ReadyWorkbench> : children}
  </WorkspaceBootContext.Provider>;
}
