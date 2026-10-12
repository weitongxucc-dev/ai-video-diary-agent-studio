# 文档权威与路由

本目录只保留长期有效的权威文档。Agent 和开发者必须按任务逐步加载，不默认读取整个 `docs/`、README、CHANGELOG 或 Git 历史。

## 权威地图

| 领域 | 唯一权威 | 读取触发条件 |
| --- | --- | --- |
| Agent 工作纪律、渐进加载和任务分级 | [`../AGENTS.md`](../AGENTS.md) | 每个 Agent 任务自动生效 |
| 产品定位、用户结果、能力边界和端到端验收 | [`product-requirements.md`](product-requirements.md) | 产品范围或完成判断发生变化 |
| 业务名词、对象关系、状态机和转换语义 | [`domain-model.md`](domain-model.md) | 数据结构、API 契约或工作流状态发生变化 |
| 模块责任、跨进程数据流、模型与平台适配 | [`system-architecture.md`](system-architecture.md) | 变更跨越两个以上运行模块或外部服务 |
| UI 信息架构、三栏职责、功能命名和视觉系统 | [`../DESIGN.md`](../DESIGN.md) | UI、产品文案、组件或截图验收 |
| 任务拆分、并行代理、审核、验证和文档治理 | [`development-workflow.md`](development-workflow.md) | 复杂、高风险或跨模块任务 |
| 账号、首启和 workspace 安全不变量 | [`auth-workspace-boundary.md`](auth-workspace-boundary.md) | 认证、授权或数据隔离变更 |
| Electron 开发与运行结构 | [`../apps/desktop/README.md`](../apps/desktop/README.md) | 桌面调试、进程或打包实现 |
| 构建、签名、公证和分发 | [`../apps/desktop/RELEASE.md`](../apps/desktop/RELEASE.md) 与实际 workflow | 生成或交付安装包 |

## 有效文档与隔离区

当前事实按用途查询：产品规则看上表；当前写入范围看活动合同；需求覆盖看开发台账；交付状态看 GitHub 对应 Issue、Git/PR 与同提交 CI。Linear 仅保留历史记录，新工作按开发计划第13节进入 GitHub Issues。README 不复制动态状态，活动合同直接改写现行规则，不用末尾追加文字覆盖前面的旧命令。

`work/`、`output/`、设计原型、旧交接和未跟踪记录均不是默认启动输入。仅按当前任务给出的精确路径读取；不得把其中的历史基线、临时HOLD或旧授权重新激活。

- 上表及其直接链接的长期项目文档构成当前有效权威；`.project-control/`下合同和开发计划仅是短期执行材料，不是产品、领域或架构的第二份权威。`docs/design/` 只保存由 `DESIGN.md` 解释的视觉参考资产，不独立定义产品规则。
- `docs/archive/` 是非当前资料的隔离区，不属于渐进加载路径。除非任务明确要求历史追溯，Agent 搜索、读取和 Authority Snapshot 都必须排除该目录。
- 普通旧版本默认从活动目录删除，由 Git 历史追溯。只有外部签收、法律合规、重要迁移或无法从 Git 重建的证据可以进入隔离区，具体规则见 [`archive/README.md`](archive/README.md)。

## 常见加载组合

- 小型代码修复：目标代码和相邻测试；出现契约疑问时才补读一份领域权威。
- 新产品能力：`product-requirements.md` 的目标章节；涉及对象状态时补读 `domain-model.md`。
- 0.11开发：工作包、依赖、技术候选和验收样本读 [`../.project-control/0.11-development-plan.md`](../.project-control/0.11-development-plan.md)；Pi 自主编程、工具扩展、细粒度编辑与外部 CLI 方案读其中[第4.2节](../.project-control/0.11-development-plan.md#42-2026-10-12pi-开放能力脚本扩展与外部-agent-接入方案)。计划不表示功能已实现，也不自动授权实施；当前是否可开始工作由活动合同及最新用户指令决定。
- 下一阶段规划：长期优先级读 [`product-requirements.md`](product-requirements.md#151-制作质量修改效率与复用的优先级)；本轮工作包和剩余项读 [`../.project-control/active-contract.md`](../.project-control/active-contract.md)。后者是短期执行入口，不是第二份产品权威；合同结束删除时同步移除此链接。
- UI 页面：先定位 `DESIGN.md` 的目标页面与受影响规则，并按需查看[当前参考资产入口](../DESIGN.md#510-当前视觉与交互基线)；原型只供构图与交互对照。应用 design-md Skill 时按其要求补读，同一任务不重复读取未变内容。只有范围不明确时才读产品需求。
- 跨 Web/API/Worker：`system-architecture.md` 的目标边界和直接相关代码，不读全部产品文档。
- 认证或发布：读取各自技术权威和实际实现；不要用产品文档推断当前运行状态。
- 文档治理：本文件和目标文档；只有发现跨域冲突时才加载第二份权威。

## 文档生命周期

1. 一条规则只能由一份文档拥有。其他文件使用链接和一句上下文，不复制正文。
2. 产品文档描述稳定目标和不变量，不记录“已完成百分比”、临时缺口、测试次数或某个构建包状态。
3. 代码、配置、Git、CI、Release 和实时服务分别证明实现与运行状态；README 只做人类开发入口，CHANGELOG 只记录已发布变化。
4. 新增文档前先确认现有权威无法容纳该规则。没有独立读者、生命周期和责任边界时，不新增文件。
5. 规则被替代时，同一次权威变更必须更新新规则、删除活动文档中的旧表述并修复引用；不能把新旧规则并排保留给 Agent 自行判断。
6. 被整体替代且没有独立保留价值的文档直接删除，由 Git 记录历史；确需留存的文件移入 `docs/archive/`，并标注当前权威、归档原因、日期和默认禁止 Agent 读取。
7. 临时计划、Agent handoff、审查快照和运行日志不进入活动文档或隔离归档；任务完成后删除，或留在 Issue、PR、Actions 和 Release。

## 权威变更事务

通过 [`development-workflow.md`](development-workflow.md#11-act-动态权威变更协议) 路由到治理 Skill 的 Change 模式；本文件不复制分级、消息或批准流程。项目特有约束在该节维护，通用事务只在 Skill 维护；局部实现修正不触发权威事务。

## 文档变更验收

- 相对链接可以解析，删除文件没有残留引用。
- 同一个术语在产品、设计、领域和代码中含义一致。
- 没有把实现细节提升为产品承诺，也没有把产品范围藏在实现 README 中。
- 新规则有唯一归属；没有为了“更保险”复制到多个文件。
- 活动文档不引用已删除的旧权威，也不把 `docs/archive/` 内容作为当前决策依据。
- 权威变更影响的活动任务已收到新版本，并完成重新绑定或明确终止。
