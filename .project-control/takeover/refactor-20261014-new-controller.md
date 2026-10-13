# 全面重构：新主控与五线程接续交接（2026-10-14）

这是用户要求的短期交接快照，不是第二份产品权威或实时进度表。先读当前 `../active-contract.md`、`../../AGENTS.md`，核实时 Git 与本目录同名 JSON；旧线程、旧 HOLD、本文候选和测试结果都不能越过当前身份/写权绑定。**恢复的是同一 R0–R9 工程，不从头重做。**

## 用户决定与接手方式

用户已批准全面重构并要求高内聚、低耦合，允许新建可见线程/主控直接实施；最新明确要求：汇总当前安排，重新交接主控，由新主控重新开启同样五条实施/审核线程，减轻长期上下文负担。新主控使用 fresh `create_thread`，不 fork 老历史、不派内部子智能体。本次交接不是暂停整体目标，也不是新的产品/数据/发布授权。

唯一 checkout `/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`，分支 `main`；交接前源码 HEAD `4602e8979f7b514778b0c6b41703bacc673fd50f`。本交接自身另有文档提交，按实时 Git 核对，不回退到本 SHA。共享脏树留在原地，不能 reset/checkout/stash 覆盖，不复制用户库/账号/凭据。不签名、公开发布、覆盖原应用或删除用户数据。原 8000/3000 和用户服务不作为本轮测试清理对象。

新主控先只读接收本包并 ACK；当前主控完成停写、精确快照与合同身份切换后，发正式接管指令。**新主控成为唯一派工/共享文档/stage/commit负责人后，自己创建五条 fresh 可见线程，登记新 ID 与各自沿用范围，收到语义正确 REBASE_ACK 后继续。**旧范围正文说明工作职责；旧 ID 不再赋予写权。已有未验源码先审核，不能以重开线程消除失败或重跑全部历史。

## 整体完成判断

尚未完成 R0–R9。用户最近收到“约四成”的保守工程范围估算，未建立工时统计；它不是产品通过率或权威完成指标。完整编辑/执行、资产与人物仓储、显式冷启动/重载组装、所有旧消费者退役和同候选真实核心闭环仍有必要工作。最初“1秒镜头可靠调整”的同候选真实成片验收未通过，不能报彻底解决。

源码/合成夹具/真实 HTTP+SQLite/GUI/原生壳替身/生产媒体/全候选接受分别报告。前几层通过不能替代后三层。真实媒体资源门拒绝不得靠假预算、放宽采样或改保护分支变绿。

## 已集成，可复用而不重复验证

以下均来自当前 Git 和本轮源码/独立证据，完整提交与必要 gate 可由列出的路径定向核验；不全仓/历史扫描。

| 提交 | 实际结果 | 限定证据入口（仓库相对路径） |
| --- | --- | --- |
| `4602e89` | 资产五个人工命令归 `materials/assets.py`；production 薄委托，唯一 MaterialVersionConflict 同对象，旧 SQL 和多余集合 helper 导入退役 | `test-runs/refactor-20261014/manual-mutations-public-independent/SOURCE_GATE.json`：5项实际公共入口反例通过；root `manual-assets-root-review/combined-source-review.json` |
| `e6f8d35` | Whisper 单文件存在规则供 config/health/preflight 三处消费 | `whisper-presence-public-fixed/SOURCE_GATE.json`：3项真实消费者、固定基线+7 overlay；仅文件存在/选择规则，未验实际转写 |
| `7c8ccd7` | 中立媒体观察值及资产 get/list/page/save/analysis_only 存储，调用方现 conn 保留、晚分析人工事实保持、两表失败回滚 | `asset-public-independent/SOURCE_GATE.json`：5项；root `asset-core-root-review/accepted-source-review.json` |
| `04309bb` | 复合编辑具体计算迁出；采用从实际 ChangeSet 和现事务 conn 重算真实影响范围 | `compound-v2-public-independent/SOURCE_GATE.json`：7项；旧 v1 wrong-empty-scope 为真实 BLOCK，不替代 v2证据 |
| `c9aac57` | 关联编辑具体用例；intent/proposal 同现仓储事务与版本 CAS，避免孤立 intent | `linked-preview-atomic-v2-qa/SOURCE_GATE.json`；v1 orphan intent BLOCK 保留 |
| `0a1013f` | 集合 owner 同 conn 父级读取/UPSERT；四 JSON/date helper 唯一 `persistence_json`；profile checkpoint 消费者接续 | `collection-public-http-and-conn-fixed/SOURCE_GATE.json`、`collection-checkpoint-independent/result.json` |
| `93daa31` | 业务名称与关联 owner，回执解码在事务内，坏日期拒绝不半提交 | 业务存储相应本轮 gate，按已列 commit 源码核对，不重复原始失败矩阵 |
| `f4d41cb` / `c1dd990` / `182fa22` | 本地状态、服务状态解释、dotenv IO 各唯一 owner，秘密/工作区边界保持 | `local-runtime-public-independent/SOURCE_GATE.json`、服务状态/IO 定向 gate；不是 provider 或模型真实成功 |

