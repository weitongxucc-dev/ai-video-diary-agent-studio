# 数据治理与真实素材验收记录（2026-11-12）

本记录只描述本机实际跑过的输入和结果，不把合成 fixture、静态测试或未配置模型当成真实能力。

## 真实素材

- 输入文件：`/Volumes/Dujiangyan-VM/AI视频日记/video-inbox/历史成片_待审核/sample_preview.mp4`
- 文件类型：真实本地 MP4，`ffprobe` 报告时长 1 秒。
- 为保护原文件，验收把该文件复制到临时库后再执行源文件变化测试；没有修改原始素材。

## 已执行链路

1. 绑定临时素材库并保存人工复制确认。
2. 扫描素材，建立 `MaterialAsset`。
3. 执行真实 `ffprobe` 时长分析和镜头切分。
4. 查询 `AnalysisRun`、`EvidenceRef`、`AssetAssertion`、`IndexRecord` 和 `QualityProfile`。
5. 追加源文件字节，确认旧索引变为 `stale`；未重新确认时，分析被明确阻止。
6. 对新修订重新保存复制确认并重分析，索引恢复到新的源修订。
7. 创建并完成一次索引重建任务，状态经历 `queued → running → succeeded`。
8. 解析当前仓库真实文档 `docs/data-governance.md`，保存为知识资料；解析到 8 个显式参数并自动生成 8 个 `candidate` 状态的 `KnowledgeClaim`，重复保存使用确定性 claim id 保持幂等。

## 结果

| 检查 | 结果 |
| --- | --- |
| 真实素材扫描 | 通过，1 个素材 |
| 真实时长/镜头分析 | 通过，`analysis_status=ready`，1 个片段 |
| 证据回查 | 通过，首轮 2 条，重分析后 4 条 |
| 源文件修订变化 | 通过，旧索引标记为 `stale` |
| 重新确认约束 | 通过，未重新确认时拒绝分析 |
| 重分析 | 通过，状态恢复 `ready` |
| 索引重建状态机 | 通过，`queued/running/succeeded`，1 次尝试 |
| QualityProfile | 通过，八项指标均明确为 `unavailable`，没有伪造分数 |
| 真实知识文档解析 | 通过，16 个章节、8 个显式参数、8 个候选断言 |

当前真实素材没有接入可验证的清晰度、曝光、稳定性、构图、安全裁切、音频、OCR 可读性或动作完整度测量器，因此质量档案保存的是 `unavailable` 和原因；这表示治理对象已建立，不表示真实质量模型已经验收。

## 测试基线

- 全量 API 测试命令：`uv run pytest -q --tb=short`。
- 版本契约更新后重新跑过本机全量 API 测试：`5244 passed, 307 failed, 4 skipped, 23 errors`。这是真实的 1.5.2 基线，仍有其他域的既有失败，不能按全绿解读。
- 全量失败主要落在既有创作执行授权、媒体/配音 fixture、工作区配置、桌面脚本和外部服务边界；本轮只修复了已确认的 Pi 工具目录与助手路由测试契约，没有扩大到这些独立域。
- Pi 定向回归命令：`uv run pytest tests/test_assistant_pi.py tests/test_assistant_pi_failures.py tests/test_assistant_runtime_bundle.py tests/test_assistant_phase_deadlines.py tests/test_assistant_conversations.py -q --tb=short`，结果为 `154 passed`。
- 数据治理专项命令：`uv run pytest tests/test_material_*.py tests/test_knowledge_*.py -q --tb=short`，结果为 `512 passed`。
- 本轮修复了两个测试契约：脚本/编辑阶段按当前共享任务能力目录保留 `generate_creation_task_topics`；助手路由测试补齐制作许可身份隔离。两者均已在上述 Pi 回归中通过。
- Pi 运行时版本契约已更新为 1.5.2，并通过 API 符号烟测和 JS 运行桥边界测试；本轮数据治理专项没有剩余失败。
- 当前本机实际安装：Pi `1.5.2`、Node.js `v24.16.0`；运行时不再把 `1.4.0` 当作兼容版本。
- 核心治理与真实知识解析回归（`test_material_analysis.py`、`test_knowledge_file_parsing.py`、`test_knowledge_search.py`）为 `29 passed`。
- `uv run python -m compileall -q app` 通过。针对本轮修改文件的 `git diff --check` 通过；全仓库检查仍报告既有 dirty 文件中的尾随空白，未修改这些用户已有文件。

## 2026-11-13 数据闭环增量验收

在干净的 `main` worktree 上补齐了“候选证据 → Agent 采用 → 任务/时间线引用 → 输出观察”闭环：

- 候选 `MaterialSegmentMatch` 现在回带 `evidence_ref_ids`，Agent 工具和方案快照使用同一组 ID。
- 规划窗口采用保存 `material_adoption`，采用事务重新检查素材权限、用途资格和证据有效性。
- 真实渲染使用记录写入 `task_id` 与 `timeline_version_id`；旧行没有这两个字段时明确保留兼容并标记为 `current` 或 `unknown`。
- 输出观察结果增加 `material_references`，逐个绑定时间线 clip、素材/片段、源时间范围、时间线范围、匹配理由和证据 ID。

验证结果：`uv run pytest tests/test_material_*.py tests/test_knowledge_*.py -q --tb=short` 为 `517 passed`；闭环定向回归为 `12 passed`；`python3 -m compileall` 和 `git diff --check` 通过。参考视频分析与 `compare` 仍按顺序留待下一批，当前没有把静态 compare 模板或 OpenFilm 资源当成该能力已完成。

## 2026-11-13 参考视频结构能力增量

本批在数据治理闭环之后开始参考视频阶段：

- study 复用当前源修订的 `MaterialAsset` 分析片段，每个 shot 回带 `EvidenceRef` ID，并持久化分析运行 ID、源修订和源摘要。
- 源文件发生修订后，读取 study 会将其标为 `stale`；没有当前源修订的分析运行时，不能创建新的 study。
- compare 重新核验参考素材的 `ContentUseState` 和有效证据，绑定任务当前时间线版本，保存时长、镜头数和平均镜头时长差异；视觉和音频观察仍明确为 `unavailable`。

定向验证：`uv run pytest tests/test_reference_video.py -q --tb=short`，结果为 `3 passed`；`uv run python -m compileall -q app` 通过。当前仍未完成真实输出的有界帧/音频观察和逐镜头复刻验收。
