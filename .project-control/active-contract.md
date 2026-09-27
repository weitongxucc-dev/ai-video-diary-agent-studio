# 0.11 桌面闭环执行合同

## 当前授权与使用方式

ACTIVE。本文件只保留当前协作边界；已结束批次的指令和旧基线从活动入口移除，历史由 Git、PR、Linear 追溯。不得用历史提交号把现有工作树回退或覆盖。

- 主控只负责共享文档、派工、审核、集成与开发分支推送，不编写产品代码。
- 实施使用用户授权的侧边栏可见 Codex 线程及 cmux Pi/Kimi、Claude Code/GLM，不派内部子智能体。保留现有模型配置。
- 集成仓库：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`；分支 `codex/0.11-development`。每包基线由主控按实际 Git 给出；仅吸收审核通过的具体依赖。开发分支可推送，不自动合并 main。
- 不授权新增付费调用、真实平台发送/发布、签名、公证、真实用户库迁移或覆盖已安装版本。验证使用隔离数据；保护未跟踪记录和其他工作树的未提交修改。
- 长期权威从[文档路由](../docs/README.md)按需读取；完整需求和 Linear 映射见[开发台账](0.11-development-plan.md)。本合同不维护完成率或 CI 快照。

## 文件所有权

下表是当前所有权，不是要求重新执行已结束工作。新包须给出结果、精确文件、依赖和退出条件；同一文件不得多人同时写入。未列出的媒体叶子文件不得因接线需要自动扩权。

| 负责人 | 可见线程 | 所属文件或目录 |
| --- | --- | --- |
| MAT01 素材工程 | 01a0d259-54c3-7621-b055-17ce9146cac4 | apps/api/app/production.py、material_ingestion.py、material_probe.py及对应专项测试 |
| MAKE01 制作流程 | 01a0d259-67ee-75f1-ab83-8982eec022c7 | apps/api/app/main.py、creation_execution.py、schemas.py、assistant_pi.py、apps/api/agent_tools/diary_tools.ts；apps/web/src/components/CreationTaskWorkspace.tsx、src/lib/api.ts、types.ts及相邻专项测试 |
| UI01 桌面实现 | 01a0d259-8b42-7d30-82c5-8834f31842e5 | AppShell、EditorShell.module.css、VideoEditor、MaterialLibraryWorkspace、HomeWorkspace、KnowledgeWorkspace、ExecutionQueueStatus、useEditorPanels及相邻样式/测试；knowledge/page.tsx、loading.tsx；modern-desktop-contract-check.mjs |
| SCHED01 整机调度 | 01a0d29d-226b-7460-8ba6-76f14dc8c56a | device_scheduler.py、device_runtime.py、host_resource_probes.py、granted_subprocess.py及专项测试；database_migrations.py新增调度迁移；api pyproject/uv.lock仅已批准psutil依赖 |
| DESIGN01 界面设计 | 01a0d25f-5080-7991-9e9c-54efde64d4e9 | 自己工作树的 designs/desktop-011/，不写生产组件和DESIGN.md |
| AUTH01 账号共享 | 01a0e1c4-23e1-71b1-b23a-22ea2993e7ef | 当前仅账号共享接口提案；产品代码写入须另分配精确范围 |
| QA01 独立验收 | 01a0d25f-53ad-7a83-8197-16f9d0a17aa2 | 自己工作树的 work/qa-011/ 隔离证据；不修改产品代码或共享规则 |

后端省略目录的文件位于 apps/api/app/；前端组件位于 apps/web/src/components/。cmux 当前临时接管的文件以下表为准，原负责人不得同时改。Repository接口交MAT，main和任务父层交MAKE，DDL交SCHED；主控裁决跨域变更。

## 调度与交付边界

- 单个本地重活许可、可信成员和对象授权、真实进程释放是生产接线前提；缺资源或身份事实时不能自行放行。
- 素材元信息探测完成不代表场景、语音、视觉理解完成，不把资产提前标为可用。scheduled手动重试须真实接通授权入口后才能宣称可用。
- 制作队列和状态展示可先集成；尚无受许可媒体执行器时保持dependency，不能把排队接线称为真实连续制作。startup门禁未完成前不启用scheduled生产运行。
- 取消请求不等于子进程已退出；确认进程树释放后才可让出许可。检查点与调度完成使用同连接事务；具体接口由当前代码及架构权威确定。
- 默认legacy与受控scheduled阶段并存期间，禁止同时启动两套重活驱动。全入口迁移仍需单独证明旧绕行路径退役。
- 组件、API、真实媒体、浏览器、安装包和外部模型分别验收；三尺寸为1440×900、1180×800、1024×768。局部通过不标记父范围完成。

## 2026-09-27 用户确认的整体界面实现基线

用户明确认可 `http://127.0.0.1:4327/workspace-dual-v4/owner.html` 为基本达到70分的整体方案。其主账号与 `member.html` 成员版本是桌面UI整体实现基线；不能仅修旧外壳浮卡而遗漏这版页面组织与视觉结构。主控已实际读取该原型入口，源目录由设计负责人提供并固定。

