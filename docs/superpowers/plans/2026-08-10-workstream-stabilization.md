# AI 视频日记工作线程稳定化执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans` 逐项执行本计划；任务生命周期、派工、完成报告和终端回收必须以 Orca orchestration 为权威，不得用普通 Agent 替代。

**Goal:** 在不提交、不合并、不修改 `main` 的前提下，完成 backend、frontend、desktop 三个工作面的验证收口，并由 reviewer 独立复验。

**Architecture:** 先并行稳定化三个开发面，再以任务依赖阻止 reviewer 提前启动。Coordinator 负责恢复失联 Desktop 任务、处理 Worker 消息、创建定向返修任务和汇总最终结果；开发 Worker 只修改各自边界，reviewer 只读审查和复验。

**Tech Stack:** Orca 1.4.177 orchestration、FastAPI/Python/uv/pytest、Next.js 15/React 19/pnpm、Electron 33/Node.js、Git worktrees。

---

## 文件与工作区地图

| 工作面 | Worktree / 分支 | 主要文件边界 | 主要验证 |
|---|---|---|---|
| Backend | `/Users/Zhuanz/orca/workspaces/AI视频日记/backend` / `weitongxucc-dev/backend` | `apps/api/app/config.py`, `main.py`, `runtime_health.py`, `schemas.py`, `video.py`, `apps/api/tests/`, `apps/api/pyproject.toml`, `apps/api/pyinstaller.spec`, `apps/api/server.py` | `uv run pytest`, `ruff`（若仓库已有配置/工具） |
| Frontend | `/Users/Zhuanz/orca/workspaces/AI视频日记/frontend` / `weitongxucc-dev/frontend` | `apps/web/src/app/`, `apps/web/src/components/`, `apps/web/src/lib/`, `apps/web/package.json`, 根 `package.json` 的 Web 检查脚本 | Web lint/build、workflow contract、pixel-style contract |
| Desktop | `/Users/Zhuanz/orca/workspaces/AI视频日记/coordinator` / `weitongxucc-dev/coordinator` | `apps/desktop/main.js`, `apps/desktop/preload.js`, `apps/desktop/package.json`, `apps/desktop/assets/` | Node 语法、IPC 通道契约、Electron 配置和启动失败路径 |
| Review | `/Users/Zhuanz/orca/workspaces/AI视频日记/reviewer` / `weitongxucc-dev/reviewer` | 只读上述三个工作区；报告写入 `docs/ai-video-diary-review-2026-08-08.md` 或 Orca report body | 独立正确性、安全性、契约和回归审查 |

当前 frontend 的实际改动包括 `apps/web/src/app/actions.ts`、`draft/page.tsx`、`edit-plan/page.tsx`、`globals.css`、`layout.tsx`、`learning/page.tsx`、`page.tsx`、`script/page.tsx`、`settings/page.tsx`、`sources/page.tsx`、`templates/page.tsx`、`topics/page.tsx`、`video/page.tsx`、`components/AppShell.tsx`、`components/ScriptSectionEditor.tsx`、`lib/api.ts`、`lib/types.ts`，以及当前未跟踪的加载态、错误页、历史页、欢迎页和辅助组件；Worker 必须以实际 `git status` 为准，不得丢弃这些已有改动。

---

### Task 1: 预检 Orca 与 Git 状态

**Files:**
- Inspect: `docs/superpowers/specs/2026-08-10-workstream-stabilization-design.md`
- Inspect: `apps/api/`, `apps/web/`, `apps/desktop/`

- [ ] **Step 1: Confirm runtime and current Run**

```bash
orca status --json
orca orchestration task-list --brief --json
orca terminal list --json
```

Expected: runtime `state=ready`；Run `run_29e6d5c997c9` 可见；确认 backend、frontend、reviewer 当前终端句柄仍有效，若句柄已变更，按 worktree 重新解析唯一替代句柄。

- [ ] **Step 2: Snapshot all four worktrees without mutation**

