# MAT-SCENE 接手状态（2026-09-27）

## 代码与边界

- 当前 checkout：`/Users/Zhuanz/.codex/worktrees/7d8c/original`，联合检查分支 `codex/011-mat-scene-joint-check`。
- HEAD `d4519ed`。`git status --short` 仅有未跟踪的 `work/takeover-status.md`；代码没有未提交改动。不要丢弃此交接文件。
- MAT 原始提交 `279f492`（场景分窗）与 `49fa7b2`（旧 v1 缺帧率字段的 v2 重探测）基于 `63d3c2c`，可由主控独立挑入。联合检查分支把 SCHED 已提交的 `dadc618`、`2da0594`、`7c9414e` 依序挑入，等价 SHA 是 `86259be`、`6809bc1`、`a055d1b`；HEAD `d4519ed` 只修改 MAT 文件，可在主控先集成 SCHED 后独立挑入。没有改 `main.py` 或 DDL，没有 push/main mutation。
- 主控线程 `01a0d212-4a77-74f3-af12-e7767cba6e03`；SCHED 线程 `01a0d29d-226b-7460-8ba6-76f14dc8c56a`。不要自动 push 或合并主线；主控负责集成。

## 已验证

- 联合检查命令（见下）在 `d4519ed` 上：89 passed。
- `uv run --directory apps/api --with ruff ruff check app/material_scene.py app/material_probe.py app/material_ingestion.py app/production.py tests/test_material_scene.py tests/test_material_probe.py --select F401,F821,E701,E702`：通过。
- `git diff --check`：通过。
- 真实 32 秒三色 MPEG4、GOP250 样片在真实 `HostResourceProbe`、显式测试预算下运行：14/15/5 秒三个源时间窗口，切点 5/20 秒；首窗检查点后关闭调度器并重启，只运行剩余两窗。最终状态仍为 `blocked`，人工匹配片段和未匹配纠正保留。模拟超时、进程资源超限与测量不可用均不写镜头检查点；SCHED 的真实子进程测试单独验证进程组释放。
- 旧 v1 缺字段检查点测试：只对同一来源发起 v2 探测许可；v1 历史保留、人工标签保留、重启幂等；字段存在但帧率未知不会无限重探测。

## 当前边界与下一步

- `avg_frame_rate == r_frame_rate` 只是标称信息，不能当实际解码帧数硬上限。SCHED `7c9414e` 已加受许可运行中的子进程 RSS/线程及主机内存监测；超限 `GrantedCommandResourceLimit` 被 MAT `d4519ed` 终态记录为 `scene_resource_limit`，测量不可用 `GrantedCommandResourceUnavailable` 延期为 `resource_pressure`，两者都无成功检查点。进程组无法确认释放时仍由 `RecoveryBlocked` 保持许可。
- SCHED 的 Darwin 隔离样本：32 秒 160×90 MPEG4，30fps/GOP250 及 120fps/GOP1000，峰值 RSS 约 17.1MB、5 线程。MAT 测试显式采用 MPEG4 160×90、源文件上限 3MB、进程 RSS 64MiB/8线程、保留内存 128MiB 的**测试配置**。这不是生产校准或最坏峰值保证，生产 HostBudget 未接入 scene profile；未校准输入必须保持 dependency/unknown。
- 主控收到本地联合候选后应由 QA 审核准确 SHA，再决定是否挑入主线；重跑下列命令和检视真实许可、失败状态、无检查点及释放证据。无需全片预扫描。
- 终态 v2 探测失败后 `verify_retry` 仍拒绝自动重试；这是后续可信授权消费者/入口缺口，不要把替换源文件描述成已实现的重试。

## 精确命令

```sh
git status --short --branch
uv run --directory apps/api --locked pytest tests/test_material_scene.py tests/test_material_probe.py tests/test_material_ingestion.py tests/test_host_resource_probes.py tests/test_device_scheduler.py tests/test_device_runtime.py -q
uv run --directory apps/api --with ruff ruff check app/material_scene.py app/material_probe.py app/material_ingestion.py app/production.py tests/test_material_scene.py tests/test_material_probe.py --select F401,F821,E701,E702
git diff --check
```

上述命令已在 `d4519ed` 的联合检查分支通过。生产 scene profile 仍未配置，因此这不能作为实际用户素材可用或产品验收完成的证据。
