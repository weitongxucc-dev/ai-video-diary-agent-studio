# 2026-10-23 主控接续

本文件是用户要求的必要交接，事实截至源码 `5d6adc0e`；后续状态以 Git、原任务、作业回执和实际运行为准，不维护第二套完成度。唯一所有权在[活动合同](../active-contract.md)。旧主控完成移交后停止派工；新主控收到“交接完成，可接续”消息后继续，不需用户再次批准。

## 1. 不变的任务与边界

用户已批准：复用已有能力，优先修事实口径、错误回执、真实版本依赖，打通 Pi 通用读写/脚本/命令自主工作与产物登记，再用同一原任务完成中英文各30秒脱壳机视频、持续局部修改、指定修改撤销及正常退出重启恢复。不要停在文档、派工或单测通过；不做大重构、万能中间层、过度测试。不靠人工补文案、镜头、内部ID或修改数据库凑通过。

产品及验收口径继续读计划§1.1.1a/§1.1.1b和所链接权威；不是从零设计。Pi负责理解和决策，确定性代码执行、记录、校验和恢复；通用执行结果必须归属原任务。已通过局部场景不外推全部指令/任意时长。撤销产生可追溯新版本，保留无关后续成果；未知执行先对账。实际账单与资源/费用估算分开。不得放宽内存、CPU、磁盘等保护以求通过。

Mac优先，手机/Windows后置；左侧名称分区不改，正式UI遵守DESIGN/shadcn。MiniMax复用仅限mobiletest01验收，不暴露密钥或跨账号复制。原24小时任务授权、正常退出保存暂停、有效授权原目标接续仍有效。用户指定模板目录 `/Users/Zhuanz/Documents/视频剪辑/output/opc-20261011`；目前原验收用公开演示素材，不能冒称工厂实拍或已确认产品事实。

## 2. 仓库、完成范围与保护的改动

唯一checkout：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`，main。GitHub：`weitongxucc-dev/ai-video-diary-agent-studio`。已推送：

- `2777aead`：现有领域/架构/计划/合同收敛事实、错误与版本一致性。
- `91d82678`：原生操作、任务产物、素材资格与工具回执、局部版本/撤销及对应前端接线集成。相关定点检查及G静态审核完成；不代表媒体闭环通过。
- `5206c057`：修真实冻结包通用命令启动错误及错误回执丢失，真实桌面后续命令/抽帧已成功。
- `5d6adc0e`：仅校准原生read的文本能力描述与本地产物预览路径；文本守卫/SDK不变。新包已构建、未启动。

Pi1.4已在 `715d02d0` 升级。旧WIP `codex/pi-native-integration-wip-20261023@b09f190e` 已被后续集成覆盖，不能重放/重复cherry-pick。无PR/tag/签名Release。

移交时已有且不属于本批的dirty：`.gitignore`及未跟踪 `designs/yaohe-desktop-language-20261020/`、`yaohe-refactor-status-20261014/`、`yaohe-tabbed-workbench-20261016/`、`yaohe-ui-direction-20261019/`。保护，不stage、不清理。产品源码当前无未提交改动。

Git推送可用命令（不改全局配置）：`git -c http.proxy=http://127.0.0.1:7897 -c http.version=HTTP/2 -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=15 push origin main`。直连/HTTP1此前TLS失败；不要输出token。

## 3. 复用线程，不重新建队

|线程|ID|接续职责/当前停点|
|---|---|---|
|新主控|01a166a6-d27e-7791-9780-1ee9eb9ede43|共享权威、有限运行决策、审核集成、最终接受|
|A · Agent 会话与直接改片|01a14301-2730-7440-ab61-69fd35d9c341|Pi与前端纵向负责人；5d6两行描述修正结束，无新写入任务|
|B · 可跳过首启与素材发现入库|01a14301-7591-74f1-b240-d3643b78ecfe|当前实际负责共同编辑/执行用例；最后只读诊断三项配音断点完成，尚未授权其新实现|
|C · 桌面真实验收与回归|01a14301-b182-7a92-a973-66fe11baf450|无源码权，固定候选独占GUI/媒体；已正常Quit，无运行窗口|
|G · 代码治理与增量质量审查|01a15854-9259-7231-977f-32bec26fd119|只读审新diff，不重审全仓、不跑媒体|
|设计语言|01a1582b-200c-7220-8187-5925f6082fc5|用户独立授权，不打断/缩减研究，不擅自采用候选|

精确文件所有权见合同。特别：section_timeline.py归A，creation其余/事务/执行归B；B可窄改server.py冻结入口；A承担前端，不把C当实现者。一个文件只一个写者。用户已授权主控向这些线程派工/发消息；不必反复征求允许。新主控先从B/C获取所缺的具体证据定位，不重读全部聊天。

## 4. 原任务、候选及真实证据

