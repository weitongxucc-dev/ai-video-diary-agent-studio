# 曜核0.11主控交接：2026-10-04

本文件是用户切换账号时的一次交接证据，不替代实时Linear/Git。接班先读[活动合同](active-contract.md)顶部、本文和实时Linear；不要重扫整个仓库或历史输出。

## 唯一主控与本次授权

- 用户明确指定 **cmux workspace9 / pane19 / surface19** 接替原Codex主控 `01a1019f-5dfa-7f22-9bb8-cbb2a49a9140`。
- 新主控 workspace UUID：`5CA420B8-1E90-4F4E-BD28-8A6FD2A9A636`；pane UUID：`D7B149E2-D26B-4E71-9D2F-7CA08FA67100`；surface UUID：`D71D58DD-7EDB-4DBA-8E0E-FFD3CB01B9F5`。这是当前可见会话的确切身份；不是假造Codex聊天ID。
- 原主控只完成本次检查点推送、交接文件/合同/Linear更新和最终发送；最终 `AUTHORITY_CHANGED` 后停止派工、审核和写入。新主控须确认收到准确交接SHA再恢复执行者，不同时两人指挥。
- 本次用户明确要求GitHub分支和main及时更新：仅把已审核通过的准确组合代码同步main；未通过/未验候选推独立WIP分支。**这不是以后自动合PR授权**，不发布、打包、签名或激活scheduled。
- 用户已明确批准119→108、真实成员交互许可159、知识跨workspace授权110，**不再重复征求同意**。112/116/110/104/126、首页98/125具名局部恢复仍有效；行业分类待定义。其他118/121/97/96等HOLD保留。
- 交接暂停是防止两主控/文件并写，不撤回这些实施授权；新主控核准确候选后可恢复。所有执行者已停写，无自有运行服务。1/5旧通道故障继续停用。

## 工作方式：执行，不只巡检

> 以一个issue的完整目标为单位：先完成已确认的全部实现，再集中一次最小相关测试和必要真实验收；只有定位真实失败才提前定向复现，未受改动影响的通过证据复用。不要每做小点就测试/报告/等指令，不做无依据防御性编程或无意义、重复、无关全量测试。

以Linear项目 `05adcf10-cdb3-402d-b718-05e8bbe4008f` 全部issue作为清单，读完分页。In Review优先收交付；In Progress核进展/真实阻塞；Todo派给可用线程；Backlog按当前源码核依赖，已授权且就绪的安排，缺决定一次给具体方案。父子实际验收同步，PR合入/CI不能当全部验收。
主控只写合同/共享权威/Linear/Git集成、push和PR；不写产品代码，不派内部代理；用现有可见cmux。文件一写者，稳定阶段提交即可移交，不等整卡GUI关闭。上下文失效或拥挤拖慢，可直接换新的可见执行会话，无需再问用户；不改模型设置。

禁止真实用户DB/配置/密钥、未授权推理或模型下载、收费、平台账号/发布/Release。GUI仅用户桌面Chrome；旧策略拒绝不重试、换工具/通道绕过。GUI相关分项仍UNVERIFIED。197字幕用户已跳过，不再催问。定时automation和scheduled生产均OFF，不恢复。
7号历史裸import可能触发真实SQLite DDL的事故仍UNVERIFIED，记录保留；不得借此再次打开真实库复核或抹掉。

## Git与恢复入口

