# 当前产品可用性收口合同

## 当前授权与共同基线

2026-11-09 用户明确要求：先把已确认范围的完整可用产品做出来，功能叠加以后讨论。差异化提案未获采纳，不实施 M1–M7。此前 main 同步授权继续有效；Windows 在用户另一台电脑开展适配，不由本机冒称已验。

共同基线：main d8d00ef919957e19316c6a4ef7798d06b0d381e8，源码与 c0a60fcf 等价；Pi 1.5 升级已由 PR #144 合入 main b27a9da10b51e3b7338b8477411087ee60fb7886。当前实施分支 codex/usable-product。本合同取代该基线中 10 月旧线程派工；历史从 Git 查询，不恢复旧线程。原 main 工作树的全部 dirty 和未跟踪设计原位保护，不复制或整体暂存。

## 用户结果与验收

按 docs/product-requirements.md 已确认范围交付，先消除阻断桌面创作的实际缺陷：启动和配置、真实素材输入、目标/定稿/已有作品三种入口、制作与可播放预览、自然语言与精确修改、撤销、输出及退出重启恢复。UI/HTTP/Agent 共用业务事实，保身份、版本、许可、原 Job 和未知结果查询。通过固定候选的相关自动检查，再做实际 Electron 与真实产物验证；安装包、目标设备和外部平台结果分别报告，不以合成媒体或 mock 替代。

当前 CI397 后端 247 条失败/错误及一个未完成分片为修复入口，须按根因区分夹具失配与真实业务缺陷，不删除有效断言、不改权限或资源阈值求绿。前端等未受影响证据复用。完成条件包括同候选可用流程和必需检查，不能以本合同、派工或一批测试完成交回。

## 本轮写入归属

主控：共享 schema、main 装配、conftest/共享 fixtures、文档、Git/推送/集成，及下表以外必要同根缺陷。所有共享修改串行；子任务需要共享变更先给具体调用链与建议。

| 子任务 | 独占可修改范围 | 要验证的行为 |
| --- | --- | --- |
| 组装 | apps/api/tests/test_montage_pipeline.py、test_render_pressure_recovery.py、test_production_foundation.py、test_timed_subtitle_render_input.py、test_image_render_contract.py、test_video_templates.py、test_duration_requirement.py；同根 production 变更先提给主控 | 真实组装、时钟/字幕、压力恢复及当前接口契约 |
| 制作执行（当前 normal 故事板准备） | 新增 app/montage_resource_preparation.py 及对应独立测试；main、schemas、既有准备协议、Agent 工具与 montage_stage_use_case 的必要接线由主控串行处理。此前助手15测试及 timeline_restore 源已交主控 | 补齐正常首启资源档无 montage 预算的确定断链；在原身份/任务范围中测量真实元数据规划，不采用作品，不借用 CPU/实验测量，不伪造 registration |
| 平台许可（候选只读审查） | 此前 CPU 首启 Web 六文件候选及平台/配音测试包已交主控；当前仅按主控消息只读审查，不再写入或执行 | 核身份、原请求、结算和派生结果边界；真实 GUI/媒体验证由主控串行执行 |

## 验证与外部边界

当前接续修复写权：组装代理独占 `creation/assembly_manifest.py`、`task_audio.py`、`audio_timeline.py` 和 PCM 输出时钟回归，将交付 PCM 生成绑定已完成 picture master 的实际帧时钟；主控拥有 `creation_execution.py` 与 `creation/audio_delivery_assembly.py` 的接线和严格合流校验。平台代理独占 `assistant/home_plan_http_adapter.py`、`conversation_repository.py` 及原片目标恢复回归，只对真实已提交原片事件恢复 failed 原回合的接续，普通窗口、停止、讨论及权限边界不放宽。此前包已冻结；模型、媒体、桌面及共享 Git 写仍仅主控串行执行。

原声接续按产品§4.3与领域§5.1补齐实际缺失的“源视频到编辑任务”入口。它属于已授权完整桌面闭环，不实施差异化或未来功能。使用显式 `Plan.source_media` 绑定真实资产修订、逻辑引用、原片区间及原声要求，Script/Narration保持真实空值；同请求创建 Plan/Task/Montage/Timeline 和回执必须原子、可回读，不伪造分析片段或旧配音成果。主控拥有共享类型、schema、API/Web/Agent接线与源模式执行适配；组装代理仅新增 `app/creation/source_task_use_case.py` 及对应独立测试。其余 CI 收口包与本输入模式无语义冲突，继续现有边界；新增包完成后先回读当前上述规范再集成。

可在本工作树使用现有已安装依赖，执行明确隔离数据库/临时文件/回环测试。先静态核对和必要局部复现；合成媒体与设备检查串行申请主控运行窗口，禁止代理并行启动重负载。全组 CI 由主控在相关修复集成后运行，保留首失败和完整结果。不得访问真实用户库、复用密钥跑不明外发、替换外部服务、发布视频/安装包、删除用户材料或调整付费/权限。本机模型和媒体实际验收由主控核明配置、输入、资源与既有授权后串行执行。确需新外部条件仅暂停对应部分，继续独立工作。

子任务报告实际修改、根因、原断言保护、验证命令/结果及缺口；不提交/推送、不派下级、不新建框架或扩大产品范围。主控审核精确 diff 后集成，保持远端 main 可供 Windows 获取已收口成果。
