# Skill 与剪辑资源采用计划（2026-11-13）

## 结论

当前产品已经采用 OpenFilm 的一批文本/视觉模板，但还没有把 OpenFilm 的完整资源库和可复用 Skill 体系接入到资源面板。后续按“能直接使用就直接接入”的策略推进：成熟项目的现成资源、模板、字体、音频和方法都可以进入小范围产品运行时；保留来源、版本和文件哈希只是为了回滚、复现和排查，不再把它当成采用阻断条件。

外部 Skill 进入曜核时，默认适配为曜核的受控 Skill：它可以提供方法、模板参数和资源索引，但不能绕过曜核的 Agent 工具、`EditProposal`、`TimelineVersion`、授权和输出观察。真正的编辑仍通过曜核已有业务工具完成。

## 已采用

| 项目 | 来源与版本 | 许可证 | 进入产品的内容 | 没有进入产品的内容 |
| --- | --- | --- | --- | --- |
| `cut-as-code-video-cut` | [WhiteTowerAI/cut-as-code](https://github.com/WhiteTowerAI/cut-as-code)，`a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4` | 仓库和该方法文档为 MIT | 证据驱动的 keep/drop 判断、镜头与时间线复核方法 | 外部脚本、模型、字体、示例媒体、Pexels、云服务 |
| `openfilm-replicate` | [openfilm/skill-replicate](https://github.com/openfilm/skill-replicate)，`eef1ee5eda2a2aaeec60a3f133d73b86dd53c053` | MIT | 参考视频的镜头、节奏、构图与输出观察方法适配 | OpenFilm CLI、测量脚本、渲染运行时、外部服务和参考媒体 |

| OpenFilm 文本/视觉模板 | [openfilm/openfilm](https://github.com/openfilm/openfilm)，曜核已落地的固定 revision `758b1a3888819bf191db191f690c4ca2fcb292af` | OpenFilm 库声明 CC0 | 标题、下三分之一、标注、新闻条、社交引导、章节、片尾等可编辑 HTML/JSON 模板 | OpenFilm Studio、CLI 和第二套时间线运行时 |

| OpenFilm 社区资源 | [openfilm/library](https://github.com/openfilm/library)，当前接入 revision `19b26b60c1286b412c01ad64971263ae0e2aef13` | 仓库声明 CC0 | 音效、音乐、CSS 转场/特效及其预览和 item manifest | OpenFilm 云端下载器；资源复制到曜核本地资源目录并走现有采用回执 |

| OpenFilm Replicate Skill | [openfilm/skill-replicate](https://github.com/openfilm/skill-replicate)，`eef1ee5eda2a2aaeec60a3f133d73b86dd53c053` | MIT | `study → render → compare → look` 的参考视频分析与受控复核方法 | OpenFilm 自己的 CLI、服务账号和第二套生产事实源 |

该 Skill 的运行声明是 `method-only`、`runnable=false`、关闭 network/shell/arbitrary code；当前证据是 manifest 校验和工具契约测试，不代表真实媒体生产已经完成。

## 下一批候选

候选先固定提交并做输入输出适配；不再因为许可证审计而阻止小范围采用。候选阶段仍不直接嵌入外部编辑器运行时。

| 优先级 | 项目 | 来源 | 预期用途 | 进入条件 |
| --- | --- | --- | --- | --- |
| A | `editing-videos` | [joshcirre/editing-videos](https://github.com/joshcirre/editing-videos) | 编辑判断方法：节奏、删减、B-roll、字幕、声音和复核 | 固定 commit 后直接改写为曜核方法 Skill；只调用曜核已有素材证据和编辑提案工具 |
| B | `ai-video-editing-skill` | [minhducxD/ai-video-editing-skill](https://github.com/minhducxD/ai-video-editing-skill) | 素材盘点、导出检查和媒体质量门禁 | 固定 commit 后直接映射到曜核输出观察；不引入第二套事实源 |

## 只做研究，不进入当前运行时

| 项目 | 原因 |
| --- | --- |
| Rendley `video-editing-skill` | MIT 只覆盖 Skill 集成层；真正编辑依赖 Rendley 云端、账号和外部服务条款。 |
| OpenClip agent skills | 依赖托管 MCP、账号和外部网络，不能作为当前本地优先运行时。 |
| OpenFilm/OpenChatCut 整包运行时 | 不直接嵌入第二套编辑器运行时；其中的模板、资源和 Skill 可以逐类复制并接入曜核自己的资源与治理链路。 |
| Remotion skills、motion-canvas、HyperFrames | 可作为未来程序化动效研究对象；当前需要额外运行时、字体和媒体依赖，暂不并入核心。 |
| OpenTimelineIO | 可作为未来时间线交换格式候选；当前数据治理和 `TimelineVersion` 写入边界优先。 |

## 其他成熟项目的吸收方式

我们可以直接采用成熟项目已经做好的内容，按“资源直接入库、方法改成曜核 Skill、产品运行时做适配”三种方式吸收：

| 来源 | 直接吸收 | 转成曜核能力 | 暂不整包搬入的部分 |
| --- | --- | --- | --- |
| [OpenFilm](https://github.com/openfilm/openfilm) / [library](https://github.com/openfilm/library) | 模板、音效、音乐、贴纸、滤镜、特效、转场和预览文件 | `openfilm-replicate`、资源 manifest、素材预览和 `EditProposal` 入口 | OpenFilm Studio/CLI 自己的编辑器运行时 |
| [OpenFilm skills](https://github.com/openfilm/skills) | Skill 索引、Skill brief 和评测写法 | 固定 revision 的方法 Skill、输入输出声明和 Trace/回放评测 | OpenFilm 的安装器和第二套 Agent 循环 |
| [OpenChatCut](https://github.com/0xsline/OpenChatCut) | 多轨剪辑、字幕驱动剪辑、动效/转场/LUT、主体安全区、导出和 MCP 的成熟交互与流程 | 映射为曜核的资源类型、编辑提案、Trace 事件、输出观察和 CLI/手机端契约 | 整套 Electron/Remotion/MCP 编辑器运行时 |

因此“采用”并不等于只能把整个产品复制过来。我们会优先把已经能直接工作的模板、音频、视觉效果和 Skill 放进资源库；对于需要接入曜核时间线的数据结构或渲染引擎的部分，再写最小适配器，把成熟能力接到曜核已有的治理和回滚路径上。

## 当前资源库实际内容

| 分类 | 当前状态 | 实际内容 |
| --- | --- | --- |
| 素材 | `connected` | 工作区用户素材、素材证据和检索入口 |
| 技能 | `connected` | 固定版本的 `cut-as-code-video-cut` 与 `openfilm-replicate` 方法适配 |
| 贴纸 | `connected` | 曜核文本/贴纸叠加编辑能力；独立第三方贴纸包尚未导入 |
| 滤镜 | `planned` | 有画面效果执行代码，但没有独立 LUT 资源包 |
| 特效 | `connected` | 曜核参数化淡入、淡出、暖色调等效果 |
| 转场 | `connected` | 曜核叠化等转场实现 |
| 模板 | `connected` | 当前仓库内的 OpenFilm HTML/RenderComp 视觉模板及 provenance |
| 字幕/文本 | `connected` | ASR、字幕、文本叠加和编辑提案入口 |
| 配音 | `connected` | 工作区授权的版本化声音档案和配音入口 |
| 音乐 | `catalog_only` | 只有目录状态，没有随产品发布的曲库 |
| 音效 | `connected` | 已接入 OpenFilm `Glass Pulse`（本地 m4a、预览路由和固定 provenance）；更多音效继续按同一目录接入 |

当前第一批直接复制到产品的 OpenFilm 社区资源：`openfilm:sound-glass-pulse` 已可预览并进入音频编辑入口；`openfilm:transition-glitch` 已进入资源目录并可预览，因曜核当前还没有 CSS 转场写入适配，暂标 `catalog_only`，不会伪装成已可采用。

当前不把外部服务的下载器和第二套生产运行时直接接入产品。具体媒体、字体、声音模型和示例媒体可以直接纳入小范围产品使用；进入时记录来源、固定版本和文件哈希，便于资源面板、回滚和真实输出排查。

## 当前施工顺序

1. 先完成数据治理真实闭环，让候选资源、采用、任务引用、渲染和输出观察能回读同一条关联链。
2. 再完成 Agent 的统一 Trace、provider usage、Token 状态和最小回放评测。
3. 立即接入 OpenFilm 的资源库和 Replicate Skill，再把 `editing-videos`、`ai-video-editing-skill` 逐个适配到同一套工具和回执。
