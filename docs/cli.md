# 曜核 CLI

`scripts/yaohe.mjs` 是普通业务 API 的独立消费者。使用 Node.js 22 或更高版本及原生 `fetch`，没有额外依赖。当前从源码仓库执行；桌面安装包尚未随包交付 CLI，也没有安装全局 `yaohe` 命令。运行前须已有可用的业务 API 和合法成员或会话身份。

业务动作、权限和版本规则引用[架构 §3.8](system-architecture.md#38-editcompiler)、[认证边界](auth-workspace-boundary.md)及[领域模型](domain-model.md)。本页说明实际命令协议；能力与结果由服务端当前回执决定。

## 私有配置

默认读取 `~/.config/yaohe/config.json`，也可使用 `--config FILE`。通过私有文件编辑器填写配置，不把真实身份放入命令参数、终端输出或日志：

```json
{
  "api_base": "http://127.0.0.1:8000",
  "local_member_token": "<已有本地成员令牌>"
}
```

配置仅接受 `api_base`、`local_member_token`、`authorization`、`cookie`。后两项可填写已有会话的完整请求头值；CLI 不登录、不创建身份，也不推断工作区。`api_base` 须为无路径、查询、密码的 HTTP(S) origin。

POSIX 上文件必须由当前用户拥有、权限恰为 `0600`，且为普通文件而非符号链接。缺配置或权限错误会在请求前拒绝。Windows 文件保护尚未验证。所有 HTTP 重定向均拒绝，身份不会被转发至重定向目标。响应中出现的已加载身份值会被遮挡。

```bash
node scripts/yaohe.mjs --help
node scripts/yaohe.mjs --config /private/path/config.json task inspect TASK
```

## 能力、对象与素材读取

```bash
node scripts/yaohe.mjs capabilities list
node scripts/yaohe.mjs capabilities list --task TASK
node scripts/yaohe.mjs capabilities describe ReplaceClip --task TASK
node scripts/yaohe.mjs task inspect TASK
node scripts/yaohe.mjs material inspect ASSET
node scripts/yaohe.mjs material search --query "城市街景" --role b_roll --limit 8
```

全局能力读取 `/api/assistant/capabilities`；任务能力读取 `/api/creation-tasks/TASK/assistant/edit-capabilities`。`describe` 返回指定动作的实际可用性、参数 schema 和单位。素材读取沿 `/api/production/materials/ASSET` 和 `/api/production/materials/search`，仍受原成员与用途规则约束。

脚本执行、扩展注册和导出目前没有接入完整 API，本 CLI 没有这些命令。服务端能力目录中的其他入口也不代表本 CLI 已实现对应命令。

## 编辑预览与原提案应用

先从能力 schema 查到动作字段，再创建 `edit.json`。以下对象 ID 都应替换为实际当前任务中的引用：

```json
{
  "operation_type": "ReplaceClip",
  "clip_id": "clip_actual",
  "asset_id": "asset_actual",
  "segment_id": "segment_actual",
  "source_start_seconds": 0
}
```

```bash
node scripts/yaohe.mjs edit preview TASK --file edit.json --request-id UUID
node scripts/yaohe.mjs request query TASK UUID --phase preview
node scripts/yaohe.mjs edit apply TASK PROPOSAL --request-id APPLY_UUID
node scripts/yaohe.mjs request query TASK APPLY_UUID --phase apply
```

预览文件可同时指定 `expected_timeline_version_id` 和 `expected_timeline_sequence`；缺少二者时只读取一次任务，将当时的版本绑定到 POST。CLI 不生成助手轮次，不接受文件中的成员、工作区或助手身份。服务端仍校验动作字段、对象、来源和版本，过期输入会返回真实拒绝。

应用先 GET 原提案的 `/edits/PROPOSAL/result`，从该回执读取 `base_version_id` 和 `input_digest`，再 POST `/edits/PROPOSAL/confirm`。它不会用任务的新版本替换原提案基础，也不会重新编译提案。

每个新的预览或应用都使用调用方提供的 UUID。提交前先查询同一身份：未知时只保留查询定位；已经记录时，当前明确动作仍须经过共同 API 的幂等比较，差异由服务端拒绝。回执的 `submitted_input` 提供原提交的版本与摘要绑定；CLI 只复用这些绑定，当前动作参数或提案仍来自本次命令，不复制助手身份。没有可核验的原绑定时返回未知，不改绑当前任务版本或假冒原请求成功。提交连接丢失或服务端错误时最多回查一次，不自动重 POST。不要换 UUID 来盲重试未知结果。CLI 不持久化另一份成功状态。

## 作业读取、有限等待和取消

```bash
node scripts/yaohe.mjs job inspect TASK JOB
node scripts/yaohe.mjs job wait TASK JOB --timeout-seconds 30
node scripts/yaohe.mjs job cancel TASK JOB
```

沿任务的 `/media-jobs/JOB` 读取；取消沿 `/media-jobs/JOB/cancel`。等待默认 30 秒，可设置正数且至多 300 秒，只轮询 GET，不调用模型、提交后续动作或改变设备排队。期限到达仍排队或运行时返回待完成状态；成功状态缺少结果时返回未知。取消回执为 `cancelled` 时表示取消命令完成；它不表示媒体处理成功。

## 撤销与重做

```bash
node scripts/yaohe.mjs timeline undo TASK
node scripts/yaohe.mjs timeline redo TASK --expected-version VERSION --expected-sequence 3
```

缺少期望版本时读取一次当前任务并绑定；提供时须同时给出版本与非负整数序号。沿原 `/timeline/undo` 或 `/timeline/redo` POST。当前接口没有请求身份字段，CLI 不伪造；响应未知时返回任务查询定位，不自动重发。

## 输出与退出码

业务命令向 stdout 输出一个 JSON 对象，包含 `state`、`code`、原 API `data` 或错误，以及可用的 `request_locator`、`job_locator`、`task_locator`。`--help` 输出文字。`submitted` 只描述是否发起提交，不表示工作完成；`replayed` 表示读回原请求，`recovered` 表示提交后通过回读恢复回执。

| 退出码 | 含义 |
| --- | --- |
| 0 | 读取完成，或 API 返回已完成的实际结果；成功取消也是 0 |
| 1 | API 拒绝或业务终态失败；原 HTTP 状态和数据保留 |
| 2 | 参数、配置或私有文件权限错误 |
| 3 | 已排队、运行中或有限等待结束仍未完成 |
| 4 | 提交／读取结果未知、无可核验回执或重定向被拒绝 |

本包检查使用自造 HTTP 监听器、假身份文件和模拟回执，验证客户端传输与绑定。真实业务 API、外部 Agent 操作、可见预览、采用后的版本与产物仍须在同一候选上验收；不能用本包测试数或帮助页面代替它们。

```bash
node --test scripts/yaohe.test.mjs
```
