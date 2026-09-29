# 0.11 主控接手入口

当前唯一主控、现场执行者、文件归属、暂停与外部操作边界见 [活动合同](../active-contract.md)。先核实际 Git、cmux 与 Linear，再按 [AGENTS](../../AGENTS.md) 和 [文档路由](../../docs/README.md) 加载任务所需规则。

2026-09-29 已由 cmux Claude 书面交接给新 Codex 主控。回执准确路径见活动合同；旧 Codex 线程不恢复派单，cmux Claude 不再全局派单或集成。后续主控变更只在活动合同登记，不在本入口维护第二份归属表。

本目录其余 current-work、scope-snapshot、linear-snapshot 和 evidence 是 2026-09-27 的交接快照，不是当前任务列表、HOLD、批准或完成证据，默认不加载。历史候选 SHA 不得用于回退工作树或自动重新派工。当前合同已退役的批次登记由 Git/Linear 追溯。

集成目录以活动合同为准，不能因为保存的项目入口指向另一 checkout 而在那里修改或合并。保护既有未提交和未跟踪内容；不为交接做 merge/rebase/reset。当前单测、界面、媒体、桌面包和产品验收分别核实，旧 CI 绿灯不证明新候选通过。