验收根目录（以下相对名均在此）：`/Volumes/Dujiangyan-VM/AI视频日记/acceptance/phone-loop-20261018`。

- profile：`mobiletest01/user-data`；数据库 `mobiletest01/user-data/studio.sqlite3`。诊断只读URI，不能人工改任务/授权/状态。
- 原业务请求 `request_6accc294d86c430bbbb76e8aa1c16088`；中文任务 `task_3f7e5a331189`；英文 `task_58f11b388376`，确认脚本 `script_da75725471cf`。不要与每轮聊天client_request_id混淆。
- 素材 `asset_6ce5dea7da1e69e2`；`mobiletest01/materials/local-maize-shelling-input.mp4`，16,577,069字节、25.578秒、3素材段，ready/approved。
- 英文原生产Job（B持完整ID，前缀job_028f）仍queued，配音执行单元started/settled均0，0/3有声段。当前没有本轮中英各30秒成片证据，也没有真实改片/撤销/重启闭环通过。

候选：

|源码|配置/应用|真实状态|
|---|---|---|
|91d82678|desktop-facts-91d82678-candidate.json / desktop-facts-91d82678-build-20261023/mac-arm64/Yaohe Studio.app|冻结入口故障，已被修复候选替代，不重跑|
|5206c057|desktop-facts-5206c057-candidate.json / desktop-facts-5206c057-build-20261023/mac-arm64/Yaohe Studio.app|原生执行→生成联系表→登记→C实际打开预览完成；随后正常Quit|
|5d6adc0e|desktop-facts-5d6adc0e-build-20261023/mac-arm64/Yaohe Studio.app|构建完成未启动；尚无对应candidate.json；复用5206 API及未变web/programmatic，仅runtimeJS两行变更|

5206输入digest：`3b5e90a03a735d12451320d9f4c7953e3591667837e1fdf4a8b952212db355fd`；5d6：`4f23bc9f48681c0ec3c515698bc5e9416598b34acb8fbb6497e31ffb2e9fecab`。读取build-identity.generated.json只取必要字段，勿打印巨大inputs清单。相关源码再变则重建受影响服务/候选，不用旧包验新源码。

真实通用操作成果：Pi抽取3/7/11/15/19/24秒六帧（480×270），随后自行合成联系表并登记原任务，C实际打开候选预览。最终745,159字节，sha256 `38595d682d64a32005d404a20037f0273a6ee910ff3cf646cfa24f1f25736e48`。精确Artifact/path由C按原DB返回；是未采用候选图片，不是时间线或视频成片。

主要证据文件：`desktop-c-20261023-native-main-run-5206c057.log`；原始失败`desktop-c-20261020-native-main-run-20261023.log`；B有限实验/隔离映射`desktop-c-20261020-native-smoke.json`。C有同批补充证据，定向问它最终文件，勿扫描全部output。

运行停点：C已正常Quit，原app/API/workers和60695/3000监听已退出，原queued任务和产物保留。5d6未运行，无构建session挂起。启动前重核进程；不要先getApp导致默认profile启动。

## 5. 当前三处断点及最短接续

### 5.1 候选未声明配音单次范围——配置整合缺口

`creation_execution.py:770`要求budget.input_scope为`single_call_experiment_v1`，否则“配音单次输入尚无明确执行范围”。`member_permits.py`读取`DEVICE_NARRATION_INPUT_SCOPE`，默认legacy_stage。当前66项候选env遗漏该字段；尚未进入TTS推理，不能归为内存不足。旧主控保存旧54项数值时没有补新必需范围，属软件/候选整合问题。

现有字段可表达有限单次实验（basis controlled_experiment_unverified_legacy_cap）。用户已授权真实TTS与资源测量；新主控应核实际输入与下两项，作明确有限运行决策后接入候选，不能单纯设值后盲跑/冒称生产标定。尚未设置/启动。复用migrate_ungranted_original_narration保留原Job与累计预算，不新Job清零。

### 5.2 选中参考声音超过当前输入预算——真实输入与依据不匹配

B只读核实：正文3段161/138/134字符，选中参考文本38字符，实际各调用199/176/172，均<374；但选中参考voice_source_e2f6…是13.072833秒、627,574字节WAV，没有选定时间范围，超过既有max_reference_bytes=182,444（超445,130）。voice前缀voice-2b55796c…@v-3641…；完整对象向B或只读DB取。

不能偷偷换其他工作区短参考、伪造对应文案、无绑定截音或提高字节限。尚未核清该声音是用户硬约束还是Pi自动选择；须从原目标/选择记录确认，再让Pi经现有声音/参考入口选择合法且适配的授权输入。必要最小产品修复由相应owner做，C不手工补声音绕过Agent。配置修正不解决这项。

### 5.3 三段一次合并与两输入执行单元冲突——实际组合执行缺口

