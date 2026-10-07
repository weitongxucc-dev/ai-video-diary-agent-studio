# 0.11 暂停交接（2026-10-07）

用户决定本项目改由其他软件开发，cmux 多执行者 + 主控模式全线暂停。本文是暂停时点的现场快照与新工具进场指引；不是新施工授权。任务状态与完整判据以 Linear 为准。

## 目录归一化（2026-10-07 HOLD_FOR_AUTHORITY_UPDATE MAIN-LOCAL-20261007）

- **本地 main 主入口改为 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`**（独立 clone GitHub main，基线 `584ccc3`）。后续以该目录为准。
- 仍保留（进程占用/未合工作）：`original`、`bd25`（权威文档与集成分支）、`wil108-clip-inspection`、`wil110-knowledge-grants`。
- 已归档移除的 7 个闲置目录：`ci-font-portability`、`ci-home-contract`、`controller-handoff-20261004`、`wil110-read-permissions`、`wil110-upload-acceptance`、`wil119-quality-detection`、`wil188-audio-repair`。主控侧对其无活跃依赖（wil119 仅只读过冻结契约，验证已完成且内容在 main `584ccc3` 中）。归档为完整可恢复形式。
- 本次仅为目录归一化：**不恢复暂停，不授予新开发/合并/发布权**。

## 远端与分支地图

- 权威远端：**GitHub origin** `https://github.com/weitongxucc-dev/ai-video-diary-agent-studio.git`。gitee 为陈旧镜像（落后 500+ 提交），不要从它恢复。
- `main` @ `584ccc3`：稳定主线（WIL-119 成片质量检出 PR #89 已合）；本地主入口见上节 `repositories/main`。
- `codex/wil110-knowledge-grants` @ `8fc402a`：WIL-110 知识授予/撤销完整实现（API 137 / Web 23 通过，same_workspace 诚实标注落地；4 个 WIL-159 文件未同步属已知中间态）。**未合 main，独立保留**。
- `codex/wil108-clip-inspection` @ `20df87f`：WIL-108 成片质量检查仓库层 + 隔离测试（11 单测通过）。**main.py/schemas.py 接线未完成**——接线半成品已确认存于工作树 stash `stash@{0}: wil108-wiring-wip-paused-20261007`（已核实存在，工作树干净、HEAD=20df87f 与远端一致；未推）。
- `codex/0.11-development`：集成分支，含最新文档与本交接。

## 中断点：WIL-108 接线

接什么（Linear WIL-108 评论有完整描述）：
1. main.py 全局注册 `quality_check_repo = QualityCheckRepository(settings.database_path)`（与 montage_repo 同款）；
2. 渲染完成回调触发 `start_final_video_quality_check(workspace_id, task_id, quality_check_repo, artifact_store, plan)`；
3. 成片响应接 `final_quality_check_payload(record, plan, current_render_ref=...)`；
4. schemas.py 补三态质量字段（issues_found / clean / unavailable），unavailable 绝不显示成「无问题」。

只消费 WIL-119 冻结契约（`apps/api/app/quality_checks.py`，四类 kind：black_frame / truncation / clipping / timestamp_offset），不重写检测算法。

## 待验收队列（差真实页面 GUI 证据才 Done）

WIL-100、128、157、159、165、199、110 均 In Review；108 接线完成后一并进入验收。GUI 验收约束：仅用户桌面 Chrome；指定请求头策略加载失败不重试不绕过；重型/跨根环境串行。

## 仍然有效的红线（新工具同样适用）

- 不碰真实用户库、密钥、未授权模型下载、收费、平台账号/发布/Release。
- 不自动合 PR；main 只走 PR。
- 跨 workspace 端到端保持 **UNVERIFIED** 结案（用户已裁定）：不激活真实凭据、不造事实源、不在 LocalMember 持久层伪造空间绑定。
- 不编造百分比/倒计时/测试通过；无依据不做防御性编程。
- 定时 automation 与 scheduled 生产保持 OFF。

## 权威文档指引

- 合同与权限：`.project-control/active-contract.md`；执行会话身份：`.project-control/executor-handoff.md`
- 产品/领域：`docs/product-requirements.md`、`docs/domain-model.md`
- 任务卡：Linear team **Wiliiam**，项目「曜核 0.11｜桌面端完整闭环」（P-WIL-1）
