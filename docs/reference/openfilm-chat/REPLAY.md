# OpenFilm 回放验收入口

在 WCE-4 worktree 中运行：

```bash
cd apps/api
uv run python scripts/replay_openfilm_acceptance.py
```

入口读取现有 `docs/reference/openfilm-chat/replay-fixture.json`，使用当前 Pi 事件投影和 `AssistantStreamEvent` 校验，再写入临时 SQLite 轮次并回读 `public_history_turn`。输出包含事件顺序、C0–C8 时间点事实、持久化回读、停止轮次和 `assistant_input` 生命周期。临时数据库在进程结束后删除。

输出中的 `mode: replay_only` 和 `provider_contacted: false` 表示这是回放证据。它验证事件顺序、工具并行计时、停止状态、追加输入回执、持久化回读和脱敏边界，不代表外部模型回合成功。

真实 Pi SDK 生命周期、停止信号和压缩回放使用现有测试：

```bash
node apps/api/agent_runtime/tests/phase-deadlines.test.mjs
node apps/api/tests/test_assistant_sdk.mjs
cd apps/api && uv run pytest -q tests/test_assistant_activity_replay.py tests/test_assistant_inputs.py tests/test_assistant_trace.py
```

有效 provider、真实桌面入口和媒体产物仍需单独验收。
