import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useContext, type ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AssistantConversation } from '@/lib/types';
import type { WorkspaceSessionSnapshot } from '@/app/auth/workspace-session';

const mocks = vi.hoisted(() => ({ path: '/signin', local: false, readSession: vi.fn(), authChange: null as null | ((event: string, session: { user: { id: string } } | null) => void), unsubscribe: vi.fn(), current: vi.fn(), list: vi.fn(), get: vi.fn(), update: vi.fn(), reference: vi.fn(), saveReference: vi.fn() }));
vi.mock('next/navigation', () => ({ usePathname: () => mocks.path, useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/auth/routing', async original => ({ ...await original<typeof import('@/lib/auth/routing')>(), isLocalAuthMode: () => mocks.local }));
vi.mock('@/app/auth/workspace-session', () => ({ readWorkspaceSession: mocks.readSession }));
vi.mock('@/lib/supabase/client', () => ({ createClient: () => ({ auth: { onAuthStateChange: (callback: typeof mocks.authChange) => { mocks.authChange = callback; return { data: { subscription: { unsubscribe: mocks.unsubscribe } } }; } } }) }));
vi.mock('@/hooks/useLocalMemberIdentity', () => ({ useLocalMemberIdentity: () => ({ token: null, role: null, ready: true, error: null, refresh: vi.fn() }) }));
vi.mock('../workbench/Workbench', () => ({ Workbench: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/lib/api', () => ({ getCurrentAssistantConversation: mocks.current, listAssistantConversations: mocks.list, getAssistantConversation: mocks.get, updateAssistantConversation: mocks.update, getAssistantConversationReference: mocks.reference, saveAssistantConversationReference: mocks.saveReference, createAssistantConversation: vi.fn(), resumeAssistantConversation: vi.fn(), stopAssistantConversationTurn: vi.fn(), restoreUnpersistedAssistantTurnDraft: vi.fn() }));
import { WorkspaceSessionBoundary } from '../WorkspaceSessionBoundary';
import { AssistantConversationContext } from '../AssistantConversationContext';
import { useAssistantSelection } from '../AssistantConversationProvider';

let account = 'a';
function conversation(user = account): AssistantConversation {
  return { id: `conversation-${user}`, workspace_id: `workspace-${user}`, scope_kind: 'home', scope_id: 'home', title: `account-${user}`, draft_text: `server-draft-${user}`, draft_version: 1, created_at: '2026-10-18', updated_at: '2026-10-18', turns: [{ id: `turn-${user}`, sequence: 1, user_message: `private-input-${user}`, assistant_text: `private-message-${user}`, status: 'completed', events: [], result: null, created_at: '2026-10-18', updated_at: '2026-10-18' }] };
}
function ready(user = account): WorkspaceSessionSnapshot { return { status: 'ready', userId: user, workspaceId: `workspace-${user}` }; }
function Consumer() {
  const state = useContext(AssistantConversationContext), selection = useAssistantSelection();
  if (!state) return <p>业务会话未初始化</p>;
  return <><output aria-label="会话消息">{state.conversation?.turns.map(turn => turn.assistant_text).join(',')}</output><textarea aria-label="会话草稿" value={state.draftText} onChange={event => state.setDraftText(event.target.value)} /><output aria-label="当前引用">{selection?.items.map(item => item.label).join(',')}</output><button onClick={() => selection?.select([{ kind: 'voice', profile_id: 'voice-a', version: 'version-a', label: 'private-reference-a' }])}>引用当前声音</button></>;
}
const content = () => <WorkspaceSessionBoundary><Consumer /></WorkspaceSessionBoundary>;
beforeEach(() => {
  vi.clearAllMocks(); mocks.path = '/signin'; mocks.local = false; mocks.authChange = null; account = 'a';
  mocks.readSession.mockImplementation(async () => ready());
  mocks.current.mockImplementation(async () => conversation());
  mocks.list.mockImplementation(async () => ({ items: [conversation()] }));
  mocks.get.mockImplementation(async (id: string) => conversation(id.slice(-1)));
  mocks.update.mockImplementation(async (id: string, value: { draft_text: string; expected_draft_version: number }) => ({ ...conversation(id.slice(-1)), draft_text: value.draft_text, draft_version: value.expected_draft_version + 1 }));
  mocks.reference.mockResolvedValue({ selected_reference: null, reference_version: 0, status: 'ready', reason: null });
  mocks.saveReference.mockImplementation(async (_id: string, selected_reference: unknown) => ({ selected_reference, reference_version: 1, status: 'ready', reason: null }));
});
afterEach(cleanup);

it('does not initialize before login or onboarding, then restores the real current conversation', async () => {
  const view = render(content());
  expect(screen.getByText('业务会话未初始化')).toBeVisible(); expect(mocks.current).not.toHaveBeenCalled(); expect(mocks.readSession).not.toHaveBeenCalled();
  mocks.readSession.mockResolvedValue({ status: 'onboarding', userId: 'a' }); mocks.path = '/onboarding'; view.rerender(content());
  await waitFor(() => expect(mocks.readSession).toHaveBeenCalledOnce()); expect(mocks.current).not.toHaveBeenCalled();
  mocks.readSession.mockResolvedValue(ready()); mocks.path = '/'; view.rerender(content());
  expect(await screen.findByDisplayValue('server-draft-a')).toBeVisible(); expect(screen.getByLabelText('会话消息')).toHaveTextContent('private-message-a'); expect(mocks.current).toHaveBeenCalledTimes(1);
});

it('keeps the same conversation, unsent draft and references across resource browsing and token refresh', async () => {
  mocks.path = '/'; const view = render(content()); await screen.findByDisplayValue('server-draft-a');
  fireEvent.change(screen.getByLabelText('会话草稿'), { target: { value: 'unsent-private-draft-a' } }); fireEvent.click(screen.getByRole('button', { name: '引用当前声音' }));
  await waitFor(() => expect(screen.getByLabelText('当前引用')).toHaveTextContent('private-reference-a'));
  mocks.path = '/library'; view.rerender(content()); mocks.path = '/knowledge'; view.rerender(content());
  act(() => mocks.authChange?.('TOKEN_REFRESHED', { user: { id: 'a' } }));
  expect(screen.getByLabelText('会话草稿')).toHaveValue('unsent-private-draft-a'); expect(screen.getByLabelText('当前引用')).toHaveTextContent('private-reference-a');
  expect(mocks.readSession).toHaveBeenCalledTimes(1); expect(mocks.current).toHaveBeenCalledTimes(1);
});

it('clears old messages, draft and references on account change before enabling the new verified scope', async () => {
  mocks.path = '/'; render(content()); await screen.findByDisplayValue('server-draft-a');
  fireEvent.change(screen.getByLabelText('会话草稿'), { target: { value: 'unsent-private-draft-a' } }); fireEvent.click(screen.getByRole('button', { name: '引用当前声音' }));
  await waitFor(() => expect(screen.getByLabelText('当前引用')).toHaveTextContent('private-reference-a'));
  account = 'b';
  act(() => mocks.authChange?.('SIGNED_IN', { user: { id: 'b' } }));
  expect(screen.queryByDisplayValue('unsent-private-draft-a')).not.toBeInTheDocument(); expect(screen.queryByText('private-message-a')).not.toBeInTheDocument();
  expect(await screen.findByDisplayValue('server-draft-b')).toBeVisible(); expect(screen.getByLabelText('会话消息')).toHaveTextContent('private-message-b'); expect(screen.getByLabelText('当前引用')).toBeEmptyDOMElement();
  expect(mocks.saveReference.mock.calls.every(call => call[0] === 'conversation-a')).toBe(true);
  expect(mocks.update.mock.calls.every(call => call[0] === 'conversation-a')).toBe(true);
});

it('sign-out removes the private scope and a late old server snapshot cannot restore it', async () => {
  mocks.path = '/'; render(content()); await screen.findByDisplayValue('server-draft-a');
  mocks.readSession.mockResolvedValue({ status: 'signed_out' });
  act(() => mocks.authChange?.('SIGNED_OUT', null));
  expect(screen.queryByText('private-message-a')).not.toBeInTheDocument(); expect(screen.getByText('业务会话未初始化')).toBeVisible();
  await waitFor(() => expect(mocks.readSession).toHaveBeenCalledTimes(2)); expect(mocks.current).toHaveBeenCalledTimes(1);
});

it('ignores an old in-flight readiness response after a different account is verified', async () => {
  let resolveOld!: (value: WorkspaceSessionSnapshot) => void;
  mocks.path = '/'; mocks.readSession.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
  render(content()); await waitFor(() => expect(mocks.readSession).toHaveBeenCalledOnce());
  account = 'b'; act(() => mocks.authChange?.('SIGNED_IN', { user: { id: 'b' } })); await screen.findByDisplayValue('server-draft-b');
  await act(async () => resolveOld(ready('a')));
  expect(screen.queryByText('private-message-a')).not.toBeInTheDocument(); expect(screen.getByLabelText('会话草稿')).toHaveValue('server-draft-b'); expect(mocks.current).toHaveBeenCalledTimes(1);
});

it('discards an old account conversation response that arrives after the new account is active', async () => {
  let resolveOld!: (value: AssistantConversation) => void;
  mocks.path = '/'; mocks.current.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
  render(content()); await waitFor(() => expect(mocks.current).toHaveBeenCalledTimes(1));
  account = 'b'; act(() => mocks.authChange?.('SIGNED_IN', { user: { id: 'b' } }));
  await screen.findByDisplayValue('server-draft-b');
  await act(async () => resolveOld(conversation('a')));
  expect(screen.getByLabelText('会话消息')).toHaveTextContent('private-message-b'); expect(screen.getByLabelText('会话消息')).not.toHaveTextContent('private-message-a');
  expect(screen.getByLabelText('会话草稿')).toHaveValue('server-draft-b'); expect(screen.getByLabelText('当前引用')).toBeEmptyDOMElement();
  expect(mocks.current).toHaveBeenCalledTimes(2);
});