`creation_execution.py:846`直接以len(ready_inputs)=3构造一次音频合并，既有DEVICE_CPU_AUDIO_MAX_INPUT_COUNT=2，所以即便配音完成也会被拒。不要改成虚报2、强改作品段数或调大限。应复用现有音频执行/Job/单元/Artifact，用有界分步合并与检查点组成完整音轨，保留原任务累计消耗、恢复、临时文件归属；先定向确认已有组合能力再最小修改，不新建通用万能调度框架。这项尚未派实现。

建议顺序：B在原独占范围实现真实合并缺口并明确声音输入处理差额，A仅补所需Pi/前端消费者；G定点审核。主控核有限TTS配置和合法参考。新候选固定后C独占运行原任务，记录真实内存峰值/压力/停止释放；通过同一作品连续修改/撤销/正常Quit重启，失败按原Job回执诊断，保留成果。不反复空跑或重做已经通过的命令启动证据。

现有TTS数值保持：374字符、最多5段、参考182444字节、30秒、输出3145728字节、线程2、timeout300秒、RSS2147483648字节、进程线程64、sample0.1、预留内存2GiB、磁盘268435456字节，原darwin_background策略。其它音频合并限从candidate读取，不猜。

已有真实标定只支持有限样本：`/Volumes/Dujiangyan-VM/AI视频日记/acceptance/full-loop-20261018/tts-bounded-trial/`下experiment-plan.json、short-reference-import.json、row-final.json、row-audio-facts.json。17中文字、3.8秒/182444字节参考；50.67秒完成，峰值RSS643055616字节，4.32秒/207404字节输出。它不证明当前英文199字/13秒参考；background-policy-probe仅证CPU继承。不得跨workspace直接拿其voice做原任务。

## 6. 已解决根因与避免重走弯路

91d冻结包调用通用命令时，精简环境丢失PyInstaller上下文，re-exec解包落入任务tmp，每次约41文件/54后代，触发32项文件限；不是任务授权超时或电脑CPU不足。5206仅传可信宿主3个_PYI变量、拒caller伪造/reset，在server受许可入口目标执行前清理内部环境；不开放整份宿主密钥环境。任务文件超限的actual/max/category/root回执与已知failed宿主结果已保留，不再被SDK generic exit/no-code覆盖。真实5206 echo/目录/抽帧成功。

只将三个已核对归属的故障_MEI目录改名隔离至profile/native-launcher-quarantine，未删除；映射在native-smoke.json.native_failure_20261023.quarantine。不要恢复或清理它们/用户数据。

C曾两次误判，已纠正：1) 看到授权超时日志就说没有执行，实际同轮已有3个nativeJob；2) 把一次并行3图片read当3次重试，把“将合成”当已合成，在Pi正在调整时提前停轮。后续已真实合成登记预览。今后对齐turn/job/request、文件hash和终态，不能凭计划文字、相邻日志或截图宣布完成/首因，也不要过早中断正在自主修复的轮次。

原生read在本产品只能读有限UTF8文本；上游description曾宣称图片附件，5d6两行修正如实说明，并指向本地程序产物登记/预览。不表示已新增图片语义分析工具。register_task_artifacts不需要大模型看图片才能登记；本地Blob预览不把图发远端。候选登记≠采用；图像overlay采用仍需真实Timeline。

## 7. 复用验证及构建/启动要点

已通过：素材raw/资格共享及实际HTTP消费者；协议/内容/unknown错误透传；目标依赖正反面；局部正文音画字幕撤销保留无关后续成果；字幕有效性不伪造；旧全局undo仅实际业务快照相同可用；慢工作退出长写事务。A/B相应既有小范围用例及G审核完成。5206再有2项host_resource_probes与2项实际SDK回执用例通过；5d6仅描述变更node --check/diff/G审查，无新增测试。不要重跑全量或为文档/描述写文字断言。真实媒体闭环仍待验证。

Node/Pi：`/Users/Zhuanz/.nvm/versions/node/v24.16.0/bin/`，pi1.4；pnpm在同目录。打包无签名，仅electron-builder --mac --dir --arm64 --publish never。API变更用build:api再package，web变更才build:services；纯runtimeJS可只package。生成Supabase distribution-config有公开连接配置，不打印key；保留web默认3000，不加local-web-port覆盖。5206候选env含54项原保护+12项有界native实验，后者只证16MB素材六帧/联系表，不是生产资源校准。

启动须由C在主控给定独占窗口执行：合并固定candidate的budget_environment/runtime_environment，再用明确app/Contents/MacOS/Yaohe Studio和`--user-data-dir=/完整/profile`（等号形式）。先启动指定profile，再用CUA选择运行app；避免getApp隐式启动默认profile。账号登录仍在原profile，非必要不重新要求密码。构建输入、SHA、有限配置与实际运行需对应，不把旧包+新源码拼成一次通过。