仓库 `https://github.com/weitongxucc-dev/ai-video-diary-agent-studio`，控制目录 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`。
- 原远程main：`33cc48502b92d3646d603a8093b0f17ee3617851`。
- 本次可同步main的准确已审核代码：`0c1c5559644e50dd703eb64c5fa1ac63f9e27803`；其完整代码树与 `d1f8fc16424527938dfe0d125f37820e9df5c172` **相同**，已实时核。CI `37199772798` SUCCESS；d1 CI `37184253770` SUCCESS。不重复全量测试。
- main另携带本次交接合同/本文及一行已批准领域澄清；精确最终main/handoff SHA以GitHub远程和最终交接消息为准，不能使用文件自引用SHA。
- `codex/controller-handoff-20261004` 为本次main同步候选；`codex/controller-state-20261004` 保存bd25用户脏权威文档/控制文件的恢复快照，**不是产品集成候选**，不整体merge进main。
- bd25仍在legacy `codex/0.11-development` / `660f519b5ac62167c88bb96f0b80c1d6e30c55d3`，用户dirty权威文档及 `work/integration-011-tmp/` 保留，未reset/clean。不要把这个树、当前shell的original旧checkout或 `/Users/Zhuanz/Documents/AI视频日记` 当当前main。
- 新main干净同步目录：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/controller-handoff-20261004`，仅主控Git/文档交接使用。产品继续各自独立issue树。
- 三个WIP已推GitHub，没有PR、功能完成或CI通过含义，不合main：
  - `codex/wil119-quality-detection` → `8a1b46a7be51511deeba38301bd5c74b04ef0721`。
  - `codex/wil159-member-permits` → `403778778f2804fbe16a0faf24accaf3ce129409`。
  - `codex/wil110-knowledge-grants` → `ac318d2e3c7cc4a17fba9225d09a18e2d472d024`，含原领域澄清 `091784955f6bc4ae1f79accf1443a05a1355f64b`。
- GitHub短时一次SSL_ERROR_SYSCALL已遇到，后续push成功；这是网络错误，不是工具策略拒绝。无需换通道绕过。远程必须ls-remote核实，不能只看local commit。
- PR85/86及84/83已外部合入各自feature base；不是旧main，旧主控未执行合PR。81/82最新元数据头已变化，接班只读核具体Git/CI/树，不把旧头当新头。最新main同步仅用上述准确通过组合。

## 现有执行线程与下一动作

| 线程 | issue / 当前检查点 | 独立树与下一结果 |
| --- | --- | --- |
| 2：workspace2 / surface14 | 119；8a1b46a审核退回 | `/Volumes/Dujiangyan-VM/AI视频日记/repositories/wil119-quality-detection`，`codex/wil119-quality-detection`；只写quality_checks.py和直属专项。修完下述真实算法缺陷、定向验证，交稳定结果契约给4。 |
| 3：workspace3 / surface11 | 159；4037787 WIP，未验 | `/Volumes/Dujiangyan-VM/AI视频日记/repositories/wil159-member-permits`；main/device_runtime/device_scheduler/creation_execution、直属member_permits/专项。完成单一真实运行时接线、心跳/本人队列、恢复重试/取消释放。main稳定提交即停写交6，不等五人GUI。此旧上下文刚压缩且又只剩4%，优先考虑新会话接替，保留候选。 |
| 6：workspace6 / surface10 | 110；ac318d2 WIP，未验 | `/Volumes/Dujiangyan-VM/AI视频日记/repositories/wil110-knowledge-grants`；source_repository/sources/knowledge_files、直属grant路由/迁移/专项、schemas、KnowledgeWorkspace/UI/CSS/专项、Web api/types直接消费者。先解决真实目标空间事实源；main在3稳定移交后接。 |
| 4：workspace4 / surface12 | 108已批准，未建新树/未施工 | 取得119真实稳定契约后由新主控建独立树，承接完整成片检查触发/持久状态/结果/片段定位/旧版本失效。production.py和直属质量模块届时归4；main/schema/client顺序3→6→4，不并写。 |
| 7：workspace7 / surface17 | 168只读缺口已收，停写 | 不自动识别同卷重连、不造备份；其他新施工未授权。 |
| 1：workspace1 / surface16；5：workspace5 / surface13 | 旧执行通道故障，停用 | 不发旧任务或重试错误通道；必要时新主控建可见接替线程。 |

执行者完整旧UUID见[原执行者交接](executor-handoff.md)，当前surface如上。cmux CLI：
`/Applications/cmux.app/Contents/Resources/bin/cmux capture-pane --workspace workspace:2 --surface surface:14 --lines 24`
发送 `send` 后用小写 `send-key ... enter`，再capture核确实收到并开工。运行中Codex可能停在粘贴确认/队列，只有观察到未提交才补一次；明确queued不重复。必要停工可escape；不能只把工具OK当发送成功。shell用login:false。

### 119必须先修的事实缺陷（已向2发送并确认收到）

