# 当前执行范围与接续入口

本文件是短期施工入口，不拥有产品规则。产品定位、对象和界面分别以[文档入口](../docs/README.md)中的权威文档为准，完成状态以代码、GitHub Issue/PR、CI 和真实运行回执为准。

## 1. 当前产品结果

曜核的默认闭环是：用户表达目标，Agent 理解并选择当前动作，程序执行并保存真实结果，用户在同一作品中继续修改或交付，发布和数据反馈按需要接入下一轮创作。新建视频先打开持续 Conversation 与空作品区，统一参数表单只在缺少关键决定时作为检查器。

九类制作能力仍可用于恢复和依赖定位，但不再是用户导航、Agent 角色或固定顺序。任何施工任务都必须说明它改善了哪个用户结果、写入哪个事实源、如何从真实入口验收，以及旧入口或旧消费者何时退出。

## 2. 当前施工顺序

1. 收口集成分支和 PR，确认当前 head、CI、评审和工作树一致。
2. 在同一桌面应用和 userData 中完成设备资源准备，随后验收真实媒体提交、取消、失败、重试、回读和播放。
3. 按真实凭据逐一验证 Provider 回合、usage、停止和恢复。
4. 用桌面真实入口验收新建、对话、作品台、Session 切换、刷新恢复和跨任务隔离。
5. 按页面级问题清单重构作品台、素材库、人物库和声音库，不先批量修局部样式。
6. 实现同一作品的多 Session、多分支、候选比较和采用回滚。
7. 逐项吸收 ffmpeg-skill 与 CreatorHub 的可执行能力，保持未接通项明确不可用。
8. 在本地作品闭环稳定后，再做 Windows、手机、打包、发布和生产化验收。

## 3. 纵向切片与写入边界

| 切片 | 用户结果 | 主要写入范围 |
| --- | --- | --- |
| Agent-first 新建与持续作品台 | 自然语言启动创作，目标、引用和参数在对话中形成，右侧显示同一作品 | Web 入口、Conversation 接线、CreationTask/作品上下文 |
| Agent 事件与执行闭环 | 用户看到人话说明、真实动作、结果、失败和恢复，事件可持久化回读 | Pi runtime、助手宿主、SSE/Trace、对话时间线 |
| 素材与知识证据创作 | Agent 能基于有来源、版本和片段证据的内容选材、写稿和替换 | 资源查询、候选证据包、知识引用、采用记录 |
| 粗剪观察与局部精修 | 真实粗剪可播放，Agent 能观察并只重算受影响部分 | Media Execution Layer、TimelineVersion、观察和编辑用例 |
| 多分支与候选采用 | 同一作品可比较多个方向，未采用分支不覆盖正式版本 | Session/分支版本、候选、比较、采用和恢复 |
| 发布与反馈 | 一个真实平台完成作品版本到发布回查，再把反馈带入下一次创作 | 发布记录、平台内容编号、反馈观察和授权 |

每个切片必须复用现有业务用例和事实源，不能创建平行任务库、作品库、素材库或发布状态。业务执行不依赖路由或 UI，持久写入走所属事务入口。

对应的 GitHub 实施 Issue 为 [#183](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/183)、[#184](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/184)、[#185](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/185)、[#186](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/186)、[#187](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/187)、[#188](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/188)、[#189](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/189)、[#190](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/190)、[#191](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/191) 和 [#192](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/192)。Issue 负责实现追踪，Multica 只引用这些任务和证据，不另建一套产品规则。

## 4. 接手与保护

当前集成工作树为 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/wce-integration-audit`，主工作区 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/main` 存在用户已有 dirty/untracked，禁止 reset、restore、清理、覆盖或直接写入。不得删除用户素材、数据库、任务、产物和失败记录。文档删除只限仓库内已批准的过期规则或重复施工记录。

复杂协作使用 `$project-authority-harness`。主控维护共享契约和集成写权，执行线程只修改已登记的互斥文件并返回 changed files、commit、测试、持久化/UI/真实运行证据和阻塞项。真实设备、Provider、媒体、桌面/Pi、平台发布和最终成片必须分别验收，不能用静态测试或回放替代。
