# 剪辑资源库能力记录（2026-11-13）

## 当前目录

曜核已有 OpenFilm HTML 视觉目录和 RenderComp 数据动效目录。每个可执行视觉模板都保存了资源仓库、固定修订、代码许可证、源路径和 SHA-256；模板列表和 Agent 工具会回带这些 provenance 摘要。Agent 还可以调用 `read_editing_resource_catalog` 查看当前分类、接通状态和实际入口。

后端只读目录接口为 `GET /api/editing-resource-catalog`（`q`、`category`、`status`、`offset`、`limit`），并提供 `GET /api/editing-resources` 列表别名、`GET /api/editing-resources/{resource_id}?version=1` 版本详情和媒体预览路由。返回的 `categories` 是功能导航分类，`resources` 是具体资源项；每个资源项包含版本、预览元数据、来源/许可证、运行依赖和 `EditProposal`/`TimelineVersion` 写入边界。接口不创建提案、不写时间线。

对已登记 Skill 可调用 `GET /api/editing-resources/{resource_id}/preflight?version=1` 回查安装前检查、能力声明、采用和回滚边界。该接口只读，不下载、不安装、不连接外部服务；运行时仍须先通过包内 manifest 校验。

底部资源在产品上按以下类型组织：素材、技能、模板、字幕、配音、音效、音乐、文本/贴纸、滤镜、转场和动效。当前生产入口已经接通的是素材检索、受控 Skill 读取、模板/视觉组件、字幕、配音、OpenFilm `Glass Pulse` 音效、画面效果、文本/贴纸和内置转场。OpenFilm `Glitch` 已进入目录并可预览，但 CSS 转场写入适配仍未完成，因此保持 `catalog_only`；音乐仍是 `catalog_only`，滤镜/LUT 是 `planned`。

当前随版本发布的外部 Skill 包括 `cut-as-code-video-cut`（WhiteTowerAI/cut-as-code，`a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4`）和 `openfilm-replicate`（openfilm/skill-replicate，`eef1ee5eda2a2aaeec60a3f133d73b86dd53c053`）。二者只作为方法文档适配进入曜核，必须通过 `read_creation_skill` 和现有任务工具执行；网络、Shell、任意代码、外部下载及第三方运行时均未导入。

它的 manifest 还声明了 package-time 安装检查、`read_creation_skill` 只读采用入口、明确编辑授权要求，以及不删除用户数据的固定版本回滚边界。目录会同时回带 `license_spdx`、输入/输出/权限/依赖和评测状态；Skill 资源在 HTTP 资源目录中保持 `connected`，Agent Skill 目录中保持 `installed`，`Glass Pulse` 音效为 `connected`，音乐仍为 `catalog_only`，滤镜/LUT 仍为 `planned`。

## 采用边界

- 资源进入产品时记录来源修订和文件哈希，方便回滚、复现和运行排查；这些记录用于治理，不再阻断小范围采用。
- Agent 只能引用当前工作区实际可见、当前版本可读的资源；保存为用户模板时保留原 provenance。
- 外部资源先只读索引和预览，再通过现有 EditProposal/TimelineVersion 写入；不让第三方资源直接改时间线。
- 资源包版本、素材哈希和模板版本进入任务输入，渲染结果仍由曜核的时间线和产物回执负责。
- 安装前检查 schema、名称、固定 provenance、运行时关闭网络/Shell/任意代码和业务工具 allowlist。Skill 本身不写时间线；具体编辑必须经过既有业务工具和明确授权。
- 预采用阶段回滚是移除当前固定包并恢复上一份通过校验的版本；已采用编辑沿原请求、版本和既有撤销/恢复入口处理，未知结果先查询，不重放新请求。

## 后续顺序

资源目录、面板和受控 Skill 导入已形成第一版纵向切片。下一步按真实业务入口补齐贴纸、滤镜、LUT、音乐和音效的独立资源包，并为每类资源补齐真实预览、采用回执和输出观察；OTIO/FCPXML/EDL 交换和外部 Provider 放在稳定的资源 provenance 与时间线写入边界之后。
