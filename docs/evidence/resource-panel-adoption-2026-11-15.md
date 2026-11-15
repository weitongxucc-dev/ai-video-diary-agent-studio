# Skill / 资源面板真实采用验收（2026-11-15）

## 桌面页面证据

在本地桌面浏览器 `http://127.0.0.1:3000/tasks/task_45c4865fc904` 中打开“素材库”辅助区，切换到“音效”，读取了真实目录资源 `openfilm:sound-glass-pulse`（玻璃脉冲）。页面显示：

- 资源类别：音效，可用
- 资源版本：v1
- 来源修订：`19b26b60c1286b412c01ad64971263ae0e2aef13`
- 预览：真实 `/api/editing-resources/openfilm:sound-glass-pulse/preview` 音频
- 运行要求：`EditAudioTimeline`
- 采用入口：`preview_typed_edit`、`apply_typed_edit`

截图：[`resource-panel-glass-pulse-2026-11-15.png`](../../output/playwright/resource-panel-glass-pulse-2026-11-15.png)

资源目录仍是只读事实源；页面没有伪造“已经写入时间线”。实际采用、时间线持久化、真实 ffmpeg 输出和三帧观察见 [`resource-adoption-glass-pulse-2026-11-15.json`](resource-adoption-glass-pulse-2026-11-15.json)。

## 实际采用证据

运行：

```text
uv run --project apps/api python scripts/validate_resource_adoption.py --output-dir output/acceptance
```

结果：

- `adopted → task_input → timeline → render → output_observation` 五阶段持久化
- 真实 `TimelineVersion` 回读到 `openfilm:sound-glass-pulse`
- 输出 MP4 SHA-256：`a08d68777f67e607771c048c3bd232e098b7ba976164f9761ac02afb0b78dda1`
- 320×240、2 秒、H.264/AAC，3 帧有界观察通过

真实 provider Agent 回合已完成：`/api/assistant/plan/turn` 返回 `completed`，对应 Trace 回读到 `usage_status=known`、`input=7118`、`output=75`、`totalTokens=7378`，并保存 5 个运行事件。此前 runtime-only 回执被误当业务预览的问题已修复。

本机默认 `MacBook Air扬声器` 的资源音频和成片播放均退出码 0，用户第二次播放后确认“听到了”。这证明本机设备播放路径和人工听到路径通过；未对音色、杂音或音量作质量评级，外接手机或其他物理设备播放仍未完成。
## Provider、播放与未完成项（2026-11-15）

- 同一工作区的 Minimax `MiniMax-M3` 真实 provider 请求已返回 HTTP 200 和 usage：`prompt_tokens=216`、`completion_tokens=64`、`total_tokens=280`，缓存 token 128、推理 token 63。完整回执见 `agent-trace-token-replay-2026-11-15.json` 的 `provider_receipt`。
- Agent 端真实 provider 回合已经完成；修复前把 runtime-only `provider_usage` 错当成编辑/方案预览的历史失败仍保留在 Trace 证据中，用于回归说明。
- 已在本机默认 `MacBook Air扬声器` 播放资源音频和成片音轨，`afplay` 两次退出码均为 0；这证明设备路径到达，仍需实际使用者确认听感。
- 当前未完成：外接/手机等真实设备播放、完整视频生产链路的真实成片验收，以及其他 Skill 资源逐项运行时接通。
- 用户在第二次播放后确认“听到了”；本记录只证明人工听到声音，未扩展为音色、杂音或音量质量结论。
