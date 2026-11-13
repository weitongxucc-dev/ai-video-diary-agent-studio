# 资源采用、渲染与输出观察验收

执行时间：2026-11-13（Asia/Singapore）
入口：`scripts/validate_resource_adoption.py`

## 结果

本轮使用仓库内真实 OpenFilm `Glass Pulse` 音效文件完成了一次本地媒体闭环：

1. 从 Asset Library manifest 读取 `openfilm:sound-glass-pulse`，确认状态为 `connected`，并通过预览路由解析到真实 `.m4a` 文件。
2. 将真实 `sound.m4a` 与真实生成的 320×240、2 秒 MP4 混音，生成可播放成片。
3. 在 SQLite 治理库写入同一资源版本的 `adopted → task_input → timeline → render → output_observation` 记录。
4. 使用生产输出观察函数对成片采样 0.25、1.0、1.75 秒三帧，回读视频尺寸、时长、编码和音频流，并把最终 usage outcome 写为 `succeeded`。

实际结果：

- 成片：`output/acceptance/resource-adoption-glass-pulse.mp4`
- 成片 SHA-256：`a08d68777f67e607771c048c3bd232e098b7ba976164f9761ac02afb0b78dda1`
- 视频：H.264，320×240，2.000 秒，24 fps
- 音频：AAC，一条音频流
- 输出观察：3 帧通过有界采样
- 治理链：5 个阶段均已持久化；最终输出观察 outcome 为 `succeeded`
- 完整 JSON：`output/acceptance/resource-adoption-glass-pulse-2026-11-13.json`

`openfilm:transition-glitch` 仍保持 `catalog_only`，因为它的 CSS 转场 adapter 还没有写入 `EditProposal/TimelineVersion` 的执行入口，本轮没有把它伪装成可采用资源。

## 代码与测试

为使音效能够进入治理闭环，资源治理模型新增 `audio`、`transition`、`skill` 类型，并加入生产数据库迁移 28，保留已有 manifest、许可证和 usage 记录。

验证命令及结果：

```text
uv run --project apps/api python -m pytest -q apps/api/tests/test_resource_governance.py
14 passed

uv run --project apps/api python -m pytest -q apps/api/tests/test_production_foundation.py apps/api/tests/test_device_scheduler.py apps/api/tests/test_scheduled_tasks.py
163 passed

uv run --project apps/api python -m pytest -q apps/api/tests/test_assistant_runtime_bundle.py apps/api/tests/test_assistant_pi.py apps/api/tests/test_assistant_pi_failures.py
121 passed

uv run --project apps/api python scripts/validate_resource_adoption.py
真实渲染、三帧输出观察和治理回读通过
```

## 尚未覆盖

本轮证明的是本地真实资源、真实 ffmpeg 成片和治理持久化闭环；真实桌面窗口播放、真实 provider Agent 回合、真实设备播放和人工听感仍需单独验收。
