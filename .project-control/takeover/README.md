# 接手入口

当前接续任务、顺序与写入归属以[活动合同](../active-contract.md)为准；UI 收口沿当前分支与对应 PR 接续，AI 能力包的范围、依赖和原回执分别读 [#148](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/148)、[#149](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/149)、[#150](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/150)。任务登记不代表实现、验收或交付完成。

先读[活动合同](../active-contract.md)确认唯一主控、任务范围及写入所有权，再核当前 checkout、Git HEAD、相关 dirty diff 和服务事实；按[文档路由](../../docs/README.md)只加载任务涉及的权威。

旧线程、旧工作树和历史交接不授予写权；不得为接手自动 merge、rebase、reset 或恢复旧任务。当前合同有未完成验收时保留其证据边界，不用历史源码/测试通过替代真实产品接受。

先从当前任务对应 Issue 或 PR 的候选、证据及下一步继续，只检查本次差额；不重读整段聊天，不为换线程重复调研或重跑未受影响的检查。完整能力包集中验证，真实失败才提前定向复现。当前只有本轮主控写入，旧代理租约不恢复；新线程接手须显式交接，不能与原主控同时写同一文件。

已关闭批次的交接、Linear快照与审查副本不作为启动上下文；本目录旧日期文件仅供明确的历史追溯，不恢复旧基线、暂停或派工。新增交接仅保留当前合同与 Issue 无法表达的必要信息，任务结束后及时删除。
