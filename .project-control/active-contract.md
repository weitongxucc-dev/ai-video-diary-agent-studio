# 当前执行范围与接续入口

## 1. 定位

产品、领域、架构、设计和身份边界沿[文档入口](../docs/README.md)读取；需求覆盖与验收样本见[开发清单](0.11-development-plan.md)。实现、交付和当前阻断以代码、Git/PR、CI及原任务回执为准。

## 2. 任务入口与未完成边界

| 持久任务 | 用户结果 |
| --- | --- |
| [#148：现有工具完整接入与结果闭环](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/148) | AI读取对象、执行已有动作、回查采用结果并撤销/恢复 |
| [#149：素材到文案、专业剪辑与持续精修](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/149) | 贯通素材证据、内容策划、语义剪辑、局部观察与多轮修订 |
| [#150：可编辑模板与程序化制作](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/150) | 模板参数/源码可修改、可运行、可采用、可撤销并跨任务复用 |

接续先回读对应Issue/PR及原请求，不根据任务列表重新实施。长会话压缩和HTML准备恢复的未完成证据继续沿[#150原回执](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/150#issuecomment-6791203405)处理；原Job、累计用量和失败保留。UI或文档检查不能替代其真实模型/媒体接续。

最小原生窗口、Windows及手机跨端的未验证范围须独立核实，不能用Mac默认窗口、窄面板或源码接线外推通过。清理文档不取消这些验收要求。

## 3. 写入归属与代码边界

当前仅主控拥有写入权，设计线程保持只读和桌面暂停，不恢复旧代理写权。实际工作区为 `/Users/Zhuanz/.codex/worktrees/ai-capability-work-packages/main`；接续先核对实时Git、相关diff与最新用户授权，不能按本文自动启动尚未完成的验收。

开发验收不进入产品Agent的日常任务指令；设计规则只由DESIGN拥有，保留文字尺度及功能可达性。

代码职责、单写和验证纪律遵循[AGENTS](../AGENTS.md)与[开发流程](../docs/development-workflow.md)，不在本合同复制第二份规则。

## 4. 数据保护与接手

保护 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/main` 的全部既有dirty/untracked，以及其他分支、用户原始上传包、素材、数据库和失败记录。文档删除只涉及已批准的仓库副本，不删除用户材料。

后续接续从[接手入口](takeover/README.md)、当前Git和对应Issue/PR读取范围、证据与下一步；任务完成后清除临时派工，历史由Git和原任务追溯。