```bash
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/backend' status --short --branch
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/frontend' status --short --branch
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/coordinator' status --short --branch
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/reviewer' status --short --branch
```

Expected: 只记录现状；禁止 `reset`、`clean`、`checkout` 或删除任何既有改动。

---

### Task 2: 恢复失联 Desktop 任务

**Files:**
- No source change
- Orca state: `task_a0d927ae206b`, stale Dispatch `ctx_fde095a4755b`

- [ ] **Step 1: Verify stale provenance**

```bash
orca orchestration dispatch-show --task task_a0d927ae206b --json
orca orchestration worker-show --dispatch ctx_fde095a4755b --json
```

Expected: Task 仍为 `dispatched`，原 Worker 无可用生命周期；不要依据终端尾部文字直接标记成功。

- [ ] **Step 2: Close the old attempt as recovery failure**

```bash
orca orchestration task-update \
  --id task_a0d927ae206b \
  --status failed \
  --result '{"outcome":"failed","reason":"stale dispatch; original worker terminal is unavailable and no authoritative worker_done was received; code must be independently revalidated"}' \
  --json
```

Expected: 旧尝试变为 `failed`，结果明确保留失联原因；不得把旧任务更新为 `completed`。如果运行时拒绝在残留 Dispatch 仍关联时更新 Task，先执行无资源副作用的放弃操作，再重试同一条 `task-update`：

```bash
orca orchestration worker-abandon --dispatch ctx_fde095a4755b --json
orca orchestration task-update \
  --id task_a0d927ae206b \
  --status failed \
  --result '{"outcome":"failed","reason":"stale dispatch abandoned; original worker terminal is unavailable and no authoritative worker_done was received; code must be independently revalidated"}' \
  --json
```

- [ ] **Step 3: Verify the recovery state**

```bash
orca orchestration task-list --brief --json
```

Expected: `task_a0d927ae206b` 为 `failed`，尚未创建新的 Desktop 稳定化任务。

---

### Task 3: Create the three independent stabilization Tasks

**Files:**
- No source change
- Orca state: three new Tasks in Run `run_29e6d5c997c9`

- [ ] **Step 1: Create backend Task**

```bash
orca orchestration task-create --spec 'Stabilize the existing backend worktree. Inspect every current modified and untracked file under apps/api without discarding changes. Run the repository backend test command (cd apps/api && uv run pytest), run the repository-configured ruff check when available, fix only correctness/test/lint/package issues in the backend boundary, and report exact files, commands, results, and remaining risks. Do not commit or merge.' --json
```

- [ ] **Step 2: Create frontend Task**

```bash
orca orchestration task-create --spec 'Stabilize the existing frontend worktree. Inspect all current modified and untracked files under apps/web without discarding changes. Run corepack pnpm run lint:web, corepack pnpm run build:web, corepack pnpm run test:web-contract, and corepack pnpm run test:pixel-style. Fix only frontend correctness, type, build, interaction, accessibility, or contract issues in the frontend boundary, then report exact files, commands, results, and remaining risks. Do not commit or merge.' --json
```

- [ ] **Step 3: Create replacement Desktop Task**

```bash
orca orchestration task-create --spec 'Recover and stabilize the Desktop worktree in the coordinator worktree. Inspect apps/desktop/main.js, apps/desktop/preload.js, apps/desktop/package.json, and assets without modifying coordinator orchestration files. Verify all preload ipcRenderer.invoke channels have matching ipcMain.handle registrations, validate open-path input handling, verify API startup failure does not create a window, run node --check on main.js and preload.js, and run any existing Electron/package validation available. Fix only apps/desktop issues, report exact files, commands, validation coverage, test gaps, and remaining risks. Do not commit or merge.' --json
```

Record the three returned Task IDs exactly. In any later shell block, resolve them from their unique task specs instead of copying opaque IDs manually:

