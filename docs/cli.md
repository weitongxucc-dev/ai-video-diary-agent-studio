# 曜核 CLI

本文记录当前源码命令及协议。产品默认直接编辑策略见[产品规则](product-requirements.md#23-自然语言与精确编辑共享同一事实源)；下面显式的 preview/apply 是可用接口，不表示每条用户指令都必须再确认，也不表示新的默认交互已经接通。实现调整时同步核对真实命令，不仅改文档。

`scripts/yaohe.mjs` 是普通业务 API 的独立消费者。使用 Node.js 22 或更高版本及原生 `fetch`，没有额外运行依赖。运行前须已有可用的业务 API 和合法成员或会话身份；没有安装全局 `yaohe` 命令。

入口与`scripts/yaohe/`子目录一起使用：私有配置、HTTP传输、回执和具体命令分别由该目录内模块拥有，不能只复制入口文件。命令组复用同一身份与未知结果处理边界，不另存制作事实。

桌面构建的`build:programmatic`把CLI和MCP分别连同依赖打包为`Contents/Resources/programmatic/yaohe.mjs`、`yaohe-mcp.mjs`，纳入候选输入标识。这两个生成入口可独立运行，不依赖源码仓库或其`node_modules`。调用时将下方示例的源码路径替换成相应包内绝对路径。Node.js仍由使用环境提供；源码构建接线不代表已通过干净安装或完整安装包验收。

业务动作、权限和版本规则引用[架构 §3.8](system-architecture.md#38-editcompiler)、[认证边界](auth-workspace-boundary.md)及[领域模型](domain-model.md)。本页说明实际命令协议；能力与结果由服务端当前回执决定。

## 已批准扩展与当前实现边界

产品已要求CLI、MCP及飞书/钉钉可指挥同一创作系统，完整范围见[产品外部调用](product-requirements.md#123-climcp与消息产品指挥)和[架构适配边界](system-architecture.md#316-climcp与平台消息适配)。本页下方仍只列当前源码真实命令，不将目标能力写成已可执行命令。

当前CLI已有能力/素材/任务读取、编辑预览应用、图片程序、制作作业、历史控制及自然语言会话入口。自然语言意图的实际制作能力由API决定，入口接通不表示自主制作/逐条语言已通过。待补齐：完整定稿采用和逐条语言配置、安装包中的可发现入口、所有写入的独立持久回执及受权产物取得。尤其现有制作开始/恢复与撤销/重做的未知结果限制仍按下文执行；不能因新要求而盲重发。

MCP源码入口已提供下述stdio能力，复用CLI传输和业务命令；它不代表完整制作、安装交付或外部软件整条链路已验收。飞书和钉钉适配仍需各自实现与真实租户验证，不能从MCP接通外推。

## MCP本地连接

安装仓库依赖后，使用Node.js22以上启动`scripts/yaohe-mcp.mjs --config 私有配置路径`。SDK固定为`@modelcontextprotocol/sdk@1.32.1`，按[官方stdio方式](https://github.com/modelcontextprotocol/typescript-sdk/blob/v1.x/docs/server.md#stdio)协商；不会开放新的HTTP端口。客户端配置示例中的路径需替换为实际绝对路径：

```json
{
  "mcpServers": {
    "yaohe": {
      "command": "/absolute/path/to/node",
      "args": ["/absolute/path/to/repository/scripts/yaohe-mcp.mjs", "--config", "/private/path/config.json"]
    }
  }
}
```

工具包括`capabilities`、`task_inspect`、`material_search/inspect`、`execution_status/preview`、`job_inspect`、`conversation_create/inspect/draft/turn/request`、`edit_preview/apply/request`。参数schema由`tools/list`返回，动作细节仍从服务端能力目录取得。`yaohe://tasks/{taskId}`资源读取同一受权作品，不授予额外访问权。每次调用重新读取私有身份配置，由业务API验证权限；工具不接受凭据、工作区身份、任意URL或本地路径。

会话发送须先保存草稿，再提交相同正文、草稿和引用版本；原请求回执支持断线回查。精确编辑先预检，再按真实提案应用；明确用户指令可连续完成两步，不强制再问一次。未知结果只查原身份，不换ID重试。MCP连接断开不取消制作；当前未暴露制作开始/恢复、撤销/重做的直接MCP工具，待这些写入具备独立回执后接入。调用会话可能等待模型响应，不把协议请求完成等同制作完成。

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

## 自然语言会话

```bash
node scripts/yaohe.mjs conversation create --file conversation.json
node scripts/yaohe.mjs conversation inspect CONVERSATION
node scripts/yaohe.mjs conversation draft CONVERSATION --file draft.json
node scripts/yaohe.mjs conversation turn CONVERSATION --file turn.json --request-id UUID
node scripts/yaohe.mjs conversation turn CONVERSATION --file task-turn.json --request-id UUID --task TASK
node scripts/yaohe.mjs conversation request CONVERSATION UUID
```

create文件指定`scope_kind`、`scope_id`及可选`title`，例如`{"scope_kind":"home","scope_id":"home","title":"产品介绍"}`。创建对话不创建视频任务。

先inspect，再显式draft保存`{"draft_text":"只讨论介绍片文案，不开始制作","expected_draft_version":0}`；版本来自实际读取。turn文件提供相同`message`、保存后返回的`expected_draft_version`及`expected_reference_version`。CLI不替用户覆盖新草稿，不把浏览页面当作新目标。指定`--task`时还需`context_stage`，可传期望时间线版本/序号、选中对象及播放头，沿现有任务回合接口。

发送复用服务端Pi会话和业务幂等，固定UUID绑定同一内容；请求默认等待120秒，可设`--timeout-seconds`至300秒。完成回合不等于制作完成，读取返回的真实任务/作业结果。HTTP结果未知时保留原请求定位，通过request查询原回执；不按最新一轮猜成功，不自动重发或重建会话。create和draft也不自动重试。插话/撤回、随包发现及全部制作回执仍需独立接通验收。

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

局部证据读取 `/api/production/materials/ASSET/evidence`：`kind=segments|speech|words`，按源时钟 `start_seconds/end_seconds` 和 `offset/limit` 分页；后续页携带返回的 `updated_at` 作为 `expected_updated_at`。人工纠正、原ASR文字和实际逐词/词元对齐分开；句级转写不提供猜测的逐词时间。`RemoveTimelineRanges` 使用 `range_edit.removed_ranges_ms`（当前时间线毫秒删除区间）和 `reason` 沿共同精确编辑入口提交，音画、字幕与图层在同一作品版本联动，可整组撤销；源时间须先按实际镜头速度映射。

脚本仅接入当前API的Python/Pillow单PNG来源预览；可复用图片工具使用下方已冻结API；个人扩展加载和导出尚未接入，本CLI不猜这些路由。服务端能力目录中的其他入口也不代表本 CLI 已实现对应命令。

## Python/Pillow 图片来源程序

```bash
node scripts/yaohe.mjs script capabilities TASK
node scripts/yaohe.mjs script sources TASK
node scripts/yaohe.mjs script submit TASK --file script.json --request-id UUID
node scripts/yaohe.mjs request query TASK UUID --phase script
node scripts/yaohe.mjs job inspect TASK JOB
node scripts/yaohe.mjs job wait TASK JOB --timeout-seconds 20
```

能力来自 `/assistant/media-script-capabilities` 的真实 `MediaScriptPreviewRequest` schema。工具链、隔离环境或专用预算缺档时，以 `available/reason` 为准，不自动安装或执行本地Python。当前只支持单PNG输出，以及本任务已登记PNG来源输入；视频/音频脚本未接入。

`script.json` 使用API的 `script_source`、`language`、`purpose`、`parameters`、`input_refs`、`requested_outputs` 字段，整体读取上限256KiB。脚本命名空间遵循[架构中的程序接口](system-architecture.md#38-editcompiler)：`parameters` 是参数对象，`inputs[声明名称]`、`outputs[声明名称]` 是可信文件路径，使用 `image.save(outputs[声明名称])` 写文件，不覆盖映射项为图片字节。输入的 `source_ref/artifact_id/sha256` 须来自实际 `script sources` 回执；0输入也可绘图。脚本字段和数值契约仅由普通API校验，CLI不执行代码、不接受运行时路径。

```json
{
  "script_source": "from PIL import Image\nImage.new('RGBA',(64,64),(40,90,180,255)).save(outputs['panel.png'])",
  "language": "python",
  "purpose": "preview",
  "parameters": {},
  "input_refs": [],
  "requested_outputs": [{"name":"panel.png","max_bytes":65536,"width":64,"height":64}]
}
```

程序请求复用原请求查询与幂等协议，phase固定为 `script`；编辑预览和应用仍分别用 `preview/apply`。同UUID已经recorded时，本次代码/参数仍提交给共同API比较，不复制旧程序冒充本次成功；只复用原版本绑定。未知请求仅查询，不重POST或换UUID盲重试。

提交返回普通媒体Job。只有实际 `succeeded` 且返回 `graphic_source` 才取得可编辑PNG来源；这不表示当前时间线已变或成片已完成。需要使用时，另用实际 `source_ref` 创建 `EditGraphicOverlay`/`ImageOverlay` 类型化提案，沿既有预览、采用与生成流程。

## 保存和调用可复用图片工具

```bash
node scripts/yaohe.mjs tool capabilities
node scripts/yaohe.mjs tool list
node scripts/yaohe.mjs tool describe TOOL --version 1
node scripts/yaohe.mjs tool save TASK --file tool.json --request-id UUID
node scripts/yaohe.mjs tool request UUID
node scripts/yaohe.mjs tool update TOOL --file update.json
node scripts/yaohe.mjs tool revoke TOOL --expected-tool-version 2
node scripts/yaohe.mjs tool preview NEW_TASK TOOL --file invocation.json --request-id INVOKE_UUID
```

实际能力端点 `/api/media-script-tools/capabilities` 返回 `schemas.save/update/revoke/invoke`。列表、明确版本读取及停用状态来自同一API，不作为旧任务授权。仅用户明确要求时保存、更新或停用，程序成功不自动发布工具。

保存文件按真实Save DTO填写 `producer_job_id/name/description/parameter_schema`，只从本任务、当前成员真实成功的producer保存。更新文件按Update DTO填写相同元数据及 `expected_version`；停用使用明确旧版本CAS。程序代码、输出槽和运行时身份由API读取，文件不能再提供这些字段。API验证参数schema的有界子集，CLI不写第二套validator。

保存请求用调用方提供的稳定UUID；未知时最多回查原创建UUID一次，不自动重POST。已有原v1回执时仍由同POST比较本次真实元数据，差异拒绝，不能将旧内容冒充本次成功。更新/停用丢回应只读取工具HEAD，不能凭HEAD变化判定本次写入成功或自动重复CAS。

调用文件使用Preview DTO的 `tool_version/parameters/input_refs`，并绑定新任务的明确当前版本；不继承旧输入、成员或许可。请求定位仍用phase=script，结果为普通graphic_source Job。代码与输出契约由服务端从工具版本读取，等待/取消沿既有Job命令。来源成功不自动应用图片或出片。

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

音量和画面效果复用这组命令。`EditAudioTimeline` 的 `audio_edit` 使用实际 `action:update|track|add|split|duplicate|delete`、`target_id` 与对应 `values`；`AdjustVisualEffects` 使用实际 `clip_ids` 和非空 `effects`。参数修改只写指定字段，遗漏保持；add/track定位音轨，其余音频动作定位片段。delete的values为空对象，split使用整条时间线的at_ms，duplicate显式给timeline_start_ms，单位毫秒。gain_points使用从editor_gain_points读取的片段局部坐标，提交完整点列表，空列表清除。add使用已读取的受权source_ref、name、源起止与时间线起点；不能用update替换来源。`gain_db` 是绝对值：原音量 −9dB 降低6dB应提交 −15dB。音频淡入淡出单位是毫秒，画面淡入淡出单位是秒。可用字段、边界与当前可用条件以 `capabilities describe` 返回的共同 schema 为准。

```json
{"operation_type":"EditAudioTimeline","audio_edit":{"action":"update","target_id":"actual_audio_clip","values":{"gain_db":-15,"fade_out_ms":1000}}}
```

```json
{"operation_type":"AdjustVisualEffects","clip_ids":["actual_video_clip"],"effects":{"exposure":0.3,"saturation":0.8}}
```

这波未开放音量关键点写入、独立音频变速和持久独听；`solo` 仍是临时试听。预览、应用和成片是不同结果，采用草稿后须明确提交下面的制作命令。

每个新的预览或应用都使用调用方提供的 UUID。提交前先查询同一身份：未知时只保留查询定位；已经记录时，当前明确动作仍须经过共同 API 的幂等比较，差异由服务端拒绝。回执的 `submitted_input` 提供原提交的版本与摘要绑定；CLI 只复用这些绑定，当前动作参数或提案仍来自本次命令，不复制助手身份。没有可核验的原绑定时返回未知，不改绑当前任务版本或假冒原请求成功。提交连接丢失或服务端错误时最多回查一次，不自动重 POST。不要换 UUID 来盲重试未知结果。CLI 不持久化另一份成功状态。

## 作业读取、有限等待和取消

```bash
node scripts/yaohe.mjs job inspect TASK JOB
node scripts/yaohe.mjs job wait TASK JOB --timeout-seconds 30
node scripts/yaohe.mjs job cancel TASK JOB
```

沿任务的 `/media-jobs/JOB` 读取；取消沿 `/media-jobs/JOB/cancel`。等待默认 30 秒，可设置正数且至多 300 秒，只轮询 GET，不调用模型、提交后续动作或改变设备排队。期限到达仍排队或运行时返回待完成状态；成功状态缺少结果时返回未知。取消回执为 `cancelled` 时表示取消命令完成；它不表示媒体处理成功。

## 制作与原作业等待

```bash
node scripts/yaohe.mjs execution preview TASK
node scripts/yaohe.mjs execution start TASK --file execution.json
node scripts/yaohe.mjs execution status TASK
node scripts/yaohe.mjs execution wait TASK ACTUAL_JOB --timeout-seconds 30
node scripts/yaohe.mjs execution resume TASK
```

`execution.json` 只含实际制作预览返回的 `script_id`、`input_digest` 和显式 `requirements_confirmed:true`。CLI 不补确认或猜当前文案。`start/resume` 沿普通 `/execution/start`、`/execution/resume`，响应中的 `job` 是制作作业，不能用媒体预览作业的成功代替成片。成功需该作业 `succeeded` 且当前 `video_ready:true`。

```json
{"script_id":"actual_confirmed_script","input_digest":"actual_64_character_sha256","requirements_confirmed":true}
```

等待至多300秒，固定传入的作业ID；任务出现新作业时返回未知，不改绑等待。`resume` 保持原作业身份；若服务端创建新作业，需要 `status` 查明后显式等待新ID。明确 `start` 可返回新作业，并标识与此前作业是否相同。提交结果未知只 GET 原制作状态一次，仍返回未知（4），不重复 POST。该制作 API 没有每次提交独立回执，当前任务的作业查询不能证明丢失的提交已经成功。

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
