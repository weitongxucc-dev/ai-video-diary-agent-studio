# 活动合同：PI-CONT-120-130（2026-10-13）

## 用户结果、基线与权威

用户已明确授权：先修#120自动接续及#130中栏可见执行，再以真实视频完成连续修改/保存/恢复验收，通过后打包供本人试用；同步现有教学线程，及时保存提交GitHub，并解释全项目哪些已做、本轮做及未做。owner=desktop-v32，branch=codex/desktop-v32-20261010，产品基线c263a112c7e2433d1e45ff392b8c3d3d1cd9f452，实时origin/main=5968e9bf0471a7ff0ea088e87f84febc873de300。original为脏历史checkout，不写、不合并。既有PR#124保持Draft，不自动merge/发布。

S2开发按当前用户AGENTS与宿主并行指令执行，旧仓库的可见线程/cmux限定不作为本轮限制。root唯一主控/共享文档/Git/Issue/QA及打包决策者。稳定规则绑定架构§3.13、领域§6/7、产品§2.3/2.5/2.6及§14、DESIGN§2/7、开发流程§3.1；不创建新完成状态库、不全局放宽版本/权限/采用/资源门。不改模型供应商或安装依赖，不盲扩保护线/默认资源额度。实际原接口422“CPU音频预算未标定，未启动媒体”后，root核准只对已采用115.788秒单旁白工作量做有限CPU_AUDIO标定：保留原实测进程RSS256MiB/线程8/FFmpeg1线程/15秒/0.1秒监控及512MiB内存保护；仅显式声明真实输入6MiB、时长120.401秒、PCM输出32MiB和临时磁盘64MiB边界，先测正常整机许可下的原音轨试听，未实测不称已标定。宿主CPU91%/空闲内存1GiB及原render2GiB/512MiB门不放宽；若拒绝先读决定、不盲重跑。正常观察已经沿原CPU_RENDER成功，仅有限抽帧/媒体规格，OCR/语义/听感明确不可用，本轮不额外配置OCR预算。原配置不复制凭据/DB，实測通过后才能给同机试用采用对应本机profile。

## 独占分工与协议

- continuation Owner（pi_objects）：apps/api/app/main.py、assistant_pi.py、assistant_goal_continuation.py、agent_tools/diary_tools.ts及其直接test_assistant_goal_api.py/test_assistant_goal_continuation.py/test_assistant_pi.py/test_assistant_pi_failures.py/test_assistant_typed_tools.py。必要现有对话repo/DTO变更先向root报告具体路径，核准后独占。真实start/resume回执核验后立即持久既有locator/proof；普通制作Job与TaskMediaJob分别查询；模型中断不丢原作业来源，原成功结果可在未失效目标中继续观察。失败/停止/新指令/撤权/旧版本/unknown保持原语义。已完成的有限制作/观察链与无进展清楚区分，不凭模型、空steps或旧文件宣称任意要求全部完成。向Web发布实际可用的goal/event投影字段及可信错误契约，不发原载荷或隐藏推理。
- Web Owner（execution_ui）：AssistantConversationPanel.tsx、AssistantConversation.css、useAssistantConversation.ts、AssistantConversationProvider.tsx及直接ConversationPanel/ReadRaces测试；apps/web/src/lib/types.ts/api.ts只允许本轮会话event/goal字段与解析消费者。复用原目标、真实工具事件、提案/版本/作业/观察结果；展示当前目标、真实步骤、产物及等待/失败/恢复原因；读取/提案/采用/排队/运行/完成分开。不新建进度状态库、不改全局三栏、不抢未发送草稿、不显示原始工具载荷/路径/凭据/推理。旧失败历史保留但不充当已恢复后当前阻断。遵循design-md：DESIGN.md完整读一次。缺API字段报告，先用现有真实字段独立实现，不自造成功。
- coverage Owner（pi_capability_audit）：只读代码、Git及GitHub#103–#140已有正文/评论与开发计划覆盖；仅写本轮QA目录coverage-review.md/json。按用户结果归组，明确已有实现/已验证样本/未完整验收/本轮/后续/待决定/0.12；工单OPEN不等于零实现。root更新既有总工单#117为唯一动态总览入口，不新增永久完成度文档。不得改源码、共享文档、Issue或运行服务。

