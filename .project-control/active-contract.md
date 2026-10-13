# 活动合同：PI-FINE-128（2026-10-13）

## 目标、当前Owner与授权

用户明确要求先完成#128音量、混音、调色接入Pi与CLI，以及自然语言修改→实际预览→普通界面采用→新版本成片的真实闭环；同步既有教学线程，及时保存推送GitHub；核对截图和竞品调研的能力/功能/设计是否逐项有Issue并补遗漏。owner=/Volumes/Dujiangyan-VM/AI视频日记/repositories/desktop-v32；branch=codex/desktop-v32-20261010；基线4b49aa19413e6b8f29506ef6ca2fca90aa36c7e2；实时origin/main=5968e9bf0471a7ff0ea088e87f84febc873de300。original为脏历史checkout，不写、不合并。此合同替换已释放PI-CONT，不复活旧lease。

## 权威与分工

稳定语义引用架构§3.8/3.10/3.13、领域§6/7、产品§2.3/2.5/2.6/9.3、DESIGN§2及开发流程；认证/资源/实际采用不变。本轮S2，由root负责决策、唯一共享文档、集成、正常模型/GUI/CLI/媒体验收及GitHub/教学；按当前宿主并行指令与用户最新AGENTS组织最多三内部实施Owner，禁止下级。一个文件一个writer，未授权文件先报路径。

- API/编辑Owner独占apps/api/app/schemas.py、main.py、必要audio_timeline.py/visual_effects.py、直接test_typed_assistant_edits_api.py及必要test_audio_timeline.py/test_visual_effects.py，允许新增test_assistant_fine_edit_api.py。先发布EditAudioTimeline/AdjustVisualEffects真实协议，随后实现共同目录/精确受理与现有audio/visual预览、实际采用及同一渲染内核。复用真实字段/单位/边界，不复制一套mix/调色执行器或审批/版本库。所有main整合归此Owner。
- Pi/CLI Owner先只读本次工具/CLI消费者；API发布协议并由root核定后取得apps/api/agent_tools/diary_tools.ts、task_facts.ts、apps/api/app/assistant_pi.py、直接test_assistant_typed_tools.py及scripts/yaohe.mjs/yaohe.test.mjs。能力发现、完整对象读面、自然语言typed调用与外部CLI沿同核schema/请求/版本绑定。不得写API Owner文件，不自造字段或扩大许可。
- 调研覆盖Owner只读当前相关Issue与开发计划§4.5/§6/§13、既有竞品研究的结论/证据；给出截图中执行记录、轨道/波形/锁定/独听/音量表/关键帧等，以及研究后采纳项的Issue映射和遗漏建议。不跑新付费竞品、不改产品/文档、不写GitHub。
- root独占本合同、开发文档与CLI文档、Issue/PR、临时独立合成验收样本和脚本。教学线程只读讲解。

本轮API/编辑、Pi/CLI与调研覆盖包已RELEASE；root收回审阅、集成及正常模型/GUI/CLI媒体链验收。新接口只开放audio update/track混音patch，本波gain_points仍只读；其他音频结构动作继续按其实际入口判断。该释放不表示真实闭环通过。

CLI成片消费者必要窄补：Pi/CLI Owner重新独占scripts/yaohe.mjs及yaohe.test.mjs，补已有execution preview/start/status/原job有界wait/resume薄客户端，使本轮CLI采用后能沿普通API生成并回读真实成片。start必须显式提供真实preview/script/digest及requirements_confirmed；unknown只查execution原job，不拿编辑请求locator代替，不重POST；wait绑定真实job身份，任务发生新作业不得混为原作业成功。无新API/状态库、默认授权或完整CLI安装范围。

真实采用暴露必要消费者缺口：creation_execution.cpu_render_facts尚无多轨音频资源绑定，不能单独移除其拒绝。root已核定必要同核组合许可方案；API/编辑Owner窄补独占creation_execution.py、main.py、audio_timeline.py、task_audio.py、host_resource_probes.py、member_permits.py及直接测试；沿现有混音及CPU成片同一许可，实际来源/区间/次数/中间输出/单位预算、文件变化及当前身份须纳入输入与重读核对，组合资源必须消费已标定CPU_AUDIO核验。linkage归一化标志须在同一facts及预览/最终保持一致。start登记后准入拒绝应给出真实阻断，不假装运行。无新执行器或生产预算放宽。Pi/CLI窄包已RELEASE。

调研覆盖Owner重新取得仅本计划§4.5及覆盖映射的文档lease；其他文档和合同仍由root独占。

