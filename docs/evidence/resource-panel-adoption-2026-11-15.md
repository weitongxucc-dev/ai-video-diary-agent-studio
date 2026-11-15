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

真实桌面人工听感、真实 provider 回合和设备播放仍未宣称通过。