```bash
backend_task_id="$(orca orchestration task-list --json | jq -r '.result.tasks[] | select(.spec | startswith("Stabilize the existing backend worktree.")) | .id')"
frontend_task_id="$(orca orchestration task-list --json | jq -r '.result.tasks[] | select(.spec | startswith("Stabilize the existing frontend worktree.")) | .id')"
desktop_task_id="$(orca orchestration task-list --json | jq -r '.result.tasks[] | select(.spec | startswith("Recover and stabilize the Desktop worktree")) | .id')"
```

The command output is the only source of truth for later dispatch and dependency creation.

---

### Task 4: Dispatch backend, frontend, and Desktop in parallel

**Files:**
- No source change
- Worker terminals: existing backend/frontend terminals plus one fresh Desktop terminal

- [ ] **Step 1: Dispatch backend to the existing backend terminal**

Use the backend Task ID returned by Task 3 and the currently resolved backend handle (at the time of planning: `term_ee0aad51-e29e-44a1-9c6c-f019c6cb3f3a`):

```bash
orca orchestration worker-start --task $backend_task_id --terminal term_ee0aad51-e29e-44a1-9c6c-f019c6cb3f3a --json
```

If that handle is stale, resolve the sole connected agent terminal for the backend worktree with `orca terminal list --json` and use only the replacement handle.

- [ ] **Step 2: Dispatch frontend to the existing frontend terminal**

Use the frontend Task ID returned by Task 3 and the currently resolved frontend handle (at the time of planning: `term_015a56cc-cd48-481b-aa8c-b1609ebba381`):

```bash
orca orchestration worker-start --task $frontend_task_id --terminal term_015a56cc-cd48-481b-aa8c-b1609ebba381 --json
```

If that handle is stale, resolve the sole connected agent terminal for the frontend worktree and use only the replacement handle.

- [ ] **Step 3: Create and start a fresh Desktop terminal in the current worktree**

```bash
orca orchestration worker-start --task $desktop_task_id --worktree current --agent claude --json
```

Read the returned receipt. Use the returned `agentTerminalHandle` as the sole Desktop Worker handle. Do not create a new worktree and do not add a second fallback shell.

- [ ] **Step 4: Confirm all three Dispatches**

```bash
orca orchestration task-list --brief --json
orca orchestration dispatch-show --task $backend_task_id --json
orca orchestration dispatch-show --task $frontend_task_id --json
orca orchestration dispatch-show --task $desktop_task_id --json
```

Expected: all three tasks are `dispatched`; no reviewer Dispatch exists yet.

---

### Task 5: Supervise the stabilization wave

**Files:**
- No source change
- Orca messages and Dispatch lifecycle only

- [ ] **Step 1: Wait on typed completion events**

```bash
orca orchestration check --wait \
  --types worker_done,escalation,question \
  --timeout-ms 900000 \
  --json
```

Do not use sleep/poll loops. A timeout with no messages is a checkpoint; inspect `task-list` and terminal liveness, then issue another bounded wait.

- [ ] **Step 2: Process every returned message**

For `question`, reply through the original message ID:

```bash
orca orchestration reply --id "$message_id" --body "$answer" --json
```

For accepted `worker_done`, verify its `taskId`, `dispatchId`, explicit outcome, files, and report. Do not manually mark a successfully reported task completed; Orca does that automatically.

- [ ] **Step 3: Release settled Workers**

After each accepted succeeded or failed `worker_done`, inspect the Worker and release the exact completed Dispatch unless it is immediately reused:

```bash
orca orchestration worker-release --dispatch "$dispatch_id" --json
```

Do not release on timeout, heartbeat, question, escalation, or stale/rejected completion.

- [ ] **Step 4: Confirm the wave is complete**

```bash
orca orchestration task-list --status completed --brief --json
orca orchestration task-list --brief --json
```

Proceed only when the three new stabilization Tasks are completed and no one remains `dispatched` or `failed` unexpectedly.

---

### Task 6: Create and dispatch the dependent Reviewer Task

**Files:**
- Inspect: `apps/api/`, `apps/web/`, `apps/desktop/`
- Review report: `docs/ai-video-diary-review-2026-08-08.md` or the returned Orca report

