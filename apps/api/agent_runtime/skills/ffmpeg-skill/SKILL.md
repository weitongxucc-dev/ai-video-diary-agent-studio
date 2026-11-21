---
name: ffmpeg-skill
description: 受控登记 kajisho5/ffmpeg-skill 的 FFmpeg/FFprobe 能力范围、依赖和验证边界；不直接运行外部脚本。
---

# FFmpeg skill（曜核能力登记适配）

这是对 `kajisho5/ffmpeg-skill` v3.12.1 的 method-only 能力登记。它保留 probe、抽帧、剪辑、拼接、音频、画幅、字幕和导出等能力的输入输出、依赖、风险与验证顺序，不把外部 Python、FFmpeg 命令、MCP transport 或第二套媒体作业接入曜核。

## 使用边界

先读取当前任务、时间线版本、编辑能力、资源目录和已有输出观察。`catalog_only` 只能说明外部能力和缺口，`planned` 只能说明尚未完成适配；两者都不能当成当前任务可运行入口。只有现有曜核编辑入口明确支持且用户授权时，才能沿 `preview_typed_edit` / `apply_typed_edit` 提案和版本边界执行。

FFmpeg/FFprobe 的本地路径、版本、编码器、滤镜、字幕字体和具体构建许可证必须在运行前检查。命令成功、探测成功或抽帧成功都不能替代真实版本、视觉/音频观察和输出回读。

`manifest.json.adapter_contract` 是本适配的审计入口：`operation_map` 逐项记录宿主入口、运行要求和缺口，`lifecycle` 记录提交、进度、日志、恢复、取消和未知结果的现有事实。它只描述与曜核现有 `JobRun`、`TaskEvent`、`ArtifactRecord`、`TransformRun` 及版本绑定的差距，不提供第二套媒体执行器；缺少外部回执、检查点或清理证据时保持 `catalog_only`、`planned` 或 `failed`。

## 受控验证顺序

1. `probe` 读取输入媒体和时长、画幅、帧率、流及编码信息；输入源保持只读。
2. 需要画面证据时使用 `look` 对应的抽帧/接触表能力，记录帧时间和观察来源。
3. 对剪辑、拼接、音频、转画幅或字幕形成带区间和依赖的提案；先检查 `catalog_only`、`planned`、`failed` 状态。
4. 通过现有业务预览和采用入口写入 `EditProposal/TimelineVersion`，不把外部命令或输出路径当成已采用结果。
5. 再次探测、检查、观察输出并回读原请求；失败、取消、超时和未知结果保持原状态。

## 不可用行为

不得直接运行外部 ffmpeg-skill Python、FFmpeg/FFprobe 命令、MCP server、Shell 或网络下载；不得读取任意路径、覆盖源文件、绕过版本校验、把未经许可的字幕字体/编码器当成可分发依赖，或把目录记录当成真实出片。