更早的资料/授权事务、人物/声音中立契约、镜头/音轨编辑子组、Web职责与三轨布局/当前事实/死选题消费者退役、Pi/CLI/宿主、桌面 runtime/资源/安装器、手机连接 epoch+有序凭据队列已分批集成。按目标符号和 Git 定向验证，不重跑这些已过子批。手机原生/真实接入仍未验。Electron 已做独立未签名 QA 壳真实窗口/选择保存/退出重启，但 API/Web 为自建 stub，不能算产品制作闭环；证据 `electron-native-shell/native-review.md`。

## 新 API 编辑/执行线程

旧 ID `01a13610-8468-7580-9b33-d854f0ad8b7d`；当前 main/production/schema 巨型文件只有这条纵向 owner，具体范围沿活动合同，排除已转交 R4/R6 的整文件。新线程不得与资料线程同时写 `materials/assets.py`，不得修改 R6 config/health/preflight/IO 等文件。

保留 WIP：`app/main.py`、`app/audio_timeline.py`、新增 `app/creation/editing/subtitle_changes.py`、`tests/test_montage_pipeline.py`、新增 `tests/test_subtitle_change_use_cases.py`。完成度和确切最后 hashes 以 HOLD 回执及 JSON 为准，不把 standalone 计算通过当完整入口接受。

字幕内容/结构/样式/替换/合并/删除计算及实际 preview/adopt 接线已推进，部分真实 API/SQLite 用例通过。样式媒体验证首个实际缺陷为新音轨 float 字段的整型默认值，持久回读后 int→float 导致封存 key 漂移；`audio_timeline.py` 只改四个 float 默认值为 1.0/0.0，durable roundtrip-key 用例已过。其后出现真实资源探针缺失，再进入宿主内存/Swap 拒绝，媒体项仍失败。旧采样 seam 正移除；不得放宽实际资源/用途规则。

精确已知失败入口：`test-runs/refactor-20261014/subtitle-style-float-defaults-fixed/pytest.log`；后续当前决定/拒绝证据路径从最后 API HOLD 回执获取。`RecoveryBlocked` 后缺结果 event/MP4 是后果，不能当第二个根因盲修。实际输入图、key、候选 keys/参数差异应保留；合成预算不称生产标定。

API最终HOLD补充：未发布上述五源；最新 `subtitle-draft-active-consumers` 的失败是测试错误要求 `video_url` 字段不存在，实际 schema 返回 `None`，尚未改正；先按真实协议修正夹具，不能改生产字段凑断言。所有精确五源hash已入同名JSON；production当前干净，无常驻服务。

下一步：先评审稳定字幕实际消费者/恢复子组及 float identity 负例，把未过媒体门单列；接 R4三命令模块实际 production消费者，再推进完整 R2 restore/undo、R3显式执行/取消/恢复/回执/目标接续、最后显式 bootstrap。`_execute_task_media_operation` 仍有伪造 Request/反射/路由重入等旧消费者需要逐项退役，不能一个短预览过了就宣称全部退出。

## 新资料/档案 R4线程

旧 ID `01a13630-0dc5-71a1-90af-cbbea77dcace`。当前独占 WIP `app/materials/assets.py`、新增 `tests/test_material_asset_review_and_usage_store.py`：use_state/review/record_segment_usage 三个命令同一 BEGIN IMMEDIATE、同 conn 读/改/写，提取有限连接内写 helper，保留普通/analysis_only 唯一原保存 SQL。五个人工命令、中立观察源与权限/外部协议不做无关修改。

两个已确认、必须修复的真实 SQLite 反例：