10项专项1.87s通过只证明原样本，不足以算法通过，**不Done/不放108**。
1. `_decoded_stream_end_seconds` 仍读packet元数据且取最后遍历包，不是解码frame/sample末端，B帧顺序不是最大PTS，缺duration还fallback到PTS。用真实视频frame PTS+frame duration、音频frame PTS+nb_samples/sample_rate取最大，缺必要时长如实unknown。2已短实测：视频1.96+0.04=2；音频1.984+1024/48000≈2.005，结果尚未写回。
2. 连续两个0.5秒窗Peak=0仍不能证明削平，正常满幅正弦每窗同样触顶。当前0.999正弦“削波”fixture是错误判据；改真实解码饱和/削平采样证据，并用满幅未削平负向和真正削平正向。2已有PCM计数探索clean=2、0.999sine=402、硬削平=6206 /96256，仅为探索，**未形成有效最终判据/验证**。
3. 别用容器时长差冒充语义截断；只按所定义真实流尾部缺失证据。起点偏移不能声称全程AV同步。所有定位时间归一成片时间轴，未知/失败不clean。
旧eb7b5a7整体HOLD不复活。只针对这两处受影响专项重验，其他真实通过证据复用。

### 159剩余接线与边界

4037787仅main局部改动+member_permits模块+session归属检查。语法检查/文件保存，不是功能验证。永久None的假资源/进程探针已按root退回要求删除。
未完成：creation_job/material_asset共用单一运行时、锁、队列与驱动；三个真实路由；恢复/重试成员有效性；驱动生命周期；专项测试未写未跑。scheduled配置OFF只禁止定时触发，不阻止本次已批准交互许可。
真实预算输入缺口：产品没有HostBudget全部字段配置来源（仅tests有），不得猜预算、放宽门禁或用假探针冒充完成；真实可配置路径要完整，缺标定明确503/UNVERIFIED，实际预算决策/标定如有必要一次提出具体方案。
复用HostResourceProbe/HostProcessProbe/现有CreationJobOwnerVerifier/UnitExecutor，不新造第二调度系统；真实LocalMemberService核成员，持久member/workspace但不存原token。恢复、重试、start均核仍有效，不伪造VerifiedSession。
root待审点：当前member existence本身不是任意workspace成员资格；LocalMember只证明legacy local，不能把混合Bearer/local-token或自填workspace拼成跨空间许可。每次重新核可信身份/空间，明确拒绝未绑定组合。
真实负载/模型和五人GUI缺条件继续UNVERIFIED，不做模型加载；旧4个mutex证据复用。

### 110授权差额与实际缺口

ac318d2已保存grant/revoke记录、统一读取/固定版本判据、APIRouter契约、知识页/client及专项文件；**只diff --check通过，功能测试未跑，GUI未验**。main没有挂router或改消费者，仍未完成。
真实同设备目标事实源缺：LocalMember全部local，current_workspace只认已验证principal/legacy；已有知识行的workspace_id不是同设备授权证明。不能扫描知识表造targets、接受客户端伪造空间，或新增假会员/VerifiedSession。
现有device_accounts.py及device_account_invites迁移有Supabase设备/空间关系，但路径未授权激活、真实凭据不读。接班先定向核真实可复用关系；如本地缺映射需新决定，提出最小具体方案，不默默另造身份系统。
grant只知识、保留原空间/owner/固定版本，管理员不默认读私人。撤销停止新列表/详情/搜索/助手/固定版证据/新引用，既有任务追溯保留但后续使用仍核有效授权。
main消费者取得可读item后用item原workspace读visibility/version/evidence；grantee身份仍是target，禁止伪造原owner/原空间、写可见性或转授。
本树**不存在agent_bridge.py**，不新增它；助手沿既有HTTP入口。原合同中的该路径只是误列，不获得扩大框架权限。knowledge_files仅直接证据消费者需要时改。

## 已通过证据：接班复用

