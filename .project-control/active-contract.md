# 0.11 桌面闭环执行合同

## 当前授权与主控

ACTIVE。2026-09-29 19:12（Asia/Shanghai）完成交接登记：唯一主控为 Codex 聊天 `01a0ecd6-dc91-7500-8f03-45e2dbde4820`。用户新建主控聊天接续项目；cmux Claude 已书面确认停止全局派单、集成及共享合同写入，且没有更晚的禁止交接指令。原 Codex 聊天也停止派单和集成。

- 交接回执：`/Users/Zhuanz/Documents/Codex/2026-09-24/volumes-dujiangyan-vm-ai-repositories-bd25/external-agents/controller-handoff-20260929-response.md`。回执作交接证据，不定义产品规则。
- 主控负责规划、共享文档、派工、审核、集成与开发分支推送，不编写产品代码。不派内部子智能体；继续使用既有可见 cmux 执行者，不更改模型配置。
- 集成目录 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`，分支 `codex/0.11-development`。项目保存入口 `/Users/Zhuanz/Documents/AI视频日记` 指向另一旧 checkout，不在那里开展本轮集成。基线逐包给出，不用旧 SHA 回退工作树。
- 开发分支推送授权保留；main 合并、真实平台发送/发布、新增付费调用、签名、公证、真实用户库迁移、覆盖已安装版本不在授权内。测试只用隔离数据，不打开 `~/Movies/AI视频日记/agent.sqlite3`。
- 保护未跟踪的 `.project-control/0.11-bug-backlog.md`、`.project-control/handoff.md`、`work/integration-011-tmp/` 和各工作树的用户改动；不得 reset、清理或覆盖。
- 本文件只保存当前约束和文件归属。已结束批次、测试计数及旧主控登记从活动入口退役，历史由 Git、Linear 和原始交付报告追溯，不重新激活旧 Codex 线程。

## 权威和保留的暂停

长期规则从 [文档路由](../docs/README.md) 逐步读取；完整需求追踪见 [开发台账](0.11-development-plan.md)。0.11 桌面与 0.12 手机分开，追踪总表不是本轮全部必交付。

1. **0.11 Linear 整体重构已批准**：用户最新明确要求按0.11需求重构难懂、过粗的issue，以便多Agent协同。允许主控实际改写标题和描述、重排父子/关联/阻塞、补必要工作包及视图；优先复用现有ID，先保存原始内容及需求索引，保留历史交付和验收记录。旧“仅试点/先审迁移方案”的限制已被本次具体授权替代。本次只重构任务管理，不自动授权产品施工、解除既有HOLD、平台动作或真实数据迁移。Linear仅主控写入。
2. **成员可见性 HOLD**：WIL-110 / WIL-112 / WIL-116 及相关施工保持暂停，不因主控切换或命名整理恢复。候选 `5fcb8ac` 保留、不合入；`sources.py`、`source_repository.py`、`production.py` 和相关可见性迁移/测试不转派。用户已决定新建 private / 存量 shared，旧派单中新建 shared 的文字失效；本次Linear描述已纠正。恢复仍须完成相关权威和具体候选同步、执行者重新绑定，并满足原明确HOLD条件；不重复询问已经确定的默认值。
3. 本地多成员沿用户指定的本地 SQLite 方案核验；既有 Supabase 路径不删除、不自动激活。当前身份绕过面和读写授权缺口不得表述为安全隔离已完成；也不得借交接自行重设计身份架构。安全稳定规则仍见 [认证边界](../docs/auth-workspace-boundary.md)。
4. scheduled 生产运行仍关闭。WIL-92 是只读施工计划，不授予 main.py 写权。启动互斥阻断不等于准入门，OwnerRegistration、CPU预算、真实重试授权和端到端释放须分别核实。不得因接口/测试存在宣称 scheduled 已可用。
5. 真实发布不排期、不执行。发布授权和草稿的本地 mock、真实浏览器本地页、真实平台登录、真实草稿、真实发送分别报告，不能互相替代。平台集合的既有选择不构成账号操作授权。
6. 用户要求测试尽量最短、最小、串行、立即释放资源；不为更稳重复批量跑模型、不并发加载大模型。本轮交叉审核不调用外部模型、不下载模型。

## 仍保留的候选与边界

- WIL118：a59fe1f，wil96/original；model_runtime.py、model_installer.py、runtime_health.py、visual_semantics.py及原包测试保持HOLD，不恢复写入或集成。
- WIL119：eb7b5a7，insert-backend-splice/original；quality_checks.py及专项测试保持HOLD。
- WIL121/WIL97：4302197，wil97/original；AudioTimelineEditor.tsx及原包关联范围保持HOLD。
- WIL110/112/116：5fcb8ac，agent-recall-memory/original，按上文成员可见性HOLD执行。准备工作不得借其他issue改动所保留文件。
- 旧候选报告位于external-agents/review-20260929/；只按具体问题读取，旧workspace编号不是当前执行者身份。
- 真实可见验收沿DESIGN §5.10 workspace-dual-v4；模型配置、mock、结构分析和成功保存记录不等于真实制作或产品验收。保留现有exact端点，不自行退役外部消费者。
- 只释放本次明确归属的进程，旧/tmp/wil122-verify、/tmp/wil90-demo及历史证据不删除。GUI及重型验证串行，轻量独立准备可并行。

## 下一批：先收口，再补齐明确功能（2026-09-30）

用户授权加速剩余工作。本节是当前唯一派工入口；已结束派单从活动文档退役，由Git和Linear追溯。共同候选为50209dd9b356621d43e902afa20840ebb60e449b；长久规则仍由docs/README.md路由。先核身份、脏树和旧服务；干净、无运行消费者才可在既有树新建codex分支指向此提交，不重置旧分支，不删stash。当前不新开窗口、不改模型，所有具名HOLD保持。

| 执行者（当前/原） | 任务与边界 | 交付与验收 |
| --- | --- | --- |
| workspace4 / 原14 | WIL109立即取得独占GUI时段；复用已修好的8秒隔离夹具，源码无新改动。 | 健康检查后完成箭头名称/对象切换的三尺寸可见核验，交报告并释放进程；不重复专项。 |
| workspace6 / 原20 | WIL181接线已集成；从bdc6024新建干净分支继续WIL144唯一剩余C1b修正，独占main.py中ChangeCrop的describe_clip文案，可新增test_crop_preview_description.py。 | fit模式说明完整显示、位置保留但不生效；fill仍显示坐标。最小断言后交SHA。不改其他编译/确认/保存。GUI排4后，由主控另发开始。 |
| workspace2 / 原13 | WIL144仅修test_montage_pipeline.py既有画幅回归断言，仍无main.py写权。 | 用已有支持的“改成竖版”，可用状态下必须200且ChangeCanvas，不能非200即通过；只跑该用例交提交。其他文件暂停。 |
| workspace3 / 原19 | WIL100前置串条修复fa7d169已集成bdc6024；产品写权暂退役，只做可见核验准备。 | 准备隔离素材A/B刷新消失及重选入口；不启动GUI，排4后。整条说明/批次背景仍未实施，production.py HOLD不变。 |
| workspace5 / 原15 | 144最终交叉报告已采纳C1b；无产品写权。 | 本轮审核已交付，保持空闲；收到6修复SHA后只核C1b，不扩大新词表/新需求，不轮询或跑测试。 |
| workspace1 / 原18 | WIL37普通原生保存联验已交；GUI已释放。 | 主控核验收口；保存报告和隔离证据，不重跑。无产品写权，保持空闲。 |

准备包仅可写原批次对应issue目录的next-wave.md及独立/tmp夹具；它们不是实施完成，不据此关闭issue。实施者先REBASE_ACK再写，主控审查准备结果后直接授予不冲突的精确写权，不再向用户询问是否继续。GUI现独占分配workspace4的WIL109，workspace1已释放；须隔离服务健康后才开始交互，其他线程不得启动GUI。禁模型、用户库、发布、全量测试与依赖安装。连续两次同根失败改方法或换人，不反复补环境。报告须区分产品缺陷、环境故障、证据缺口、非阻断项，并给操作、预期、实际和下一步。