每文件一writer；子Agent不得下级派工、Git提交或服务/用户数据操作。共享接口先发具体增量，由root核定。报告changed files、最小测试、首个实际拒绝、未验证项及RELEASE。root审核diff后集中一次相关集成验证；单个子包完成不结束用户任务。

已核准 continuation Owner 窄增加 conversation_repository.py：现有result合并及运行中登记，模型失败/重启仍保留宿主核验的execution/proof/goal；无新表/queue。execution路由新增可选assistant_turn_id query不得视为可信授权，沿现owner/task/运行turn scope与版本/实际采用/原真人要求核验实际Job后关联，不准把裸UUID或query授给其他目标。有限链完成条件另报root审核，空steps、模型报告或旧产物均不足。

已核准同Owner的 schemas.py 仅AssistantStreamEvent可选failure字段，服务器固定reason/repair_action两键安全投影，沿现sanitizer和长度边界；SSE与历史相同，不接原载荷/物理路径/凭据/隐藏推理。completed_output_checkpoint只为同现有goal的宿主核验refs/measurement/unavailable投影，scope固定production_and_output_observation；无新status enum/独立完成事实，不能清除原未完成要求或凭它宣布全部原目标成功。

## 验收与旧目标

技术：原start回执已核验→模型回合中断→原Job真实成功→持久来源保留→未失效目标从新事实继续检查；两次修改、提案实际采用、同版本/hash观察、stop/拒绝/新指令使旧目标失效、重启及unknown原请求查询；不重复制作。退役目标是依赖模型最终返回才记录制作来源及误用媒体Job查询制作Job的消费者，不改正常历史/旧数据；同候选正向与窄负向验证。

可见：正常模型和GUI在原栏宽显示目标/步骤/当前状态/具体错误/恢复及检查范围；重启、切页、离开最新回复和编辑下一草稿保持；没有假百分比或排队完成勾。

真实视频：沿已有用户确认文案/授权素材/声音，在独立验收项目中完成实际视频、两次自然语言修改、精确操作/撤销、保存MP4/SRT与重启回读；当前授权不能替代新实际Script或提案采用。先做只读预检：若真实素材/声音或能力条件缺失，明确具体缺口，不以5秒合成样本替代。2026-10-13实查原4段Script、已采用115.788秒generated旁白、21镜头及3原素材均存在，单段录音采用接口不适用于它，local_adoption也不是完整图迁移。root核准改用原工作区task_93d78477016b的正常exact预览/采用/成片/undo与重启回读做可撤销真实编辑验收；不复制DB、不重绑owner、不合段、不伪造录音或新TTS，不改其他用户任务。验收范围明确是已批准真实视频的编辑闭环，不能据此声称从空白全新配音也已验收。原device.running=0，旧成功Job与3条input_stale历史完整保留。按原同设备协调者依次验证；用户已授权临时退出原app并恢复，先核无活跃用户作业，不取消用户工作。输入和输出只在本机，不上传私人媒体/DB。

打包：功能与真实验收完成后读取桌面README/RELEASE及workflow执行实际适用门禁，生成本轮开发验收安装包供本人试用；不把部分能力包宣传为正式0.11全产品完成，不扩大到三平台发送/签名发布。本轮明确试用授权优先于旧范围；财务/账号或不可替代门禁阻断单独标明，不伪造CI通过。临时QA服务/子进程释放后恢复原app。

## 外部交付与本轮非目标

及时提交/push已有分支，维护PR#124及#117/#120/#130真实进度；未验收不关Issue。教学既有thread=01a13039-45e5-76d0-8fbb-8ff3c02b0e6f；发送失败则留本课材料并如实报告，不新建重复教学线程。结束按workflow将剩余工作交回Issue/PR并删除本合同，Git保留历史。

不实现全部#128字段、#129中文OCR/字形、#132–#135通用多轨/监听/变速/关键帧、#136–#140偏好/批量/手机/待定设计/平台外发，不新增广泛研究。已有能力仅为当前真实链路必要故障做窄修，新增范围先报root。全项目状态来自代码、Issue、Git、CI、Release和真实证据，开发计划只维护范围/归属，不记录第二份实时完成率。
