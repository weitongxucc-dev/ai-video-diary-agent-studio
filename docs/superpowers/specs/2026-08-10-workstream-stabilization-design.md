# AI 视频日记多线程稳定化与评审设计

**日期：** 2026-08-10  
**状态：** 用户已确认  
**范围：** 验证收口，不提交、不合并、不触碰 `main`

## 1. 背景

项目当前由四个 Orca 工作线程协作：

- `coordinator`：任务编排、状态管理、异常处理和最终汇总；当前同时承载 `apps/desktop/`。
- `backend`：负责 `apps/api/` 的接口、业务逻辑、视频管线、运行健康、服务打包和后端测试。
- `frontend`：负责 `apps/web/` 的页面、组件、交互状态、API 对接和前端质量检查。
- `reviewer`：负责独立审查和复验，不默认承担开发修改。

现有 Orca Run `run_29e6d5c997c9` 中，三个任务已正式完成。Desktop IPC 任务 `task_a0d927ae206b` 的终端记录显示代码已经修改并验证，但原 Dispatch `ctx_fde095a4755b` 的 Worker 生命周期已失效，未产生正式 `worker_done`，任务仍停留在 `dispatched`。

四个工作区均存在未提交改动，因此本轮目标是先建立可信的验证结果和审查结论，而不是提交或合并。

## 2. 目标与非目标

### 目标

1. 并行稳定 backend、frontend 和 desktop 三个开发面。
2. 为每个开发面取得正式、可追踪的完成报告。
3. 由 reviewer 在开发面完成后进行独立集中评审。
4. 将评审发现交回原责任线程修复并复验。
5. 清理失联或悬挂的 Orca Dispatch，确保编排状态与实际工作一致。
6. 输出测试结果、评审结论、修改文件和遗留风险的最终汇总。

### 非目标

- 不创建 Git commit。
- 不合并任何分支。
- 不修改 `main`。
- 不做与当前稳定化无关的重构或功能扩展。
- reviewer 不直接跨工作区修改业务代码。

## 3. 执行架构

采用两阶段工作流：

```text
第一阶段：backend ─┐
          frontend ─┼─ 并行稳定化
          desktop  ─┘
                        ↓
第二阶段：          reviewer 集中评审
                        ↓
                    定向返修（如有）
                        ↓
                    reviewer 复验
                        ↓
                    coordinator 汇总
```

第一阶段三个任务相互独立，可以并行执行。Reviewer 任务依赖三个任务全部完成，避免评审未稳定的中间状态。

## 4. 第一阶段任务合同

### 4.1 Backend 稳定化

**工作区：** `backend`  
**允许范围：** `apps/api/` 及其直接相关测试、依赖和打包配置

职责：

1. 阅读当前所有已修改和未跟踪的后端文件，确认改动意图与一致性。
2. 检查 API、视频管线、运行健康、配置/schema、服务启动和 PyInstaller 打包路径。
3. 修复发现的正确性、测试或静态检查问题。
4. 执行仓库已有的完整后端测试和 lint 命令，不自行发明替代检查。
5. 完成报告必须列出：修改文件、执行命令、通过/失败数量、未解决问题。

禁止：

- 不编辑 frontend、desktop 或 reviewer 工作区。
- 不提交、不合并。
- 不进行无关重构。

### 4.2 Frontend 稳定化

**工作区：** `frontend`  
**允许范围：** `apps/web/` 及其直接相关前端配置

职责：

1. 阅读当前页面、组件、API 客户端、类型和新增错误/加载状态的全部改动。
2. 验证主要页面流程、导航、表单错误、法律页面和 API 对接的一致性。
3. 修复发现的构建、类型、lint、交互和可访问性问题。
4. 执行仓库已有的 build、lint、contract、pixel-style 及其他适用检查。
5. 完成报告必须列出：修改文件、执行命令、结果、路由或 bundle 异常、未解决问题。

禁止：

- 不编辑 backend、desktop 或 reviewer 工作区。
- 不提交、不合并。
- 不扩展新的产品功能。

### 4.3 Desktop 恢复与稳定化