- 98 Done，API6项真实成员HTTP、2294321、PR85；125产品19+2相关检查，d1、PR86，最新CI三项PASS，但GUI三个尺寸/真实跳转及行业待定义不算通过。Linear125现在In Progress，以实时卡为准。
- 110同空间读取既有421050d/a8219/PR84和CI37183870912 PASS；上传owner、private URL和可信身份专项复用；**新跨空间grant不在既有PASS范围**。
- 126 52bd545/PR81既有20项、桌面复制确认/补扫/重开证据复用；普通GUI未验。104 2f6cc42/PR82既有5+8定位专项复用，完整权限依110；新remote元数据头接班再核。
- 116 a9cb959/PR83三CI通过；旧042产品UI未变。任务/模板PR78通过。实时Linear116已Done但仍有受阻label，**不要把自动状态当补齐GUI证据**，接班核其描述判据。
- 112 a297ec1/PR80三CI和17UI专项复用；160 2b9ae3b/PR77三CI通过，GUI未验。
- 195/196/132普通GUI已有通过证据，Done，不重建重测；194也Done。199/197/127等GUI/真实声音缺口保留，197用户跳过字幕不催问。
- 157实现链已存在，模板新任务仍缺GUI；168缺盘PASS复用，异路径重连/隔离备份无产品入口，方案未批准；165/169相关包/Keychain/录音/模型验收不可冒充完成。
- 历史/tmp ready.json与release证据保留，不删除、重复启动或遍历output。无自有新运行服务。

## Linear完整未完成清单（交接时快照）

一次完整250上限查询返回188项、hasNextPage=false：Done95、In Review14、In Progress21、Backlog54、Todo0、Canceled3、Duplicate1。该快照供断档恢复，**后续必须用实时Linear，不维护第二套完成度**。免费issue限额拒绝新增，复用159/110保存本次实施线，保留历史证据；不付费升级、不因无法新增卡停工。