组合许可冻结：CreationJobOwnerVerifier沿可信audio_mix_facts封存同一资源声明，HostResourceProbe同时核既有CPU_RENDER/CPU_AUDIO预算；顺序memory取较大值、磁盘计并存中间WAV。API/编辑Owner补granted_subprocess.py及直接测试lease，仅可信组合音频用途消费已标定音频子进程RSS/线程/输出约束，视频保持原预算，不开放任意用途。明确尚未submit的准入拒绝沿原JobRun失败/impact/repair，不新增状态，unknown不伪造终态。旧QA被拒作业由正常API取消并保留历史，正常用户库不改。

共享许可改动须保留既有消费者：compound按原编译器及included selection封存实际after；RemoveSilence的动态after仅由同job真实分析来源和removed_ranges_ms沿原指令/版本重算，核来源/hash/当前actor及音频uses/中间输出上界。不得给未知graph无条件许可或遗留本轮引入的回归；范围仍为同7文件和直接测试，无新执行队列/预算默认/领域对象。

Pi/CLI Owner窄QA lease仅本轮独立evidence的silence-case/脚本：准备新的合成5秒单镜头/单声音静音样本，不运行/提交或改DB；root待最终API就绪后按普通录音/采用/typed预览检查动态回归，无私人数据或生产库变更。

组合API包及独立QA准备均已RELEASE；API集中239项检查通过，最后动态上界窄回归19项通过，Ruff和diff检查通过。root收回集成及真实验收；静音样本最终为2秒/60帧、0.7–1.3秒真静音、linkage=True/-16LUFS，两PCM上界899072B；沿实际旧command预览入口验回归（RemoveSilence未进入typed/CLI目录），不新增该迁移范围。

真实eab818b验收窄修：初次无字幕轨渲染的SRT5秒与后续初轨投影2.5秒不一致，API Owner独占main初轨投影及test_montage_pipeline/test_timed_subtitle_render_input直接用例，改为同narration_schedule，不改既有显式轨。静音after混音首超时确证固定audio15秒包含中间video，沿原7files/creation_audio_mix改为同context音频active阶段累计≤15、每section固定剩余时间、video只outer60，不增加预算。root显式修正本合成QA旧错轨并重验，正常用户库不改。no_progress但实际步骤完成的语义缺口归#120/#130，不在本波引新泛化完成证书。

API Owner新窄修回合因宿主模型容量错误终止，未发布新diff。root收回其lease并授Pi/CLI Owner上述音频active时钟及首次字幕投影的同7files/直接tests唯一writer；原API Owner只读/停止新写。已授权范围及旧eab818b实证不变，无新模型/预算或外部操作授权。

## 验收、非目标与实际旧路径

共同typed目录支持真实音频对象音量/静音/淡入淡出/混音参数，以及镜头现有画面效果；未支持独立音频retime/通用关键帧/solo持久化不承诺。既有手动audio/visual入口保持，其执行内核由共同受理入口复用，不建立第二validator/状态机。实际对象必须按当前版本读取；只改指定字段，不把完整效果默认值覆盖其他效果。

最小直接检查先由Owner完成，协议合流后root一次相关集成；真实验收使用独立合成两轨音频/两镜头：正常已配置M3通过普通界面提出指定音乐片段降6dB+末尾淡出，实际前后试听、采用、自动出片及回读；镜头调色真实前后视频、采用与输出；CLI沿同schema完成同样预览/采用并查询保存版本。核对无关镜头/音轨/字幕不变、过期/锁定/重复请求/unknown和撤销恢复，输出实际摘要/尺寸/时长及音频测量，不以模型自报或成功文件替代内容证据。正常模型和真实输出未验收的不关#128。

本轮不实现整个剪辑台重设计、关键帧/新轨道模型、字体/OCR/语义/听感AI、新供应商/依赖、迁移生产DB、删除用户数据、打包/签名/发布/替换安装应用。竞品建议按已有Issue补全或明确新Issue，不用规划冒充实现。

## 运行和交付

沿既有模型连接，不打印密钥；私有凭据/原媒体不复制出真实库，不外发媒体。延用用户明确许可的临时退出原应用、验证后恢复授权：先核原应用无活跃作业，唯一设备交接采用正常退出，不取消用户作业。独立QA沿原设备协调者和实证预算；捕获实际资源决定，未知不盲重跑/加预算。

代码分阶段保存提交并推送既有分支，保持Draft PR#124不合并；GitHub对应Issue维护动态进度，长期权威/开发计划只写设计接口和覆盖映射，不重复完成度。当前已知GitHub CI因账户付款/消费限额未启动，核实时状态，不放宽本地检查或冒充通过。未经实际结果不关闭Issue。
