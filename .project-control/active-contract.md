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

## 验收、非目标与实际旧路径

共同typed目录支持真实音频对象音量/静音/淡入淡出/混音参数，以及镜头现有画面效果；未支持独立音频retime/通用关键帧/solo持久化不承诺。既有手动audio/visual入口保持，其执行内核由共同受理入口复用，不建立第二validator/状态机。实际对象必须按当前版本读取；只改指定字段，不把完整效果默认值覆盖其他效果。

最小直接检查先由Owner完成，协议合流后root一次相关集成；真实验收使用独立合成两轨音频/两镜头：正常已配置M3通过普通界面提出指定音乐片段降6dB+末尾淡出，实际前后试听、采用、自动出片及回读；镜头调色真实前后视频、采用与输出；CLI沿同schema完成同样预览/采用并查询保存版本。核对无关镜头/音轨/字幕不变、过期/锁定/重复请求/unknown和撤销恢复，输出实际摘要/尺寸/时长及音频测量，不以模型自报或成功文件替代内容证据。正常模型和真实输出未验收的不关#128。

本轮不实现整个剪辑台重设计、关键帧/新轨道模型、字体/OCR/语义/听感AI、新供应商/依赖、迁移生产DB、删除用户数据、打包/签名/发布/替换安装应用。竞品建议按已有Issue补全或明确新Issue，不用规划冒充实现。

## 运行和交付

沿既有模型连接，不打印密钥；私有凭据/原媒体不复制出真实库，不外发媒体。延用用户明确许可的临时退出原应用、验证后恢复授权：先核原应用无活跃作业，唯一设备交接采用正常退出，不取消用户作业。独立QA沿原设备协调者和实证预算；捕获实际资源决定，未知不盲重跑/加预算。

代码分阶段保存提交并推送既有分支，保持Draft PR#124不合并；GitHub对应Issue维护动态进度，长期权威/开发计划只写设计接口和覆盖映射，不重复完成度。当前已知GitHub CI因账户付款/消费限额未启动，核实时状态，不放宽本地检查或冒充通过。未经实际结果不关闭Issue。