| issue | 实际状态 | 标题 |
| --- | --- | --- |
| [WIL-108](https://linear.app/wiliiam/issue/WIL-108/在成片检查阶段展示问题并定位到对应片段) | Backlog | 在成片检查阶段展示问题并定位到对应片段 |
| [WIL-110](https://linear.app/wiliiam/issue/WIL-110/让列表详情和助手都遵守同一可见范围) | In Progress | 让列表、详情和助手都遵守同一可见范围 |
| [WIL-159](https://linear.app/wiliiam/issue/WIL-159/验收多人共用设备时的公平排队和资源释放) | In Progress | 验收多人共用设备时的公平排队和资源释放 |
| [WIL-119](https://linear.app/wiliiam/issue/WIL-119/检测成片的黑帧截断削波和时间戳异常) | In Progress | 检测成片的黑帧、截断、削波和时间戳异常 |
| [WIL-125](https://linear.app/wiliiam/issue/WIL-125/从首页图表和待办进入对应的真实内容) | In Progress | 从首页图表和待办进入对应的真实内容 |
| [WIL-33](https://linear.app/wiliiam/issue/WIL-33/把满意的制作方式存为模板并创建新任务) | In Progress | 把满意的制作方式存为模板并创建新任务 |
| [WIL-157](https://linear.app/wiliiam/issue/WIL-157/选择模板和新主题生成独立的新任务) | In Review | 选择模板和新主题生成独立的新任务 |
| [WIL-168](https://linear.app/wiliiam/issue/WIL-168/验收缺盘源文件重连和隔离备份恢复) | Backlog | 验收缺盘、源文件重连和隔离备份恢复 |
| [WIL-112](https://linear.app/wiliiam/issue/WIL-112/让知识资料保留可见性并兼容存量记录) | In Review | 让知识资料保留可见性并兼容存量记录 |
| [WIL-11](https://linear.app/wiliiam/issue/WIL-11/从首页看懂设备运行和自己的待办) | In Progress | 从首页看懂设备运行和自己的待办 |
| [WIL-104](https://linear.app/wiliiam/issue/WIL-104/让文案引用只来自有权读取且可回查的知识) | In Review | 让文案引用只来自有权读取且可回查的知识 |
| [WIL-126](https://linear.app/wiliiam/issue/WIL-126/外部素材经复制完成确认后入库并支持重启补扫) | In Review | 外部素材经复制完成确认后入库并支持重启补扫 |
| [WIL-160](https://linear.app/wiliiam/issue/WIL-160/重开应用后继续查看同一任务的进度和失败原因) | In Review | 重开应用后继续查看同一任务的进度和失败原因 |
| [WIL-12](https://linear.app/wiliiam/issue/WIL-12/接收完整素材并继续后台分析) | In Progress | 接收完整素材并继续后台分析 |
| [WIL-19](https://linear.app/wiliiam/issue/WIL-19/用有出处的知识写文案并复用旧文案) | In Progress | 用有出处的知识写文案并复用旧文案 |
| [WIL-9](https://linear.app/wiliiam/issue/WIL-9/控制内容私有共享和撤销共享) | In Progress | 控制内容私有、共享和撤销共享 |
| [WIL-165](https://linear.app/wiliiam/issue/WIL-165/隔离工作区文字模型配置并安全保存密钥) | In Review | 隔离工作区文字模型配置并安全保存密钥 |
| [WIL-36](https://linear.app/wiliiam/issue/WIL-36/看懂任务进度并在重开后继续) | In Progress | 看懂任务进度并在重开后继续 |
| [WIL-105](https://linear.app/wiliiam/issue/WIL-105/在知识页查看资料详情主题关系和冲突来源) | In Review | 在知识页查看资料详情、主题关系和冲突来源 |
| [WIL-135](https://linear.app/wiliiam/issue/WIL-135/发现相互矛盾的知识参数并保留人工修正) | In Review | 发现相互矛盾的知识参数并保留人工修正 |
| [WIL-20](https://linear.app/wiliiam/issue/WIL-20/在知识库查看资料关系参数和冲突) | In Progress | 在知识库查看资料关系、参数和冲突 |
| [WIL-18](https://linear.app/wiliiam/issue/WIL-18/把知识文件和网页解析成可回查资料) | In Progress | 把知识文件和网页解析成可回查资料 |
| [WIL-142](https://linear.app/wiliiam/issue/WIL-142/验收文案确认后连续制作取消和失败恢复) | Backlog | 验收文案确认后连续制作、取消和失败恢复 |
| [WIL-169](https://linear.app/wiliiam/issue/WIL-169/用同一候选桌面测试包验证启动和真实媒体输出) | Backlog | 用同一候选桌面测试包验证启动和真实媒体输出 |
| [WIL-16](https://linear.app/wiliiam/issue/WIL-16/搜索素材并打开可定位的片段详情) | In Progress | 搜索素材并打开可定位的片段详情 |
| [WIL-138](https://linear.app/wiliiam/issue/WIL-138/纠正一句的发音并只重做受影响的配音) | Backlog | 纠正一句的发音并只重做受影响的配音 |
| [WIL-127](https://linear.app/wiliiam/issue/WIL-127/把上传时口述的说明保存给对应素材) | In Review | 把上传时口述的说明保存给对应素材 |
| [WIL-167](https://linear.app/wiliiam/issue/WIL-167/验收版本冲突局部重算和未修改产物保留) | Backlog | 验收版本冲突、局部重算和未修改产物保留 |
| [WIL-101](https://linear.app/wiliiam/issue/WIL-101/让不要自动选用对自动选片生效) | In Review | 让“不要自动选用”对自动选片生效 |
| [WIL-199](https://linear.app/wiliiam/issue/WIL-199/从首页标签词云进入对应素材) | In Review | 从首页标签词云进入对应素材 |
| [WIL-128](https://linear.app/wiliiam/issue/WIL-128/为素材关联业务对象并保留批量背景) | In Review | 为素材关联业务对象并保留批量背景 |
| [WIL-100](https://linear.app/wiliiam/issue/WIL-100/为一批上传素材保存各自的文字说明) | In Review | 为一批上传素材保存各自的文字说明 |
| [WIL-197](https://linear.app/wiliiam/issue/WIL-197/查看当前任务的历史时间线版本) | In Review | 查看当前任务的历史时间线版本 |
| [WIL-15](https://linear.app/wiliiam/issue/WIL-15/纠正素材事实并控制自动选片) | In Progress | 纠正素材事实并控制自动选片 |
| [WIL-21](https://linear.app/wiliiam/issue/WIL-21/建立可复用的人物形象和身体参考) | Backlog | 建立可复用的人物形象和身体参考 |
| [WIL-13](https://linear.app/wiliiam/issue/WIL-13/为上传素材补充说明和业务背景) | In Progress | 为上传素材补充说明和业务背景 |
| [WIL-28](https://linear.app/wiliiam/issue/WIL-28/让对话修改和手动编辑得到同一种结果) | In Progress | 让对话修改和手动编辑得到同一种结果 |
| [WIL-10](https://linear.app/wiliiam/issue/WIL-10/在三栏工作台中调整布局并编辑选中内容) | In Progress | 在三栏工作台中调整布局并编辑选中内容 |
| [WIL-56](https://linear.app/wiliiam/issue/WIL-56/核对工作台中文控件密度和不可用状态) | Backlog | 核对工作台中文、控件密度和不可用状态 |
| [WIL-29](https://linear.app/wiliiam/issue/WIL-29/精确调整镜头顺序时长和画面) | In Progress | 精确调整镜头顺序、时长和画面 |
| [WIL-27](https://linear.app/wiliiam/issue/WIL-27/在九个制作阶段之间查看和继续同一任务) | In Progress | 在九个制作阶段之间查看和继续同一任务 |
| [WIL-8](https://linear.app/wiliiam/issue/WIL-8/邀请成员加入并管理五人使用名额) | In Progress | 邀请成员加入并管理五人使用名额 |
| [WIL-7](https://linear.app/wiliiam/issue/WIL-7/首次启动后完成设备与工作目录检查) | In Progress | 首次启动后完成设备与工作目录检查 |
| [WIL-153](https://linear.app/wiliiam/issue/WIL-153/调整字幕样式并切换中英显示) | Backlog | 调整字幕样式并切换中英显示 |
| [WIL-150](https://linear.app/wiliiam/issue/WIL-150/验收音频导入剪移分割和锁定后的播放同步) | Backlog | 验收音频导入、剪移分割和锁定后的播放同步 |
| [WIL-123](https://linear.app/wiliiam/issue/WIL-123/验收首次启动设备绑定与工作目录修复) | Backlog | 验收首次启动、设备绑定与工作目录修复 |
| [WIL-41](https://linear.app/wiliiam/issue/WIL-41/保存任务版本并只重算受影响的内容) | Backlog | 保存任务版本并只重算受影响的内容 |
| [WIL-139](https://linear.app/wiliiam/issue/WIL-139/验收在当前视频录音重录试听并应用) | Backlog | 验收在当前视频录音、重录、试听并应用 |
| [WIL-31](https://linear.app/wiliiam/issue/WIL-31/修改字幕文字时间和显示样式) | Backlog | 修改字幕文字、时间和显示样式 |
| [WIL-32](https://linear.app/wiliiam/issue/WIL-32/调整画面颜色并进行轻美颜) | Backlog | 调整画面颜色并进行轻美颜 |
| [WIL-146](https://linear.app/wiliiam/issue/WIL-146/剪切移动和锁定镜头时保持关联内容同步) | Backlog | 剪切、移动和锁定镜头时保持关联内容同步 |
| [WIL-147](https://linear.app/wiliiam/issue/WIL-147/改变镜头速度或停顿且不误改人声) | Backlog | 改变镜头速度或停顿且不误改人声 |
| [WIL-124](https://linear.app/wiliiam/issue/WIL-124/验收邀请加入五人上限和成员停用) | Backlog | 验收邀请加入、五人上限和成员停用 |
| [WIL-184](https://linear.app/wiliiam/issue/WIL-184/检查成片问题并定位需要修复的片段) | Backlog | 检查成片问题并定位需要修复的片段 |
| [WIL-170](https://linear.app/wiliiam/issue/WIL-170/在同一候选上验收目标到保存的完整创作旅程) | Backlog | 在同一候选上验收目标到保存的完整创作旅程 |
| [WIL-166](https://linear.app/wiliiam/issue/WIL-166/按确认后的权益显示赠送用量和剩余额度) | Backlog | 按确认后的权益显示赠送用量和剩余额度 |
| [WIL-164](https://linear.app/wiliiam/issue/WIL-164/按时间查看自己或有权查看的发布数据) | Backlog | 按时间查看自己或有权查看的发布数据 |
| [WIL-163](https://linear.app/wiliiam/issue/WIL-163/取得已确认平台支持的发布指标并标明来源) | Backlog | 取得已确认平台支持的发布指标并标明来源 |
| [WIL-158](https://linear.app/wiliiam/issue/WIL-158/让定时计划产生候选任务并保留人工确认) | Backlog | 让定时计划产生候选任务并保留人工确认 |
| [WIL-156](https://linear.app/wiliiam/issue/WIL-156/验收轻美颜在多帧中保留身份和口型) | Backlog | 验收轻美颜在多帧中保留身份和口型 |
| [WIL-151](https://linear.app/wiliiam/issue/WIL-151/处理局部底噪并保留可恢复的原声) | Backlog | 处理局部底噪并保留可恢复的原声 |
| [WIL-141](https://linear.app/wiliiam/issue/WIL-141/修改一句声音后只重做对应人物口型) | Backlog | 修改一句声音后只重做对应人物口型 |
| [WIL-140](https://linear.app/wiliiam/issue/WIL-140/按六档出镜设置生成并核对实际人物比例) | Backlog | 按六档出镜设置生成并核对实际人物比例 |
| [WIL-137](https://linear.app/wiliiam/issue/WIL-137/用本人或已授权样本建立可试听的声音) | Backlog | 用本人或已授权样本建立可试听的声音 |
| [WIL-133](https://linear.app/wiliiam/issue/WIL-133/在确认授权后预览并保存去水印派生素材) | Backlog | 在确认授权后预览并保存去水印派生素材 |
| [WIL-130](https://linear.app/wiliiam/issue/WIL-130/让画面文字识别保留证据帧和时间位置) | Backlog | 让画面文字识别保留证据帧和时间位置 |
| [WIL-129](https://linear.app/wiliiam/issue/WIL-129/让转写说话人和静音标注定位到原片) | Backlog | 让转写、说话人和静音标注定位到原片 |
| [WIL-121](https://linear.app/wiliiam/issue/WIL-121/让分割后的音量曲线与实际导出音量一致) | Backlog | 让分割后的音量曲线与实际导出音量一致 |
| [WIL-118](https://linear.app/wiliiam/issue/WIL-118/模型文件或工作目录不可用时说明原因与修复方法) | Backlog | 模型文件或工作目录不可用时说明原因与修复方法 |
| [WIL-115](https://linear.app/wiliiam/issue/WIL-115/在发布后台填入文案和成片并保存草稿) | Backlog | 在发布后台填入文案和成片并保存草稿 |
| [WIL-114](https://linear.app/wiliiam/issue/WIL-114/经用户最终确认后发送成片并保存真实结果) | Backlog | 经用户最终确认后发送成片并保存真实结果 |
| [WIL-113](https://linear.app/wiliiam/issue/WIL-113/显示发布账号的真实授权状态和失效原因) | Backlog | 显示发布账号的真实授权状态和失效原因 |
| [WIL-97](https://linear.app/wiliiam/issue/WIL-97/让关键点淡入淡出和音乐压低作用到实际混音) | Backlog | 让关键点、淡入淡出和音乐压低作用到实际混音 |
| [WIL-96](https://linear.app/wiliiam/issue/WIL-96/让素材画面描述附带可回看的时间证据) | Backlog | 让素材画面描述附带可回看的时间证据 |
| [WIL-42](https://linear.app/wiliiam/issue/WIL-42/交付可启动可恢复的桌面测试包) | Backlog | 交付可启动、可恢复的桌面测试包 |
| [WIL-40](https://linear.app/wiliiam/issue/WIL-40/配置模型服务并看懂用量与失败原因) | Backlog | 配置模型服务并看懂用量与失败原因 |
| [WIL-39](https://linear.app/wiliiam/issue/WIL-39/查看自己或全体成员的真实发布数据) | Backlog | 查看自己或全体成员的真实发布数据 |
| [WIL-38](https://linear.app/wiliiam/issue/WIL-38/连接发布账号并在确认后发送成片) | Backlog | 连接发布账号并在确认后发送成片 |
| [WIL-35](https://linear.app/wiliiam/issue/WIL-35/多人共用设备时保持响应并公平排队) | Backlog | 多人共用设备时保持响应并公平排队 |
| [WIL-34](https://linear.app/wiliiam/issue/WIL-34/按时间安排任务且保留人工确认) | Backlog | 按时间安排任务且保留人工确认 |
| [WIL-30](https://linear.app/wiliiam/issue/WIL-30/调整多轨声音并让试听与成片一致) | Backlog | 调整多轨声音并让试听与成片一致 |
| [WIL-26](https://linear.app/wiliiam/issue/WIL-26/确认文案后连续制作并从失败处恢复) | Backlog | 确认文案后连续制作并从失败处恢复 |
| [WIL-25](https://linear.app/wiliiam/issue/WIL-25/让创作助手理解当前对象并提出可执行修改) | Backlog | 让创作助手理解当前对象并提出可执行修改 |
| [WIL-24](https://linear.app/wiliiam/issue/WIL-24/按指定出镜比例生成与声音同步的人物画面) | Backlog | 按指定出镜比例生成与声音同步的人物画面 |
| [WIL-23](https://linear.app/wiliiam/issue/WIL-23/录制自己的旁白并应用到当前视频) | Backlog | 录制自己的旁白并应用到当前视频 |
| [WIL-22](https://linear.app/wiliiam/issue/WIL-22/创建授权声音并校正发音) | Backlog | 创建授权声音并校正发音 |
| [WIL-17](https://linear.app/wiliiam/issue/WIL-17/从链接导入素材并保留来源) | Backlog | 从链接导入素材并保留来源 |
| [WIL-14](https://linear.app/wiliiam/issue/WIL-14/理解素材内容并定位到原始时间片段) | Backlog | 理解素材内容并定位到原始时间片段 |
| [WIL-5](https://linear.app/wiliiam/issue/WIL-5/完成一条从目标到成片的桌面创作流程) | Backlog | 完成一条从目标到成片的桌面创作流程 |

已Done标识（不意味着父项/整版Done）：WIL-116、WIL-98、WIL-103、WIL-134、WIL-131、WIL-132、WIL-194、WIL-196、WIL-195、WIL-198、WIL-106、WIL-107、WIL-191、WIL-182、WIL-171、WIL-193、WIL-152、WIL-192、WIL-136、WIL-102、WIL-180、WIL-188、WIL-155、WIL-148、WIL-190、WIL-189、WIL-176、WIL-185、WIL-149、WIL-145、WIL-183、WIL-154、WIL-187、WIL-186、WIL-143、WIL-181、WIL-179、WIL-178、WIL-177、WIL-175、WIL-173、WIL-172、WIL-99、WIL-162、WIL-161、WIL-92、WIL-144、WIL-122、WIL-109、WIL-95、WIL-94、WIL-93、WIL-91、WIL-90、WIL-89、WIL-88、WIL-87、WIL-86、WIL-85、WIL-84、WIL-83、WIL-82、WIL-81、WIL-80、WIL-79、WIL-78、WIL-77、WIL-76、WIL-75、WIL-74、WIL-73、WIL-72、WIL-71、WIL-70、WIL-69、WIL-68、WIL-67、WIL-66、WIL-65、WIL-64、WIL-63、WIL-62、WIL-61、WIL-57、WIL-60、WIL-59、WIL-58、WIL-55、WIL-54、WIL-53、WIL-52、WIL-51、WIL-50、WIL-49、WIL-37。
Canceled：WIL-174、WIL-120、WIL-111；Duplicate：WIL-117。

## 新主控第一轮动作

1. 核已收到准确远程main/handoff SHA、三个WIP远程头及执行者停写；不用旧合同/旧cwd开工。
2. 给2恢复119两处真实修复；3优先换新可见会话接159准确WIP继续单例/路由/身份/恢复；6先收同设备目标缺口最小方案并继续其余独立grant差额。明确路径、完成判据并核真正发送，不重复要求用户批准已确定实施范围。
3. 119真实契约达标后立即建108独立树交4；main按3稳定→6→4顺序移交，不将整卡GUI当文件硬依赖。
4. 收已完成issue候选、审diff、push单一issuePR并核真实Linear关联。新建PR应attach到主控聊天（若工具可用），不自动合PR。
5. 按全项目实时issue推进其他就绪已授权项；未批准模型/真实库/发布/HOLD不以赶进度为由放开。没有真实就绪工作不造ACK、重复报告或假空结果。

