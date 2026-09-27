# MAT-SCENE 接手状态（2026-09-27）

## 代码与边界

- 当前 checkout：`/Users/Zhuanz/.codex/worktrees/7d8c/original`，分支 `codex/011-material-scene`。
- HEAD `49fa7b2`（父提交 `279f492`，共同基线 `63d3c2c`）。目前 Git 工作树干净；此交接文件位于忽略的 `work/`，没有其他 WIP。
- `279f492` 增加有许可的分窗 FFmpeg 镜头候选、逐窗检查点和重启恢复。`49fa7b2` 为缺少帧率字段的旧 `probe-meta:v1` 检查点增加同源、同许可的 v2 重探测。两提交均仅覆盖 MAT 授权文件，没有改 `main.py`、DDL 或 SCHED 文件。
- 主控线程 `01a0d212-4a77-74f3-af12-e7767cba6e03`；SCHED 线程 `01a0d29d-226b-7460-8ba6-76f14dc8c56a`。不要自动 push 或合并主线；主控负责集成。

## 已验证

- `uv run --directory apps/api --locked pytest tests/test_material_scene.py tests/test_material_probe.py tests/test_material_ingestion.py -q`：44 passed（HEAD 代码与原有 SCHED 基线组合）。
- `uv run --directory apps/api --with ruff ruff check app/material_scene.py app/material_probe.py app/material_ingestion.py app/production.py tests/test_material_scene.py tests/test_material_probe.py --select F401,F821,E701,E702`：通过。
- `git diff --check`：通过。
- 真实 32 秒三色 MPEG4、GOP250 样片：14/15/5 秒三个源时间窗口，切点 5/20 秒；首窗检查点后关闭调度器并重启，只运行剩余两窗。最终状态仍为 `blocked`，人工匹配片段和未匹配纠正保留。模拟超时不会写镜头检查点。
- 旧 v1 缺字段检查点测试：只对同一来源发起 v2 探测许可；v1 历史保留、人工标签保留、重启幂等；字段存在但帧率未知不会无限重探测。

## 当前 HOLD 与下一步

- QA 联合验收 HOLD：`avg_frame_rate == r_frame_rate` 只是标称信息，不能当实际解码帧数硬上限。SCHED 正在补真实 `HostResourceProbe` 的窄范围准入和 `run_granted_command` 的运行中子进程 RSS/线程/主机内存监测。SCHED 的 `2da0594` 是旧准入提交；新监测目前仍在其工作树 WIP，未收到新 SHA。
- SCHED 冻结异常契约：`GrantedCommandResourceLimit(GrantedCommandError)` 表示进程超出校准 RSS/线程上限，MAT 应终态 `scene_resource_limit`，不写检查点；`GrantedCommandResourceUnavailable(GrantedCommandError)` 表示主机压力或测量未知，MAT 应延期 `resource_pressure`，不写检查点。两者仅在进程组释放确认后抛出；不确认则 `RecoveryBlocked`。等待 SCHED 提交后修改 MAT 的 `run_granted_scene`，加针对两个异常的测试。
- SCHED 新封装要求 scene grant 使用真实 `HostResourceProbe` 与显式 `SceneWindowBudget`。MAT 当前 `test_material_scene.py` 的两个真实许可测试使用测试用 `TestResources`，新 SCHED 组合会失败；必须改为显式测试预算。SCHED 已测本机隔离样本：32 秒 160×90 MPEG4，30fps/GOP250 和 120fps/GOP1000，峰值 RSS 约 17.1MB、5 线程；建议测试预算仅用于测试，不能宣称生产校准。当前 MAT 样片为 5fps 160×90 MPEG4/GOP250，符合测试形状。
- 在主控集成 SCHED 新提交与 MAT 两提交后，重跑下列最小联合检查并核对超限后请求状态、零镜头检查点、完整进程组释放。无需新增全片预扫描。未校准输入必须保持 dependency/unknown。
- 终态 v2 探测失败后 `verify_retry` 仍拒绝自动重试；这是后续可信授权消费者/入口缺口，不要把替换源文件描述成已实现的重试。

## 精确命令

```sh
git status --short --branch
uv run --directory apps/api --locked pytest tests/test_material_scene.py tests/test_material_probe.py tests/test_material_ingestion.py tests/test_host_resource_probes.py tests/test_device_scheduler.py tests/test_device_runtime.py -q
uv run --directory apps/api --with ruff ruff check app/material_scene.py app/material_probe.py app/material_ingestion.py app/production.py tests/test_material_scene.py tests/test_material_probe.py --select F401,F821,E701,E702
git diff --check
```

只在 SCHED 新提交可用且测试预算接线完成后把上述联合命令作为有效验收。原 MAT 独立测试可按“已验证”节单独复跑。
