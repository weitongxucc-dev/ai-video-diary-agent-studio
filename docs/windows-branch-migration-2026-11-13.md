# Windows 分支集成记录

## 已集成范围（2026-11-17）

Windows 继续只使用 `codex/windows-integration-1.5.2`。本次将主线 `54da85b1` 的已验证功能及 `738a06d8` 打包修复合入，集成提交为 `c0640f2c`、`9cb6876e`；后续文档同步沿同一分支完成，实际最新提交查 Git。本地主线两处 settings dirty 未纳入，两分钟人工通过的任务与运行数据未改动。

| 同步内容 | 结果 |
| --- | --- |
| 数据治理 | CandidateBundle、知识claims、人物/声音反馈、跨域候选查询与固定版本回读进入Windows代码 |
| Pi Agent | Trace/Token账本、资源导入/采用/回查/指定撤销及保存回执恢复同步；Windows Job Object、npm shim与隔离环境保留 |
| 资源与制作 | 背景音乐混音、音量编辑、OpenFilm资源、Fluent静态贴纸、资源抽屉与版本化输出同步 |
| CPU运行 | 主线线程预算与真实任务准备修复同步；Windows文件锁、凭据存储和进程回收适配保留 |
| 桌面打包 | 补齐Electron中的Skill JSON清单及构建身份；PyInstaller包含资源文件与API目录使用的Skill清单，避免源码可用但安装包缺文件 |

采用普通合并，保留两边历史；14个冲突文件是重复迁移后落后的公共功能，按主线已验版本解决。原本地 `8839103d` 与主线ledger实现等价，历史保留。没有强推，没有把尚待真机验收的Windows专用实现反向合入main。

## 本次验证边界

- Windows集成worktree运行Python定向验证：**36 passed、10 skipped**。覆盖候选查询、资源目录/贴纸采用、保存后中断恢复、打包资源布局，以及平台文件/环境；跳过项为当前macOS不能执行的Windows凭据和Job Object等测试。
- 桌面脚本：真实electron-builder过滤复制后加载三个Skill、构建身份、配置、standalone资源物化均通过。
- main打包布局测试2项通过，构建身份与Skill过滤加载通过。未生成安装包，未发布Release；不是全量CI通过声明。
- 当前候选Windows真机安装、启动退出恢复、真实模型、媒体和设备播放仍未验收。历史安装包证据不代替本次候选。

## 当前接手基线（2026-11-18）

远端 Windows 分支为 `241ea35c`，远端 main 为 `8d229ed0`，共同祖先为 `f3d7a443`。主线其后的 `cd19ec70`（回复保留与新安装任务工具）及 `8d229ed0`（已保存编辑后的制作失败回报）尚未进入 Windows 分支。主工作区未提交的单页、字幕和设备准备改动另行集成。同步时重新比较实际提交，保留 Windows 独有历史和当地改动。

## Windows电脑接续

1. 在现有Windows checkout先检查 `git status` 并保留当地修改，然后fetch并继续 `codex/windows-integration-1.5.2`；干净且无分叉时使用 `git pull --ff-only`。有本地独有提交则先核对合并，不使用reset或强推覆盖。
2. 按该分支的 `apps/desktop/README.md` 安装依赖、构建Windows preview包（`pnpm --filter ai-video-diary-studio build:win`）。Pi与Node仍是外部运行依赖，不因同步自动安装。
3. 验证安装/启动API与Web、凭据持久化、Pi真实对话及停止后子进程回收；检查Skill目录、贴纸和音频预览，正式采用并出片、播放、撤销。
4. CPU准备按Windows设备实际能力生成；不要复制Mac设备proof、锁、资源档、数据库或密钥。

现有 Windows 线程从首批起并行。先在目标电脑记录 Windows 版本、CPU、RAM、GPU/显存、驱动及实际可运行的模型后端；分别检查离线素材解析、中英指定声音和授权人物口型短样。能力未就绪时区分安装与依赖缺失、实现缺口、硬件实测不达标及未取得设备四种情况，给出对应功能的可用范围。

共享候选合入后，按[开发清单 §1.1.3](../.project-control/0.11-development-plan.md#113-资源与平台验收)在 Windows 原生安装版检查同一作品页的阶段与局部修改、查询无写入、停止与重开恢复；核对资源随包、贴片与 Skill 正式出片。最终用同一候选逐版播放中英成片，实听指定声音、查看人物口型，并确认仅改英文时中文产物保持。回执交回主控时附分支 SHA、安装包标识、设备配置、实际输入输出和仍未通过项。跨机器仅共享经授权的验收样本。

Windows发现的通用业务修复回归main；专用适配保留在本分支，获得本平台证据后再决定反向合入。后续共用功能从main同步，不再为每个功能另开Windows分支。

## 已保留的历史迁移

`7b001dd9` 平台文件/进程/凭据、`f9bb01f7` 预览安装器、`74bf0381` 打包硬化、`80b31e3a` 可移植开发入口均保留。Draft PR #146已关闭、旧 `windows/local-model-validation` 分支已退役；旧迁移细节查Git历史，不恢复旧开发线。
