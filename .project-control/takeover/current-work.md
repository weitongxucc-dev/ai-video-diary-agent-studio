# 在途工作与接手顺序

快照：2026-09-27晚，集成源码8ef879e。此后候选仍可能推进；接手必须核实际Git/工作者交付，不按快照强制回退。Linear快照见linear-snapshot.json，范围见scope-snapshot.md。

## 已核实集成状态

- 集成目录 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`，分支 `codex/0.11-development`，8ef879e已推远端。
- 未跟踪 `.project-control/handoff.md`、`.project-control/0.11-bug-backlog.md`、`work/integration-011-tmp/` 保留，不暂存或删除。
- PR45：https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/pull/45 ，OPEN，head b281d9c / codex/011-ci-followup，Backend/Frontend/Desktop syntax均SUCCESS。该树对应68fd7a5，不含8ef879e派工文档及以下未集成候选。绿灯只证明该head。
- PR44已squash合main（8d09c8e历史观察），不是主控在本轮自动合并。先核远端，不按历史SHA判当前main。

## 各包状态与下一步

文件独占以active-contract为准，下列仅帮助定位。所有“PASS”均为组件限定，不是父范围完成。

| Issue / 执行者 | 工作树、基线与候选 | 当前证据 / 下一步 / 阻塞 |
| --- | --- | --- |
| WIL-65 MAKE | ~/.codex/worktrees/fc12/original；base44ea483；1b4b231→7245555→42cc339→a816127；main.py/creation_execution.py及测试有未提交改动 | montage短输出/取消状态问题在42cc339获QA PASS，157项；主路径尚未交。先完成既有配音+确认纯素材montage真实CPU导出，不支持人物/美颜/未计量组合前置拒绝，不丢有效音轨。不得开放全局scheduled |
| WIL-66 SCHED | ~/.codex/worktrees/device-scheduling/original；CPU dadc618，scene2da0594；granted_subprocess.py/host_resource_probes.py及测试dirty | CPU probe有限PASS；scene与MAT整体HOLD。正在补进程树RSS/线程/整机余量采样，超限停止及确认释放。标称fps不能算解码硬上界，未校准源组合dependency |
| WIL-68 MAT | ~/.codex/worktrees/7d8c/original；base63d3c2c；279f492→49fa7b2，核时clean | 14s逻辑窗/15s读取/2fps/320px/30输出帧，v1缺fps升级v2恢复获QA PASS；联合资源HOLD未解。待SCHED发布ResourceLimit/ResourceUnavailable异常，补失败/等待无checkpoint测试；v2终态失败人工retry尚无授权入口，ASR/VLM仍缺，不能ready |
| WIL-67 AUTH | ~/.codex/worktrees/account-sharing/original；base96c28e2；422cd42；仅work未跟踪 | PG邀请及Python适配器QA PASS，真实隔离PG6项；可做三新增文件兼容集成审核。真实注册根/凭据注入/桌面接受/本地资源权限未接。AuthService现需已有成员，首次接受须可信JWT身份入口；方案在work/invite-first-identity-wiring.md |
| WIL-69 UI | ~/.codex/worktrees/ui-v4-integration/original；base743ac02；46e7a90，clean | 新录音组件3文件，8单测/类型/lint；SSR截图，未真人麦克风验收，未父层持久化。先review候选再交MAKE接onRecorded(blob, metadata)，不可算R19完成 |
| WIL-70 Pi | /Volumes/Dujiangyan-VM/AI视频日记/repositories/ci-home-contract；base68fd7a5；a8fdc2b / codex/wil70-template-states | 已交候选未审核未集成，AppShell浮卡/三尺寸/父层接线未验。仅模板组件/测试，自身样式若有须核diff。报告在主控项目external-agents，先审后下一单 |
| WIL-71 Claude | /Volumes/Dujiangyan-VM/AI视频日记/repositories/ci-font-portability；base68fd7a5 / codex/wil71-publishing-states | 已定位发布页状态缺陷，终端出现429五小时上限，未交候选；核时HEAD仍68fd7a5。恢复前核dirty，不把额度阻塞当完成；不自动换收费模型 |
| WIL-55 UI / WIL-56 DESIGN / WIL-60 MAT | Linear仍In Progress，关联原owner线程 | 旧大包状态未收口，不重新派整包；分别核既有UI/知识库/接收解析集成与剩余验收，不能因新子包交付直接关父单 |
| QA | ~/.codex/worktrees/26aa/original，work/qa-011/ | 只写隔离证据，不改产品；精确报告副本在本包evidence目录。新监控候选需再验，旧PASS不传递给未来版本 |

账号决策：接受后进入RPC返回且授权验证通过的目标空间，保留已有个人空间；不按owner/最早排序跳回。后续请求每次验证成员权限，客户端workspace ID只是选择。

## 接下来的顺序

1. 收齐在途MAKE/SCHED/MAT最后候选与dirty清单；原owner未停止前不抢写。先审Pi70与UI69等独立现成小包，避免重复开发。
2. 主控审核AUTH422cd42 diff与QA证据，可先集成隔离组件但保留首次身份/设备根缺口。
3. 资源监控新候选→MAT异常接线→QA联合复验→再集成；HOLD不被“所有单测通过”解除。
4. MAKE主路径真实短片/取消/恢复通过后集成。阶段只支持明确组合，其他正式功能仍在R表，不默认砍掉。
5. Claude额度恢复后继续71；若转给Pi，先确认Claude停止/交dirty，更新唯一owner再转，不能双写。
6. 每次集成补Linear候选+集成SHA+PR；待验收不夸称完成。随后从范围表选择首启/邀请接线、素材理解与纠正、录音应用等具体包，按依赖推进。

## 通信和工具

cmux CLI：`/Applications/cmux.app/Contents/Resources/bin/cmux`。本次核对workspace:1，surface:1=Pi，surface:2=Claude；surface:3为用户Hermes，不操作。重启后这些编号可能改变，先list/read-screen核对，不能照旧编号发送。

读屏例：`cmux read-screen --workspace workspace:1 --surface surface:1`。发送用cmux send然后回车，必须再次读屏确认真正开始；Claude有时第一次回车只是草稿，不能反复盲发。具体CLI参数先`--help`核对。

Pi当前终端显示deepseek-v4-pro（不是先前Kimi），保留配置；Claude曾报额度上限。工具名字不证明模型或额度。两者并不自动拥有Codex的Linear MCP/侧边栏消息工具。

Codex可见线程（可用于现有主控收尾；新工具无法访问时以交付文件/Git为准）：

- MAKE 01a0d259-67ee-75f1-ab83-8982eec022c7
- MAT 01a0d259-54c3-7621-b055-17ce9146cac4
- UI 01a0d259-8b42-7d30-82c5-8834f31842e5
- SCHED 01a0d29d-226b-7460-8ba6-76f14dc8c56a
- AUTH 01a0e1c4-23e1-71b1-b23a-22ea2993e7ef
- QA 01a0d25f-53ad-7a83-8197-16f9d0a17aa2
- DESIGN 01a0d25f-5080-7991-9e9c-54efde64d4e9

报告外部路径：`/Users/Zhuanz/Documents/Codex/2026-09-24/volumes-dujiangyan-vm-ai-repositories-bd25/external-agents/`。报告不包含secret或用户媒体；离机接手仅Git不含未推分支/dirty，请先保存候选并核需要的证据。不要声称交接包等于所有在途代码已推GitHub。

## 后续增量：联合候选等待最终复验

SCHED7c9414e监测叶子QA通过；MAT d4519ed已补Limit终态/Unavailable延期及真实探针测试。准确集成依赖：SCHED dadc618→2da0594→7c9414e，MAT279f492→49fa7b2→d4519ed。MAT工作树现分支codex/011-mat-scene-joint-check，其中SCHED等价挑入a055d1b不要重复集成。工作者报联合89通过，独立联合QA尚待结论。生产预算未配置，仍dependency。最新逐线程记录见evidence/material-takeover-status.md及scheduling-takeover-status.md。

最终独立QA：d4519ed限定测试profile的场景窗口联合集成PASS，89测试及真实32s长GOP恢复通过，报告见evidence/mat-sched-d4519ed-joint-review.md。这些候选仍未合入开发分支；生产SceneWindowBudget/硬件校准、失败probe人工重试与后续音画分析仍缺，不将父R10/R31标完成。

MAKE最终交接增量：实现候选3aa2464，接管说明8d8050d，fc12工作树已干净。说明见evidence/production-takeover-status.md。工作者专项15+39+18通过，主路径独立QA尚待；必须验证inner/outer检查点崩溃恢复不重复FFmpeg、取消竞争和多空间，不能引用旧montage叶子PASS替代。仅旁白+纯素材剪辑，生产scheduled未启用。

**最新QA阻断（3aa2464）：** 已实证inner产物发布后、outer检查点前崩溃，重进执行会再次跑FFmpeg（调用1→2）。初判新增字幕轨道改变重算input_digest，使成功内层作业无法复用。QA隔离失败用例位于26aa/original/work/qa-011/output/make-3aa2464-sched-dadc618/apps/api/tests/test_creation_execution.py；已退回MAKE修复。该候选禁止按原专项通过直接集成，稳定输入绑定与真实版本变化都须覆盖。

**同候选第二QA阻断：** assert_grant之后、claim_job_run之前API将JobRun取消，claim抛ValueError而permit遗留held。须在无活进程且状态核实后可靠释放，不能无条件finally放行。QA用例test_qa_cancel_between_initial_grant_check_and_claim_releases_permit已失败；第一重复渲染问题也在真实HostResourceProbe下复现。两项均已交MAKE，后续新SHA须复验。

MAKE两项修复候选5ee08db已交，接手记录7c68f9c；工作树clean。作者报恢复实际render1→1、源字节/独立版本变化拒绝旧许可、claim前取消释放通过。**独立QA尚未复验，不解除原HOLD。** 对应实现/验证说明见最新production-takeover-status.md；不挑入纯handoff提交亦可读取说明。

5ee08db独立QA增量：旧重复渲染/claim取消两项已转绿；新源身份HOLD：等长改1byte并恢复mtime_ns（ctime变化）旧grant仍放行。当前stat元数据不是内容hash。已退回MAKE最小修复，内容重验必须受许可，不能在GET/准入里无许可全文件hash。独立版本变化仍待报告；候选整体仍未通过。
