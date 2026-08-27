# 历史资料隔离区

状态：非当前权威。除非任务明确要求历史追溯，Agent 不得搜索、读取、引用或从本目录恢复产品与工程规则。

## 允许进入的内容

- 法律、合规或外部签收材料；
- 无法从 Git 重建的原始证据；
- 必须解释一次重大迁移或不可逆产品决策的历史材料。

普通旧版本、阶段计划、Agent handoff、审查快照、测试日志和完成清单不得归档：可由 Git 追溯的直接删除，临时状态留在任务、PR、Actions 或 Release。

## 归档要求

文件按年份放置在 `docs/archive/YYYY/`，并在开头标注：

    status: superseded
    current_authority: ../../当前权威.md
    archived_at: YYYY-MM-DD
    reason: 保留原因
    agent_default_access: forbidden

归档时必须同时：

1. 从活动文档和 Authority Snapshot 中移除旧文件；
2. 修复旧路径和旧术语的活动引用；
3. 确认当前规则已进入唯一权威；
4. 不在本目录维护另一套“当前索引”或持续更新旧内容。

本文件只定义隔离边界。活动文档生命周期和权威变更由 [`../README.md`](../README.md) 与 [`../development-workflow.md`](../development-workflow.md) 负责。