- `R4-usage-atomic-counterexample-40/RESULT.json`：usage INSERT 先提交，第二个资产写失败后 usage=1/count=0/null，同键重试仍1/0/null。固定原4方法 AST、实际 store/neutral源+真实第二写 trigger，不是整产品/渲染接受。
- `R4-use-state-concurrency-counterexample-42/RESULT.json`：实际旧get暂停，合法人工标签写成功，再旧use_state save成功把新标签覆盖回旧标签；两个调用均成功返回。以同conn事务修复，不增加 CAS字段/权限/格式，不自动猜修历史用户行。

`R4-use-review-usage-readonly-41/REVIEW.json` 给出 owner/消费者/规则。usage幂等键(workspace,plan,clip)、UUID缺省/重复noop、审核前提/角色继承/使用限制保持。跨任务/方案的 `list_segment_usage_records` 是读取协调，不能整段塞资产仓储或给它 production 回调。

模块 SOURCE_RELEASE 后由 API 唯一接 production 三个薄委托/旧 body 退役，联合公开仓储 gate 之后才接受。摄取/发现/probe/场景/检索/人物仓储仍剩余，不能把该小组当整个 R4 完成。

R4最终HOLD补充：两WIP源尚未SOURCE_RELEASE；`R4-review-usage-tests-44` 新23项+受保存helper提取直接影响旧3项共26PASS，前后无source drift。`R4-review-usage-baseline-43/BASELINE.json` 保存原AST；新 `_write_asset` 只有原UPSERT statement，旧5人工/core函数及全局/异常保持。actual production三个入口仍走旧逻辑，修复尚未接入公共路径，必须由新API接续。两源exact hashes已入JSON，无常驻PID。

## 新 Web R5线程

旧线程 `01a13626-c206-74f1-a258-ae4d9d86c7a8` 已连续3次 remote-compaction系统失败，没有可用当前 turn；已发 HOLD。其最后磁盘 SOURCE_RELEASE 已完整保存，不丢源码或从头重跑。为救援临时新建的 `01a13765-af10-71c3-a47f-b2fc151c233b` 只做只读预检，无写权，已 HOLD_ACK；用户随后要求全套 fresh 重开，它不承担新轮永久租约。

待审冻结6源：AppShell.tsx、CreationTaskWorkspace.tsx、VideoEditor.tsx及三个相应 tests，清单 `test-runs/refactor-20261014/r5-web-consumer-fix/SOURCE_RELEASE.json`，digest **4cd9915baf6c5eec6dbb8a5311a29d07d8501710a1dc047bbb35c9324864b956**，recipe sorted path+TAB+sha+LF。root 六source/patch/screenshot hashes已匹配；看过快捷单卡、本地提案承接、2倍源终点截图。QA独立组件门尚未完成，**未集成**。

同目录 `result.json` 是固定 c9 API+三 Web overlay，真实 browser→Next→Uvicorn→新合成 SQLite的10项限定接受，含真正同SPA loaderId的旧task晚回保护、真正消费输入采用后空PATCH及SQL版本变更。此前整页跳转不算 SPA；补充 v3已完成这些缺口。67个局部用例/类型检查过，但不替代独立门。所有3 source hash在各实际子运行保持。单独 live diagnostics/waveform、fixture身份、关闭lifespan均诚实单列；黑屏/缩略图失败/未生产媒体不算通过。

实际 PATCH/SQL：`fixed-draft-patches.json`、`supplement-http-trace.jsonl`、`supplement-db-owned-input-cleared.json`、`supplement-db-spa-old-result-settled.json`。两卡互斥、按钮不清无关草稿、后来输入/409/任务切换保护、2倍后 source0–6/timeline1–4 两输出6已记录。原10项/三尺寸/旧后端四动作不重复；新 QA至多补必要独立输入归属/同文再次输入/晚回及互斥反例。新 Web先核6字节并等待 gate，不能为重开线程改冻结候选。通过后再沿原 R5范围接后端稳定consumer。

临时端口3241/9241、3243/9243、3245/9245、tabs已释放；救援线程再次确认无监听。需要GUI时新上下文先读工具文档，复用隔离plan，只补当前缺项。API、useAssistantConversation和root接手的transport四文件保持只读。

## 新工具/安装/资源 R6线程

旧 ID `01a13610-8970-7a50-96f9-244e1e37d481`。最近实现已全部集成，当前是 settings/model/reload有限符号只读审阅，无产品 WIP。不机械搬短 getter或把设置包传给所有模块。