**工作区：** `coordinator`  
**执行方式：** 新建独立 Agent 终端，共享当前 worktree；不创建新 worktree

职责：

1. 将旧 Desktop IPC Dispatch 作为失联恢复事件处理，不把终端历史文字直接当作正式完成证明。
2. 重新检查 `apps/desktop/` 的五个 IPC handlers、参数校验、路径打开行为、退出行为和 API 端口获取。
3. 检查 API 启动失败时不创建窗口，以及 Electron 应用生命周期的其他关键分支。
4. 检查 preload 与 main 的通道契约一致性。
5. 执行项目已有的 Desktop/Electron 检查；若缺少自动化测试，至少执行语法、静态检查和可重复的定向验证，并明确测试缺口。
6. 只修改 `apps/desktop/`，不修改 coordinator 的编排或根目录职责文件。
7. 完成报告必须列出：修改文件、执行命令、验证覆盖、测试缺口和未解决问题。

禁止：

- 不把 reviewer 继续当作 Desktop 主开发者。
- 不提交、不合并。
- 不修改 backend 或 frontend 工作区。

## 5. 第二阶段 Reviewer 合同

**依赖：** Backend、Frontend、Desktop 三个稳定化任务均正式完成

Reviewer 首轮只读审查三个工作区，重点检查：

- 正确性和回归风险；
- API/类型/IPC 契约一致性；
- 错误处理和失败路径；
- 输入、安全和本地路径处理；
- 测试覆盖与验证可信度；
- 明显的维护性问题，但不扩展为无关重构。

每个发现必须包含：

1. 严重程度；
2. 工作区与文件位置；
3. 可复现的输入或状态；
4. 预期行为与实际错误；
5. 建议的最小修复方向。

Reviewer 不直接修改业务文件。若存在阻断发现，coordinator 为对应责任线程创建定向返修任务。返修完成后，reviewer 只复验相关发现；循环直到：

- 所有阻断发现已修复；或
- 存在无法在当前范围内解决的明确 blocker，并记录原因与影响。

## 6. Coordinator 状态机

Coordinator 按以下状态推进：

1. 将旧 Desktop 任务 `task_a0d927ae206b` 以恢复性失败收口：结果中明确记录原 Dispatch 失联、缺少权威 `worker_done`，不得把它手工标记为完成；随后创建新的 Desktop 恢复与稳定化任务。
2. 创建三个独立稳定化任务。
3. 启动 backend、frontend 和 desktop Worker。
4. 等待并处理 `question`、`escalation` 和 `worker_done`。
5. 每个 Worker 完成后，按 Orca 生命周期要求释放或明确复用终端。
6. 三个任务完成后启动 reviewer。
7. 根据 reviewer 结果创建定向返修任务并监督复验。
8. 确认没有未处理 Delivery、失联 Dispatch 或阻断发现。
9. 输出最终验证汇总，保留所有未提交改动。

## 7. 异常处理

- Worker 提问：由 coordinator 通过原消息线程答复，不重复创建问题。
- 检查超时：视为检查点，不视为失败；确认 Worker 状态后继续等待。
- Worker 终端失联：先检查 Dispatch 和替代终端；无法恢复时记录旧尝试失败并创建明确的恢复任务。
- 测试失败：Worker 必须先尝试在所属范围内修复；仍失败则以失败结果报告具体命令和错误。
- 跨工作区依赖问题：由 coordinator 协调，不允许 Worker 直接跨边界修改。
- Reviewer 发现：交回原责任线程，reviewer 仅复验。

## 8. 完成标准

本轮仅在以下条件全部满足时结束：

1. backend、frontend、desktop 都有正式 `worker_done`。
2. 每个线程列出实际执行的检查及结果。
3. reviewer 已完成独立审查，且无未处理的阻断发现。
4. 所有返修发现已复验，或明确登记为当前范围 blocker。
5. Orca Run 中没有残留的失联或悬挂 Dispatch。
6. coordinator 输出统一的文件、测试、风险和下一步报告。
7. Git 工作区保留未提交状态，没有 commit、merge 或 `main` 变更。
