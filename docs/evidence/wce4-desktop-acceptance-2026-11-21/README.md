# WCE-4 独立桌面验收（2026-11-21）

## 入口

```bash
cd /Volumes/Dujiangyan-VM/AI视频日记/repositories/wce-4-agent-event-stream
cd apps/api
uv run python scripts/replay_openfilm_acceptance.py

apps/desktop/dist/mac-arm64/'Yaohe Studio.app'/Contents/MacOS/'Yaohe Studio' \
  --user-data-dir=/Volumes/Dujiangyan-VM/AI视频日记/repositories/wce-4-agent-event-stream/docs/evidence/wce4-desktop-acceptance-2026-11-21/user-data-3 \
  --local-web-port=3236
```

验收启动使用 arm64 未签名本地候选包。数据库通过 user-data-3/.env 绑定到同目录 agent.sqlite3，未读写默认用户数据库。候选启动后 API 和 Web 分别就绪，生命周期日志记录了同一 buildIdentity 对应的构建身份。

## 结果

- 回放读取现有 `docs/reference/openfilm-chat/replay-fixture.json`，投影 33 个事件，保留 C0–C8 与 C6b 检查点。
- 工具顺序、并行活动计时、脱敏投影和临时 SQLite 持久化回读通过。
- 停止轮次保存为 `stopped`，停止前工具事件仍保留。
- 追加输入回执从 `accepted` 经过 `consumed` 到 `completed`。
- 独立桌面候选启动并完成 API/Web readiness。空任务首页、创建草稿、输入目标和保存草稿均在 GUI 中实际观察。
- `agent.sqlite3` 中没有创建制作任务，保存结果只产生一份隔离草稿，避免生成假成片或重复生产任务。

## 图像证据

- `desktop-candidate-home-real.png`：独立 user-data 的空任务首页，显示 0 个视频任务和真实空素材状态。
- `desktop-new-task.png`：独立候选的新视频草稿入口。
- `desktop-goal-typed.png`：目标输入已送入表单，内容为 30 秒动态文字短片请求。
- `desktop-draft-saved.png`：草稿保存后显示“草稿已保存”，证明输入持久化到隔离数据库。

## 未验证

真实模型 provider 回合、Pi SDK 事件流和正式制作媒体输出未在本次运行中触发。provider 仍按 WCE-4 回放报告标记为 `provider_contacted: false`，这些项目需要后续真实凭据和单独的业务输出验收。