- 保留导航—持续创作助手—核心工作区三栏、可拖动分隔及收起、紧凑字号间距，以及右上精细修改浮卡。首页按这版总量、趋势、行业分布、任务阶段、可点词云、近期作品的组合布局落地；其他导航页逐页对照，不退回长条列表替代方案。
- DESIGN-V5只能在v4上修正已确认的九阶段、交互缺口、密度和权限等问题；未经用户确认不得另起整体布局。先交v4到组件与产品页面的映射、差异及受控改动，不以新原型替代真实组件交付。
- UI-FLOAT是整体方案落地的子项，其局部通过不代表整体UI通过。桌面负责人先给v4与现有组件逐页差距，主控据此分配具体页面；共享CreationTaskWorkspace/API仍由MAKE写，不得跨写。
- 原型的示例数据是布局与交互依据，生产页面接真实服务；缺失能力明确显示状态，不把示例数字或按钮点击当功能已实现。QA除尺寸检查外，要逐页比对已认可v4结构和交互，分开报告示例、真实组件和真实业务链路。

此补充纠正此前仅派浮卡局部修补的遗漏，优先于与该整体基线冲突的旧UI派工；保留现有成果，不要求回退用户代码。

### UI-V4 第一实施包：外壳与首页

UI01独占本包的 `apps/web/src/components/AppShell.tsx`、`EditorShell.module.css`、`HomeWorkspace.tsx`、新增 `HomeWorkspace.module.css`、`useEditorPanels.ts` 及对应AppShell/HomeWorkspace/useEditorPanels专项测试。布局hook仅为v4默认导航188px、助手280px及必要最小宽度、拖动/收起/恢复一致性调整；已有用户手动保存宽度不得静默重置。浮卡修正与首页改动分开提交。不得写MAKE的任务父组件、API/types或后端。

首页按v4组合看板实现，可信现有数据可用，缺聚合接口保留对应结构与诚实空态；隔离组件展示可用显著标注的示例供两身份三尺寸验收。角色未接真实服务不得伪造管理权限。MAKE先提供同源聚合和九阶段接口提案，知识库、模板及其他页面后续逐包指派，不因本包未包含而从范围删除。

### UI-V4 第二实施包：知识库页面

UI01独占 `apps/web/src/app/knowledge/page.tsx`、必要时同步其 `loading.tsx`、新增 `KnowledgeWorkspace.tsx`、`KnowledgeWorkspace.module.css` 及该目录 `__tests__/page.test.tsx`。页面入口保留数据加载，展示组件独立以便隔离示例和测试，不从Next页面入口导出额外组件。按v4分类入口、来源封面网格及可操作详情实施；保留现有搜索、来源信息、原文和真实读端。用途分类不能从文件格式擅自推断，缺分类数据需明确未分类；没有真实引用/版本/纠正接口的操作不得伪造成功。原文及摘要只显示已有事实，不能编造解析、主题关系或问答出处。隔离示例可用于完整布局验收并明确标注。

本包不写 `modern-overrides.css`、API/types、后端或其他页面；旧全局选择器如确实阻断，先提交具体冲突选择器与局部解决证据。第一包与本包分开提交；知识库页面改造不代表知识解析、召回及权限业务完成。


## 当前实施包与依赖

| 执行者 | 本批结果 | 独占范围与边界 |
| --- | --- | --- |
| cmux Claude / GLM | 修后端叶子模块 Ruff，保留逻辑 | audio_timeline、compound_edits、graphic_overlays、graphic_sources、local_portrait、subtitle_edits、subtitle_schedule、visual_effects；只修诊断位置，AST等价，禁止全仓格式化 |
| cmux Pi / Kimi | 修 CI 日志中的测试规范问题 | test_audio_timeline、test_canvas、test_compound_selection、test_graphic_overlays、test_linked_timeline、test_montage_pipeline、test_production_foundation、test_silence_edits、test_subtitle_alignment、test_subtitle_edits、test_subtitle_style、test_transitions、test_visual_effects、test_voice_clone；不删除断言，不改生产 |
| UI01 | 更新过时扫描约束，再对接真实队列事实 | modern-desktop-contract-check.mjs及必要相邻检查测试；不弱化检查，不恢复手动扫描为默认；后续展示仍沿原UI归属，新增文件先列范围 |
| SCHED01 | 真实资源/进程探针和执行前注册子进程 | 新host_resource_probes.py、granted_subprocess.py及相邻测试，device_runtime必要适配；允许仅为可靠采样新增psutil至api pyproject/uv.lock，无无关升级；无可信测量则unknown，不启用生产 |
| MAT01 | 获许可后的有界真实ffprobe及检查点恢复 | 原production/material_ingestion及测试；新增material_probe.py及专项；不造队列、不改main/DDL，场景/视觉未实现不得报完成；production.py规范窄修单独提交 |
| MAKE01 | JobRun提交、获许可阶段执行与恢复适配 | 原main/creation_execution/schema及既定前端API文件；creation_execution规范窄修独立提交；host/媒体依赖未齐不激活新生产路径 |
| QA01 | 单许可、活子进程、超时/取消、两空间竞争的独立证据 | 原QA隔离目录；合成候选只做一次必要集成验证，失败只阻断对应包 |

两位cmux使用已清洁的既有工作副本，新分支分别为 `codex/ci-ruff-production`、`codex/ci-ruff-tests`，基线均为13b253f。除上述测试文件暂交Pi外，既有归属不变；MAKE/MAT不得同时修改这些CI清理文件，需变更则先交主控协调。

推进规则：工作者完成首个可审提交后继续其已授权剩余范围；依赖未到时完成不依赖部分并报告具体接口，主控接收后审核、合成、必要复验、推送、同步Linear。候选完成不等于整个版本完成，也不自动解除生产切换门槛。后台监控是否启用以实际自动化配置为准，不能由派工消息推断。