最后静态结论：HTTP本地来源限制/workspace/请求/错误映射应留；模型列表/安装/作业读取已经薄委托唯一 installer/catalog，无需再包一层。当前两类配置未发现同份双写；workspace+device保存与reload也不是整体事务，不能称原子。

值得处理的两项 **静态、尚未运行复现** 风险：reload改 DATABASE_PATH 后未重建 conversation/quality/local-member/platform-auth/scheduled-task五个DB服务；冷启动/重载构造已分叉。另有 installer先rebind/驱动先停止、后续临时目录配置可能失败的半切换顺序。先按现 `test_host_model_installation.py` 合成反例验证，再由 API唯一bootstrap owner统一初始化/重载；R6不跨写main，不盲新建框架，不碰私人env/模型/安装下载。最后报告实际路径从 HOLD回执获取。

## 新独立 QA线程

旧 ID `01a13610-8dcc-7d33-8b7d-75b47619301d`。只写本轮 test-runs evidence/probes与允许的隔离依赖，不改产品/shared docs、stage/commit。

manual五项和Whisper三项已过，精确提交绑定已补，不重复。Web6源当前只完成清单/报告/部分源码审读，组件独立门没有跑完，不能 SOURCE_PASS。R4三命令的六项公开探针已准备未执行；沿旧 QA HOLD给出的脚本继续，联合 module+production清单后才执行。重点二写全回滚/同键重试exactonce/相同不同键竞争/人工事实保留/审核用途与跨workspace。

API环境Python3.11锁定 `.venv`。相关 runner `test-runs/refactor-20261014/run_api.py`、`pytest_entry.py`、固定snapshot runner；任何actual main导入必须先绑定合成primary+secondary env roots、DB/output/tmp，不能读私密dotenv/账号。外部pytest文件用绝对路径。runner不继承任意env，QA_BOUND_SOURCE_ROOT不在普通runner allowlist，不为凑通过放宽。whisper先两次依赖/冻结Settings夹具失败是QA harness问题，已修并保留，产品无改。

QA最终HOLD：六项准备脚本 `test-runs/refactor-20261014/test_asset_usage_public_qa.py`，SHA `a1f4493eea1d9e0c7ed745fb532e9f869f94ccef1c7cc7d176583a6e9df8b05d`，未执行；Web未编新组件反例/未独立复核截图。两个已过候选的 `integrated-commit-binding.json` 在上述manual与Whisper gate同目录，均已绑定。不重复已过门；没有持续进程。

Node路径 `/Users/Zhuanz/.nvm/versions/node/v24.16.0/bin/node`；Flutter `/Users/Zhuanz/development/flutter/bin/flutter`。过去runner/source closure只从精确Git base+有限overlay封存；不能混入并行WIP正式接受。sources摘要recipe各候选不同（默认JSON空格、compact JSON、NUL或TAB lines），照清单，不能猜。

## 主控接手顺序

1. 核唯一合同身份、旧所有 writer停写 ACK和same checkout实时Git，读本包/JSON及精确 HOLD文件；只需目标章节，禁止archive/整个docs/整仓/输出历史扫读。
2. 将旧主控替换为自己，创建五条 fresh可见线程，登记ID与互斥范围；每组小任务带完整目标/旧退役/同候选验证/现WIP处置，别让线程继承整份老对话。QA先审核已冻结Web及新R4，API继续稳定字幕+R3，R6按静态风险先给真实反例。
3. 先集成现稳定候选，再立即 SOURCE_INTEGRATED/WRITE_LEASE_RETURN，不让某批冻结等另一批；整文件单owner，stage精确路径、cached diff check单独查退出、staged/commit字节匹配，不能stage生成证据/他人WIP。
4. 当前10分钟heartbeat `automation` 名“曜核重构线程监督与接续”，改target为新主控，prompt按活动合同新角色表动态读取。只重要变化通知，未整体完成不停监督；旧主控移交后停止派工/产品施工/集成，不继续监督旧ID。
5. 用连续核心实际输入→预览→采用→undo/restore→真实输出与启动恢复验证全候选；旧目标RETIRED及不存在证明完成后才收口。受阻的media/native/phone项单列，独立已授权结构继续，不猜预算或降低资源门。

最后的实时 HOLD回执、精确WIP hashes、游标/证据路径补入同目录 JSON；新主控若发现与本文不一致，以最新合同/Git/actual release为准并做最窄修订，不把快照当当前执行命令。
