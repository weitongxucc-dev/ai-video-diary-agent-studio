# 0.11 桌面闭环执行合同

## 当前授权与主控

ACTIVE。2026-09-29 19:12（Asia/Shanghai）完成交接登记：唯一主控为 Codex 聊天 `01a0ecd6-dc91-7500-8f03-45e2dbde4820`。用户新建主控聊天接续项目；cmux Claude 已书面确认停止全局派单、集成及共享合同写入，且没有更晚的禁止交接指令。原 Codex 聊天也停止派单和集成。

- 交接回执：`/Users/Zhuanz/Documents/Codex/2026-09-24/volumes-dujiangyan-vm-ai-repositories-bd25/external-agents/controller-handoff-20260929-response.md`。回执作交接证据，不定义产品规则。
- 主控负责规划、共享文档、派工、审核、集成与开发分支推送，不编写产品代码。不派内部子智能体；继续使用既有可见 cmux 执行者，不更改模型配置。
- 集成目录 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`，分支 `codex/0.11-development`。项目保存入口 `/Users/Zhuanz/Documents/AI视频日记` 指向另一旧 checkout，不在那里开展本轮集成。基线逐包给出，不用旧 SHA 回退工作树。
- 开发分支推送授权保留；用户已明确授权合适时合并 main，须先审核候选范围、必要验证和未解决阻断，不把派工完成当作合并条件。真实平台发送/发布、新增付费调用、签名、公证、真实用户库迁移、覆盖已安装版本仍不在授权内。测试只用隔离数据，不打开 `~/Movies/AI视频日记/agent.sqlite3`。
- 保护未跟踪的 `.project-control/0.11-bug-backlog.md`、`.project-control/handoff.md`、`work/integration-011-tmp/` 和各工作树的用户改动；不得 reset、清理或覆盖。
- 本文件只保存当前约束和文件归属。已结束批次、测试计数及旧主控登记从活动入口退役，历史由 Git、Linear 和原始交付报告追溯，不重新激活旧 Codex 线程。

## 执行效率（2026-10-01 用户明确要求）

- 不做无依据的防御性编程：只修真实问题、已定义业务不变量和实际边界；不顺带增加猜测式兜底、兼容层或泛化抽象。
- 每项验证必须回答具体问题。优先复用未受改动影响的已有证据；不为数量、覆盖率或报告形式新增测试，不重复跑已通过且未变化的检查。
- 主控先审diff与执行者证据，只有集成确实改变相关行为、证据不足或发现新失败时才追加定向验证。不默认再由第二人完整重测；真实入口必要验收保留，未验不得报通过。
- 派单一次给清结果、文件归属、交付判据和实际阻断。低风险修正直接推进，不反复要求已确认的ACK、不为例行进展增建文档或issue。
- 达到既定交付判据立即收口；非阻断优化不能无限延长审核。以版本关键依赖和可用功能排优先级，不以线程占满、测试数或关卡数量充当进度。

## 权威和保留的暂停

长期规则从 [文档路由](../docs/README.md) 逐步读取；完整需求追踪见 [开发台账](0.11-development-plan.md)。0.11 桌面与 0.12 手机分开，追踪总表不是本轮全部必交付。

1. **0.11 Linear 整体重构已批准**：用户最新明确要求按0.11需求重构难懂、过粗的issue，以便多Agent协同。允许主控实际改写标题和描述、重排父子/关联/阻塞、补必要工作包及视图；优先复用现有ID，先保存原始内容及需求索引，保留历史交付和验收记录。旧“仅试点/先审迁移方案”的限制已被本次具体授权替代。本次只重构任务管理，不自动授权产品施工、解除既有HOLD、平台动作或真实数据迁移。Linear仅主控写入。
2. **成员可见性 HOLD**：WIL-110 / WIL-112 / WIL-116 及相关施工保持暂停，不因主控切换或命名整理恢复。候选 `5fcb8ac` 保留、不合入；`sources.py`、`source_repository.py`、`production.py` 的可见性范围和相关迁移/测试不转派；目录检查局部例外仅按本轮明确白名单执行。用户已决定新建 private / 存量 shared，旧派单中新建 shared 的文字失效；本次Linear描述已纠正。恢复仍须完成相关权威和具体候选同步、执行者重新绑定，并满足原明确HOLD条件；不重复询问已经确定的默认值。
3. 本地多成员沿用户指定的本地 SQLite 方案核验；既有 Supabase 路径不删除、不自动激活。当前身份绕过面和读写授权缺口不得表述为安全隔离已完成；也不得借交接自行重设计身份架构。安全稳定规则仍见 [认证边界](../docs/auth-workspace-boundary.md)。
4. scheduled 生产运行仍关闭。WIL-92 是只读施工计划，不授予 main.py 写权。启动互斥阻断不等于准入门，OwnerRegistration、CPU预算、真实重试授权和端到端释放须分别核实。不得因接口/测试存在宣称 scheduled 已可用。
5. 真实发布不排期、不执行。发布授权和草稿的本地 mock、真实浏览器本地页、真实平台登录、真实草稿、真实发送分别报告，不能互相替代。平台集合的既有选择不构成账号操作授权。
6. 用户要求测试尽量最短、最小、串行、立即释放资源；不为更稳重复批量跑模型、不并发加载大模型。本轮交叉审核不调用外部模型、不下载模型。

## 仍保留的候选与边界

- WIL118：a59fe1f，wil96/original；model_runtime.py、model_installer.py、runtime_health.py、visual_semantics.py及原包测试保持HOLD，不恢复写入或集成。
- WIL119：eb7b5a7，insert-backend-splice/original；quality_checks.py及专项测试保持HOLD。
- WIL121/WIL97：4302197，wil97/original；AudioTimelineEditor.tsx及原包关联范围保持HOLD。
- WIL110/112/116：5fcb8ac，agent-recall-memory/original，按上文成员可见性HOLD执行。除下文明确授予 workspace3 的目录检查局部例外外，不得借其他issue改动所保留文件；可见性语义及候选仍HOLD。
- 旧候选报告位于external-agents/review-20260929/；只按具体问题读取，旧workspace编号不是当前执行者身份。
- 真实可见验收沿DESIGN §5.10 workspace-dual-v4；模型配置、mock、结构分析和成功保存记录不等于真实制作或产品验收。保留现有exact端点，不自行退役外部消费者。
- 只释放本次明确归属的进程，旧/tmp/wil122-verify、/tmp/wil90-demo及历史证据不删除。GUI及重型验证串行，轻量独立准备可并行。

## 当前批次：收口与已证实缺口修复（2026-10-01）

用户已批准上一轮反馈后的安排。本批替代旧派工；产品基线仍为4d3a4ed，控制文档以本文件和当前PRD/auth为准。所有执行者不改Linear、不push、不派子代理、不使用真实库/账号/模型。保留旧候选及用户改动，不reset。派工消息给出关联issue编号。

| 执行者 | 任务和独占写入范围 | 结果、验收和退出 |
| --- | --- | --- |
| workspace2 / 原13 | P1 WIL143，候选261c4f8。继续独占 CreationTaskWorkspace.tsx、CreationTaskTopicWorkspace.tsx、CreationTaskWorkspace.module.css及现有两组件测试；环境已恢复，现在独占GUI；允许复用合法隔离样本，只验证真实产品页面。 | 普通产品页面核九阶段、人物口型入口、纯素材跳过、缺能力提示、切任务不串上下文和160恢复入口。复用67项证据不全量重跑；不调用模型，隔离数据可用但不可新造模拟产品页。局部真实失败在原白名单修复；交候选/截图/事实来源，结束退出自有进程并明确释放GUI。 |
| workspace6 / 原20 | P1 WIL145，只改独立/tmp/wil145-*夹具及原报告，产品只读。 | 修复已确认4秒音频被声明8秒的夹具不一致；先ffprobe核实所有被本场景引用的媒体与裁剪范围，只修自己的副本，不改历史原样本。夹具已修正且普通页面旅程PASS，GUI已释放；本包冻结等待主控收口，不重复部分取消→确认→撤销→重做，复用后端证据。此次不把可纠正夹具错误当产品失败终止；同一错误修正一次仍失败时报告具体原因。 |
| workspace3 / 原19 | P1 目录不可写修复，关联WIL123。独占 apps/api/app/production.py仅_material_library_status与bind_material_ingestion目录检查段；schemas.py仅目录/输出目录状态字段；main.py仅目录绑定/修复提示和输出目录状态段；apps/web/src/lib/onboarding.ts及上述已有专项测试；apps/desktop/scripts/lifecycle-smoke.js仅遗漏device-identity.js夹具复制修复。 | 这是用户批准后的目录检查局部HOLD例外，绝不改可见性/身份/素材业务。先读PRD首启、desktop README。只修真实不可写缺陷和无依据“可写”文案，所有新增状态同步实际消费者类型，若需额外消费者文件先报精确路径。隔离目录可写→不可写→恢复可写，验证拒绝错误绑定、提示和恢复；不另造onboarding、不重测已过身份/缺盘全套。GUI未授。 |
| workspace5 / 原15 | P1 五人上限与并发占位，关联WIL124。独占 apps/api/app/local_members.py及现有成员专项测试；不写database_migrations.py、main.py或前端。 | 读PRD1.1/1.2及auth9：主账号计入总五人，不能只统计role=member；add_member/join共用同一事务保护，失败不半写；1→5、第6拒绝、并发最后名额和既有owner保留约束最小验证。不在本包新增邀请表/停用语义，不更改身份认证，不用全局BEGIN影响无关读。候选92f824e已审并集成为336fb57，集成专项30项通过；本包写权释放。邀请生命周期待独立边界同步后由同一owner扩包。 |
| workspace1 / 原18 | P1 WIL124与110/112/116后续接线边界，只读产品；不再重复恢复条件审计。更新原WIL116/resume-review.md的下一包落点。 | 已决定新建private/存量shared现已写入PRD1.2，错误旧派单不再有效。只给出一个可交给workspace5的本地邀请撤销/幂等/停用/设备约束最小接口与文件清单，依据auth9保留停用后旧token失效、原归属保留；不能判错设备N/A，不能自动激活Supabase或扩展登录方案。有产品事实缺失只列一个具体决定及选项；最多完成此有限准备即交回。可见性候选仍禁止合入和写入。 |
| workspace4 / 原14 | P2 WIL154排队，产品只读，保留已有封面报告与合法短样本，不再重复调查或测试。 | 等143审核集成、workspace2明确释放前端写权后，由主控转交CreationTaskWorkspace与封面API/schema/渲染白名单；本轮main.py/schema正由workspace3独占，也须其释放。目标仅既有中部/底部标题构图，不做自由拖拽或模板系统。收到转交前不施工。 |

GUI当前仅workspace2；workspace6已交普通页面PASS及释放证据。workspace2复用 bd25/apps/api/.venv/bin/python 和合法隔离任务，禁止写真实库或为演示修改产品事实。无需再为准备隔离数据请求批准。随后按候选安排123/154。任何线程的“准备完成”不是自动获得GUI。主控收到释放证据后更新合同并发送转交。轻量隔离验证并行，媒体/GUI串行。不重复已通过未变化的测试。

旧工作包WIL123/124已交失败报告，不能标为功能通过；新修复候选独立审核后再恢复剩余验收。WIL154为缺实现排队；新父项WIL183仅改变归属，不代表封面已通过。交付含候选SHA、实际文件、最小验证、未验证项、截图及资源释放；主控审查集成，不以派工代替产品完成。
