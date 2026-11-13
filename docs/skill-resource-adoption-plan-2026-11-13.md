# Skill 与剪辑资源采用计划（2026-11-13）

## 结论

当前产品只实际采用一个外部 Skill 方法文档，没有导入外部视频、图片、音乐、音效、字体或模型资源包。

外部 Skill 进入曜核时，只能作为固定版本的方法适配；它不能直接执行 GitHub 代码、访问网络、调用外部云服务或修改时间线。真正的编辑仍通过曜核已有业务工具、`EditProposal` 和 `TimelineVersion` 完成。

## 已采用

| 项目 | 来源与版本 | 许可证 | 进入产品的内容 | 没有进入产品的内容 |
| --- | --- | --- | --- | --- |
| `cut-as-code-video-cut` | [WhiteTowerAI/cut-as-code](https://github.com/WhiteTowerAI/cut-as-code)，`a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4` | 仓库和该方法文档为 MIT | 证据驱动的 keep/drop 判断、镜头与时间线复核方法 | 外部脚本、模型、字体、示例媒体、Pexels、云服务 |

该 Skill 的运行声明是 `method-only`、`runnable=false`、关闭 network/shell/arbitrary code；当前证据是 manifest 校验和工具契约测试，不代表真实媒体生产已经完成。

## 下一批候选

候选先做固定提交、许可证和输入输出审计，审计通过后最多选择一个进入适配。候选阶段不下载素材、不连接云服务、不复制第三方脚本。

| 优先级 | 项目 | 来源 | 预期用途 | 进入条件 |
| --- | --- | --- | --- | --- |
| A | `editing-videos` | [joshcirre/editing-videos](https://github.com/joshcirre/editing-videos) | 编辑判断方法：节奏、删减、B-roll、字幕、声音和复核 | 固定 commit；确认 MIT 覆盖范围；改写为只读方法文档；只调用曜核已有素材证据和编辑提案工具 |
| B | `ai-video-editing-skill` | [minhducxD/ai-video-editing-skill](https://github.com/minhducxD/ai-video-editing-skill) | 素材盘点、导出检查和媒体质量门禁 | 固定 commit；确认 MIT 与 helper 依赖边界；把检查结果映射到曜核的输出观察，不引入第二套事实源 |

## 只做研究，不进入当前运行时

| 项目 | 原因 |
| --- | --- |
| Rendley `video-editing-skill` | MIT 只覆盖 Skill 集成层；真正编辑依赖 Rendley 云端、账号和外部服务条款。 |
| OpenClip agent skills | 依赖托管 MCP、账号和外部网络，不能作为当前本地优先运行时。 |
| OpenFilm/OpenChatCut 整包资源 | 代码、模板、字体、示例媒体和音效的许可证边界没有逐文件确认；AGPL 项目不直接嵌入核心。 |
| Remotion skills、motion-canvas、HyperFrames | 可作为未来程序化动效研究对象；当前需要额外运行时、字体和媒体依赖，暂不并入核心。 |
| OpenTimelineIO | 可作为未来时间线交换格式候选；当前数据治理和 `TimelineVersion` 写入边界优先。 |

## 当前资源库实际内容

| 分类 | 当前状态 | 实际内容 |
| --- | --- | --- |
| 素材 | `connected` | 工作区用户素材、素材证据和检索入口 |
| 技能 | `connected` | 一个固定版本的 `cut-as-code-video-cut` 方法适配 |
| 贴纸 | `connected` | 曜核文本/贴纸叠加编辑能力；独立第三方贴纸包尚未导入 |
| 滤镜 | `planned` | 有画面效果执行代码，但没有独立 LUT 资源包 |
| 特效 | `connected` | 曜核参数化淡入、淡出、暖色调等效果 |
| 转场 | `connected` | 曜核叠化等转场实现 |
| 模板 | `connected` | 当前仓库内的 OpenFilm HTML/RenderComp 视觉模板及 provenance |
| 字幕/文本 | `connected` | ASR、字幕、文本叠加和编辑提案入口 |
| 配音 | `connected` | 工作区授权的版本化声音档案和配音入口 |
| 音乐 | `catalog_only` | 只有目录状态，没有随产品发布的曲库 |
| 音效 | `catalog_only` | 只有目录状态，没有随产品发布的音效包 |

当前不把 Pexels、Pixabay、Mixkit、Freesound、商业曲库、字体包、声音模型或示例媒体直接复制进产品。每一个未来资产都必须单独记录文件哈希、来源、repo license、asset license、font/model license、service terms、是否允许商用和导出。

## 当前施工顺序

1. 先完成数据治理真实闭环，让候选资源、采用、任务引用、渲染和输出观察能回读同一条关联链。
2. 再完成 Agent 的统一 Trace、provider usage、Token 状态和最小回放评测。
3. 最后审计上面的两个候选方法 Skill，最多适配一个；资源包暂不扩张，直到许可证和真实采用回执闭环。