- [ ] **Step 1: Create Reviewer Task with exact dependencies**

```bash
orca orchestration task-create \
  --spec 'Independently review the completed backend, frontend, and desktop stabilization changes. Read the exact current worktrees, do not edit business files, and report severity, file/line, reproducible failure scenario, expected versus actual behavior, and minimal remediation. Check correctness, API/type/IPC contracts, error paths, security-sensitive input/path handling, regression coverage, and credibility of reported tests. If a blocking finding exists, identify the owning worktree; otherwise report a clean review. Do not commit or merge.' \
  --deps "[\"$backend_task_id\",\"$frontend_task_id\",\"$desktop_task_id\"]" \
  --json
```

Expected: Reviewer Task is `blocked` until all three dependencies are completed, then becomes `ready`.

- [ ] **Step 2: Dispatch reviewer only after it is ready**

Use the existing reviewer terminal after re-resolving its current handle (the previously observed replacement was `term_8c91aa7c-cae8-406d-9f47-ee2ec2631c2c`):

```bash
orca orchestration worker-start --task $reviewer_task_id --terminal term_8c91aa7c-cae8-406d-9f47-ee2ec2631c2c --json
```

If stale, resolve the connected reviewer worktree terminal and use only that replacement. Do not dispatch reviewer to the coordinator worktree for implementation.

---

### Task 7: Handle review findings and re-verification

**Files:**
- Modify only the owning Worker worktree when a finding is confirmed
- Review output: `docs/ai-video-diary-review-2026-08-08.md` or Orca report

- [ ] **Step 1: Wait for reviewer completion**

```bash
orca orchestration check --wait \
  --types worker_done,escalation,question \
  --timeout-ms 900000 \
  --json
```

Process and acknowledge the full Delivery before waiting again.

- [ ] **Step 2: Classify each finding**

Keep only findings with a concrete file/line and reproducible failure scenario. Mark each as blocking, non-blocking, or rejected with a reason. Do not ask reviewer to edit files.

- [ ] **Step 3: Dispatch targeted repairs when needed**

For each confirmed blocking finding, create a new Task whose spec names the exact owning worktree, file, failure scenario, expected behavior, required regression check, and no-commit boundary. Start it on the original owner thread only after checking the terminal is available.

- [ ] **Step 4: Re-run the affected checks**

The repair Worker must report the exact command and result for the affected suite. Reviewer then rechecks only the repaired findings and sends a final completion report.

- [ ] **Step 5: Release every settled repair/review Dispatch**

```bash
orca orchestration worker-release --dispatch "$settled_dispatch_id" --json
```

Do not leave completed Workers live merely for inspection.

---

### Task 8: Final verification and handoff report

**Files:**
- Inspect only: all four worktrees and Orca Run state
- Do not modify source or create commits

- [ ] **Step 1: Confirm task and delivery cleanliness**

```bash
orca orchestration task-list --brief --json
orca orchestration check --peek --format --json
orca status --json
```

Expected: no unread actionable completion/error message, no stale Dispatch, runtime ready, and all stabilization/review tasks have explicit terminal outcomes.

- [ ] **Step 2: Recheck Git preservation**

```bash
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/backend' status --short --branch
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/frontend' status --short --branch
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/coordinator' status --short --branch
git -C '/Users/Zhuanz/orca/workspaces/AI视频日记/reviewer' status --short --branch
```

Expected: existing modifications remain; no commit, merge, reset, clean, or main-branch mutation has occurred.

- [ ] **Step 3: Produce the user-facing summary**

Report four sections: completed checks, modified files by worktree, reviewer findings and disposition, and unresolved risks/follow-up. Explicitly distinguish Worker-reported checks from checks rerun by coordinator.

## Execution constraints

- Never run `git reset --hard`, `git clean`, or destructive deletion.
- Never discard or overwrite existing worktree changes.
- Never dispatch lifecycle messages to a group address.
- Never treat terminal preview text as a completion report.
- Never create a commit or merge during this plan.
