# SCHED01 接手状态（2026-09-27）

## 当前候选与归属

- 工作树：`/Users/Zhuanz/.codex/worktrees/device-scheduling/original`
- 分支：`codex/011-sched-host`
- 当前候选：`7c9414e`，父提交 `2da0594`；最初从集成基线 `13b253f` 起步。
- 顺序提交：`c5ba544`（host探针/许可子进程）、`45f2f24`（旧探针请求替换）、`fb8ef24`（取消边界）、`dadc618`（CPU render预算）、`2da0594`（scene预算）、`7c9414e`（scene运行中监测）。集成时按主控已审核的实际依赖与SHA选择，不盲目重放。
- SCHED独占范围：`apps/api/app/device_scheduler.py`、`device_runtime.py`、`host_resource_probes.py`、`granted_subprocess.py`、相邻专项测试；API pyproject/uv.lock仅批准的psutil依赖。活动合同以集成仓库 `.project-control/active-contract.md` 为准。
- 本记录写入前分支干净；本文件是唯一新增未跟踪文件。没有push、merge、rebase或触碰集成仓库未跟踪文件。

## 已验证的包结果

- 许可子进程在exec前登记ACK，超时、取消、输出超限后终止并确认进程组释放；macOS/Linux支持，Windows拒绝。
- `creation_render` 只在明确 `cpu_ffmpeg` 全字段与显式 `CpuRenderBudget` 下按真实CPU/内存/swap/磁盘/IO样本准入；MAKE当前bare kind仍返回unknown。总阶段deadline、线程、输出及中间产物上限须由MAKE媒体叶子实际执行，不能因此开启scheduled。
- `material_scene_window` 只在显式 `SceneWindowBudget`、支持的codec/源尺寸/源字节/标称帧率档位、窗口/输出/线程/超时上限及真实主机余量下准入。标称帧率**不是**实际解码帧数上界；长GOP成本由12秒墙钟限时和进程组清理限制。
- `7c9414e` 增加scene子进程树的实际RSS与OS线程数采样（预算间隔上限250ms），以及主机剩余内存/swap复核；stdout/stderr关闭后继续监测。超限或采样未知即停止，释放无法确认则`RecoveryBlocked`。`GrantedCommandResourceLimit`用于RSS/线程超限；`GrantedCommandResourceUnavailable`用于主机压力/测量未知，两者均继承`GrantedCommandError`。这是采样压力控制，**不是硬峰值内存保证**。
- 测试：`43 passed`。真实负向例在关掉两路输出后让进程分配80MB，24MiB RSS cap触发终止，并经调度fail/defer与许可释放；另有psutil测量失败路径。真实隔离FFmpeg短渲染、ffprobe、孙进程清理也通过。
- 本机Darwin一次性小范围测量：MPEG4 160×90、32秒、GOP250/30fps、源1,131,306B，scene窗口14–29秒耗时0.086秒，10ms采样峰值RSS17,072,128B、5线程；GOP1000/120fps、源2,784,725B耗时0.129秒，采样峰值RSS17,088,512B、5线程。测试配置仅允许mpeg4≤160×90、源≤3MB，RSS cap64MiB、OS线程cap8、预留内存128MiB。样本小且采样可能漏瞬时峰值；该配置不是生产硬件校准，也未接生产factory。

## 复验命令

在上述工作树运行：

```bash
uv run --directory apps/api --locked pytest tests/test_device_scheduler.py tests/test_device_runtime.py tests/test_host_resource_probes.py -q
git diff --check 13b253f..7c9414e
git status --short --branch
```

定向负向测试：

```bash
uv run --directory apps/api --locked pytest tests/test_host_resource_probes.py -q -k scene_process_monitor
```

## 待办与阻断

1. QA独立报告见 `/Users/Zhuanz/.codex/worktrees/26aa/original/work/qa-011/sched-scene-7c9414e-review.md`：`7c9414e`进程监测叶子PASS，43项通过；MAT `49fa7b2`+SCHED原联合选集64通过/1失败。唯一失败是MAT恢复测试仍注入always-true TestResources，新场景执行前门禁正确拒绝。QA-only副本换真实HostResourceProbe+160×90 MPEG4测试profile后该32秒FFmpeg恢复例通过。**联合消费者和产品准入仍HOLD**；测试profile不是生产校准。
2. MAT需使用真实`HostResourceProbe`预算适配scene grant测试，并按上述两种资源异常分别失败/让出；QA发现当前`GrantedCommandResourceUnavailable`被泛捕后误写terminal `scene_failed`，需单独映射为资源等待并复验。无预算或未测codec/尺寸继续dependency，不写场景成功检查点。
3. MAKE仍需落实render全字段、总阶段deadline及所有嵌套媒体叶子参数，之后才可考虑配置CPU render预算。没有真实两目标硬件压力与五会话验收，不宣称共享设备产品完成。
4. 主控负责审查、按已审核具体SHA集成及必要复验；不自动推送main或开启scheduled。所有权移交前不得让另一执行者覆盖本分支。
