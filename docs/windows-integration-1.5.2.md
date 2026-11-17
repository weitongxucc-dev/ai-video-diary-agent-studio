# Windows 集成分支 1.5.2

本分支是唯一Windows开发线：`codex/windows-integration-1.5.2`。共用功能从main合入，Windows平台适配和未完成的本机验收继续留在本分支。

2026-11-17已同步主线数据治理、Agent恢复、Skill与资源制作能力，并补齐安装包Skill清单与资源文件。同步基线、合并验证和Windows电脑接续统一见[Windows集成记录](windows-branch-migration-2026-11-13.md)，不在此维护第二份进度。

Windows安装器、NSIS、API onedir与standalone资源物化代码已存在；此前“下一批再迁移安装器”的描述已失效。当前候选尚未取得Windows真机安装、Pi对话/停止、真实出片及播放证据，不能因代码合并或Mac测试通过而标为完成。

构建入口和平台依赖见[桌面说明](../apps/desktop/README.md)。旧迁移门槛及提交由Git历史追溯。
