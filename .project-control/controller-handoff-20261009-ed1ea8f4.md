# 主控交接快照：2026-10-09

本快照供用户指定的新主控接手；唯一归属和权限仍由[活动合同](active-contract.md)规定。运行情况与SHA须按实时Git/CLI回读，旧任务不因交接复活。

## 接收人及控制权

- 用户明确指定窗格：`ed1ea8f4-8d0d-49cb-abad-0ba2a3b20a8c:f59b1ff5-5526-4ab1-942c-f485845cf255`。
- 已核实接收句柄：`term_f7d3806a-fe63-446c-b5ee-c8b0c3164345`，Codex，同一main工作区。
- 唯一入口：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`。旧主控`term_2a22cf6b-53e4-402c-969f-5365d6a15444`送达交接后停止派工、源码/共享文件、GitHub和Linear写入。
- 保留既有Run `run_e3cb6568939f`；不是新增worker，不创建Run/Task/Dispatch。交接前coordinator为旧句柄、consumer_generation=1、legacy=0。
- 新主控在自己的终端读取当前orca-cli/orchestration指导，执行`orca orchestration run-use --id run_e3cb6568939f --json`，再run-show核coordinator为自己的句柄。若CLI要求接管，按其准确恢复命令在本终端执行；用户已授权主控转移，旧主控已停协调。不得从旧窗格冒用新句柄。
- 接管后消费原Run FIFO收件箱、逐消息处理并ACK；workers check用自己的`--terminal`且不带`--run`。不用旧Dispatch发送新worker_done，不从历史START运行。

## 用户目标和既有决定

核心是对话驱动系统真实原子：流式回复、可见工具执行、选中素材一句话改标签并在右侧/刷新可见；以及对话创建→文案→配音/已有录音→剪辑修改→重新生成→保存视频。用户要先用本机软件试出问题，避免测试数量、宽度矩阵、反复请用户代按键和防御性兜底拖慢主线。

- 当前优先本机ARM64包能跑、演示和试用。新电脑安装、Intel实机、自动依赖准备、签名公证后置；Pi/Node和本地模型不塞入本次.app，沿用本机已有配置。
- 本地模型、客户自备MiniMax/DeepSeek Key；国内MiniMax为当前默认，**无产品收费/报价/充值/扣费施工**。删除确认保留。
- 飞书只是将来举例，本期后置；不建机器人、不索取凭据。
- 首版仍要求抖音/小红书/视频号自动发布，不能悄悄改成人工上传。具备三账号、既有本机扫码只读授权不等于上传/保存平台草稿/公开发布授权。
- DESIGN v5.3及soft-cards-v3-all-right已批准：保留左/中栏；统一右侧360×560浮卡、14圆角、父卡状态、顶层关闭/回焦；普通编辑直接草稿与撤销，无额外确认链。不要重新整轮改UI。
- Linear/GitHub/Agent跨项目制度讨论在`5c315f73-cb59-4f47-8a15-1bbd760d5abf:b50a4262-edb2-4851-9557-adf78df6ac7a`，本窗格继续产品coding主控。

## Git与GitHub：不要在main旧产品代码上直接施工

- 交接前main clean，HEAD `bb80b249e61c93b01534b07c47130b5960d76bf9`，对origin/main ahead225，净差额是控制/设计文档，不是最新聊天产品实现。此交接提交会改变该HEAD。
- 主控记录已推送`codex/controller-state-20261009`；本次交接文档及归属变更仍推到该分支，不直接push main。
- 最新产品集成树：`/tmp/main-controller-20261008/ui-v3-integration`，分支`codex/chat-delivery-20261009`，clean HEAD `6d7b0ada06e8389cd5495c2d32501fdb92102e60`，远端同值。
- PR #102： https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/pull/102 ，draft，Backend/Frontend/Desktop syntax均SUCCESS（run37880268355）。尚未合入main，未做Linear结算。
- PR是叠加链：main→#96创建恢复→#97UI v5.1→#98人物许可→#99版本草稿/UI v5.3→#100发布队列/桌面配置→#101首次录音/冻结许可子进程→#102对话/素材。全链draft；不要只把#102当独立main PR直接合并，也不要整体merge旧混合工作树。
- #101 head `4c748dfc45229b879e9e1daf473b309432899f4c`；#102产品打包源码`7aa1eedd7480b45e09561860485eb6a23384005f`。之后eea5f306/6d7b0ada仅两处已有测试修正，运行产品blob相等，**无需重包**。
- main产品代码只经PR、无自动合并批准。用户过去批准#90/#92/#93/#94不覆盖#96–102。

## 已交付试用包及证据

- 用户包：`/Users/Zhuanz/Downloads/智能视频日记_对话更新试用_20261009/智能视频日记 0.11 测试版.app`；同目录启动对话更新试用.command/先读我.txt/package-receipt.json。
- launcher保留上一试用userData：`/Users/Zhuanz/Library/Application Support/智能视频日记_本机试用_20261009`。不读/复制其DB、用户Key或随意变更设置。
- 用户已批准退出旧版；旧PID45104/Next45400已正常退出。12:43核新版PID31698仍运行，窗口25151曾显示工作台。它现在是用户使用的软件；关闭须按活动合同和实时归属判断，不能机械复用旧关闭批准。
- B106原包及report：`/tmp/main-acceptance-20261009/chat106/report.md`；candidateInputDigest `9c0a8e3d831821911e71fecec3326ebaf1511a9631c66624565867f13fe95523`。API sha256 `8423323df3f416512e891f6da63e737a8e6bc1ce99982e62169f379c7b92fa5d`；app.asar `06d3a2dfc50b6adc1d52efe826dc7b6d5f6e6811782295abd4183597a3b211e7`。
- B108真实包API/Pi：`/tmp/main-acceptance-20261009/material108/report.md`。仅一次国内MiniMax-M3/Pi1.1.0，4.21秒，两个工具start/end、两个text_delta、final result；GET→PUT→GET及独立GET、停机只读库均确认新素材user_tags=[产品演示]；其他字段保留。
- B108隔离导入分析因自己PATH缺ffprobe真实失败，不是用户软件分析成功证据，也不能据此宣布用户机器缺ffprobe。未加载本地模型。
- B108自有40320/40389及Pi组已退出、18108空闲；根扫26文件无已知Key命中，新临时keychain已锁。**旧运行租约结束，不复活。**
- 原生工具能截图，但对新版点击`--restore-window`仍被window_not_focused拒绝；GUI右侧标签和刷新未验，不用API或CDP冒称GUI、不反复要求用户代按键。后续按当前工具真实能力选择可执行入口；明确拒绝不绕过。
- 旧真实成片证据`/tmp/main-acceptance-20261009/rerender97/current-video.mp4`：5秒1080×1080/H264/AAC、V5→V6；只证明当轮许可/渲染/产物，不能证明当前聊天完整链路。

## 执行线程和未提交工作

所有旧Dispatch已结案/保留，无当前产品写权；交接前reclaimable清单为空。新主控继续时只复用这些用户可见终端，写具名当前任务，不派内部子智能体、不新建执行窗口。主控负责文档/审核/机械集成/PR/Linear，不编写产品代码。

| 线程 | 句柄 | 工作树/状态 |
| --- | --- | --- |
| UI | term_0ff98007-d2b6-442e-b45e-7d1b0b4d9811 | /Volumes/Dujiangyan-VM/AI视频日记/repositories/main-ui-linear；clean6d7b0ada；UI109结案retain |
| B | term_2ee18b71-c3c8-4e56-b551-5f5a765c4298 | /Users/Zhuanz/orca/workspaces/main/pipefish；platform99分支09680ab7，base/douyin/playwright_driver/xiaohongshu四文件脏树原样保留；B108结案retain |
| A | term_79a48d73-cb24-4736-a0c2-4e995483ea88 | /Users/Zhuanz/orca/workspaces/main/manta；65额度中断/fenced，17个已有脏文件，不能直接恢复或覆盖 |

未提交的B99/A65工作未被推送，也未进入当前包；后续按真实diff和作用范围判断复用，不reset/checkout丢弃、不能声称全系统原子已完成。

## 下一步与未完成范围

1. 优先在准确当前候选完成一次真实对话创作主线，逐个真实断点修复；选中镜头聊天修改→一次应用→重出→保存。不要复测未影响UI/旧模型/全仓，只为实际变化与断点验证。
2. 素材标签GUI的右侧显示与刷新仍需正常客户端操作证据；素材/片段绑定和API持久化已有源码与真实Pi证据。
3. 三平台：账号适配和完整发布回执仍有缺口，#100不等于三平台完成。B99报告`/tmp/main-acceptance-20261009/account99/report.md`、UI98同名目录报告可定向读，账号profile原地保留不复制、不公开；发送前确认账号/内容及具名权限。
4. 仅在范围完成后审核对应PR、获得按合同所需合入批准、合入和Linear结算分别回读。Linear issue当前状态本轮没有再在线核查，不用旧195项统计当实时完成率。

安全准备资料只在确需新实际验证时读取：用户已授权五秒本人媒体`/tmp/main-acceptance-20261009/creation92/library/user-video.mp4`及user-audio.wav原件只读；外接models `/Volumes/Dujiangyan-VM/AI视频日记/models`、model-runtime/bin/python；Pi `/Users/Zhuanz/.nvm/versions/node/v24.16.0/bin/pi`。MiniMax文档`/Users/Zhuanz/Desktop/sk.docx`及native service `ai-video-diary.workspace-secret.minimax-validation-20261007.llm_api_key`/account同名，只能按既有具名授权在RAM处理；本交接不包含任何密钥、令牌、账号ID或会话。无自动模型/媒体/外发运行授权。
