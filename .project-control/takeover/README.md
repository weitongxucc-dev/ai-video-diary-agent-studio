# 0.11 接手入口

这是2026-09-27为Codex额度可能耗尽准备的交接包。当前主控仍为Codex原线程；准备交接不等于两个工具自动获得主控权。先读本页，再读当前工作清单，不要扫描所有历史聊天。

## 1. 当前权威与范围

- [完整范围与状态快照](scope-snapshot.md)：R01–R40、D01–D04全部保留。正式范围/依赖/验收唯一来源仍是[开发台账](../0.11-development-plan.md)第12–13节。
- [活动合同](../active-contract.md)：唯一文件所有权及外部操作边界；[AGENTS](../../AGENTS.md)和[文档路由](../../docs/README.md)按需加载。
- [设计](../../DESIGN.md)与[可运行v4原型](../../docs/design/workspace-dual-v4/README.md)为已确认入口。旧product-ui-v2图片已退役，不能恢复；v5未经确认不能替代v4。
- [当前工作清单](current-work.md)：快照有时间性，Git/实际进程/Linear优先。未跟踪handoff.md、bug-backlog和用户work目录保留，不当作最新授权。

## 2. 一个主控，两种模式

**目前：Codex主控，Pi和Claude Code各执行一个明确包。** 默认Pi界面/小修复，Claude Code跨文件工程，但任务难度按实际表现决定，不能凭工具名称认定能力。

**额度耗尽时的接手协议：** 用户在一个会话明确说“由你担任主控”；该会话先读取本包、核对Git与Linear、确认旧主控停止派单/集成，并在active-contract记录唯一主控和切换时间。未收到切换指令，执行者不自行争抢主控。新主控不同时实现自己待审核的包；两工具时可由一位主控只审阅/整合，另一位顺序实现。复杂权限/迁移仍按原验收门禁，不因换模型降低。

当前Claude会话曾报五小时额度上限；Pi会话上下文已接近满。接手先核额度和会话健康，必要时用户开新会话只贴下面入口，不复制百页历史。保留实际模型配置，不替用户购买/切换收费方案。

## 3. 接手前五步

1. 进入 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`；运行 `git status --short`、`git branch --show-current`、`git log -5 --oneline`、`git worktree list`。外接盘不存在就报告，不在内置盘另起同名工程。
2. 读取current-work和active-contract。逐个核相关worktree的dirty文件及候选SHA；工作者仍写入的文件不得接管、复制、reset或覆盖。先要求交付当前候选/停止写入，再转移所有权。
3. 用 `gh pr view 45 --json state,headRefName,headRefOid,statusCheckRollup` 核PR，`git fetch origin`只更新远端引用。不要把fetch变成merge/rebase。
4. 核Linear当前单；没有Linear工具时可用用户已登录Linear客户端/浏览器。若无法写入，记录本地待同步条目并告诉用户，不能声称已同步，不索要明文密钥。Codex MCP不会自动继承到Pi/Claude。
5. 每次只派一个有文件边界、基线、退出条件的包。先审核现成候选，再取下一包，防止再造已有代码。

## 4. 日常闭环与工程规则

- 工程入口：apps/web为界面；apps/api为业务及媒体执行；apps/desktop为桌面壳；supabase/migrations为PG迁移；共享层owner见活动合同。
- 独立干净checkout、新分支从审核基线起步；已有cmux两个checkout可复用，但先核dirty和前包集成。禁止两个工具写同一文件。
- 实施者交：issue、准确SHA、基线、改动文件、测试命令及结果、真实产物/界面证据、剩余项。只提交自己的明确文件；不git add全仓，不混入凭据/用户媒体/生成缓存。
- 主控审diff、依赖、权限/失败语义、必要专项复验，然后按具体SHA集成。冲突回owner，不凭猜测解决业务冲突。通过后推开发分支、补PR与Linear集成SHA。
- 开发分支推送已授权；main合并、真实库迁移、签名/公证、付费调用、真实平台发布不因此获得新授权。
- 测试按包运行，不反复全量。Web看package.json使用pnpm专项Vitest/tsc/ESLint；API用uv与仓库锁文件。Ruff如被用户全局配置干扰，诊断可用 `uvx 'ruff==0.15.7' check . --isolated --target-version py311`，不能改CI门禁掩盖失败。
- PR44已被squash合main；dev历史与main不同。PR45 followup分支是dev树的快照提交，不能盲目合两条历史或force push；新主控先核tree/diff，选择普通增量提交维护PR。

## 5. 验收与完成

单测、类型检查、PR合并、截图分别只是证据。完成必须是对应用户路径在同一候选真实工作：输入→授权→操作→持久状态/产物→失败恢复，且符合该条验收。

- 页面：v4对照、可点击、三尺寸1440×900/1180×800/1024×768。模拟数据明确标注。
- 媒体：真实输入输出、流/时长/音画/字幕检查及人工试听看片。不能用存在MP4替代质量验收。
- 权限：真实隔离数据库跨身份正负例；mock不能证明RLS/并发事务。
- 调度：真实子进程、取消/超时、释放后才让出许可；未知资源拒绝，不偷偷启用scheduled。
- Linear：候选交付先In Review；主控审核并核集成证据后才Done。子单Done不关闭父范围；未验证写明缺口。

## 6. 可复制的启动语

> 接手曜核0.11。先读 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25/.project-control/takeover/README.md` 和 current-work.md。核Git、dirty、Linear和活动合同，不按旧快照覆盖文件。保持0.11正式范围，0.12手机与待决定项分开。你当前先做接手核验；只有我明确指定你为唯一主控后，才派单、整合并更新共享规则。先报告当前候选、阻塞与下一件可执行工作，不重写项目。

新主控给执行者的包必须包含：issue/结果/工作目录与分支/基线/独占文件/禁止文件/所需权威章节/真实验收/外部操作边界/报告位置。不要让执行者从全部issue自行猜。
