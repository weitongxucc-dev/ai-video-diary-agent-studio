# 外部编辑 Skill 候选与许可结论

盘点日期：2026-11-13。候选只作为研究或本地方法适配；没有把外部运行时、脚本、网络服务或素材带入曜核。

| 候选 | 固定来源 | 仓库/Skill许可 | 资源、模型与服务边界 | 结论 |
| --- | --- | --- | --- | --- |
| [WhiteTowerAI/cut-as-code](https://github.com/WhiteTowerAI/cut-as-code) `/skills/video-cut` | `a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4` | 仓库 MIT；该 Skill 文档随仓库 MIT | 原 Skill 还依赖 FFmpeg、Python/faster-whisper、下载器及可选 Pexels；脚本、模型、素材和服务条款不由 MIT 覆盖 | **接入方法适配**：只保留证据驱动的 keep/drop 决策，调用曜核已有业务工具 |
| [rendleyhq/video-editing-skill](https://github.com/rendleyhq/video-editing-skill) | `main` 在盘点时可读；仓库已于 2026-11-08 归档 | MIT | 关键编辑逻辑在 Rendley 服务；MCP/REST、上传、项目链接和服务条款都要求外部网络与账户 | **研究，不接入** |
| [pvhuwung/dolly](https://github.com/pvhuwung/dolly) | `main` 在盘点时可读 | 仓库代码/文档 MIT；示例录屏与 logo 属 Termary；字体 OFL、图标 ISC | 依赖 Remotion 自有许可；录屏需 macOS 权限/observee；示例媒体不能复用 | **研究，不接入** |

## 许可判断

- MIT/Apache-2.0/CC0 只说明相应仓库中被覆盖的代码或文档可按该许可使用；不能推导仓库内字体、示例媒体、模型、第三方包或云服务可自由使用。
- 本次适配没有复制外部脚本、模型、字体、示例媒体或服务调用，只留下带来源修订的工作方法说明。
- `video-cut` 原文要求下载、转录、FFmpeg 渲染和自检；曜核适配明确禁止这些外部执行路径，改用 `read_material_evidence`、`read_task_observations`、`preview_typed_edit`、`apply_typed_edit` 等已有业务工具。导入不等于真实媒体验收。
