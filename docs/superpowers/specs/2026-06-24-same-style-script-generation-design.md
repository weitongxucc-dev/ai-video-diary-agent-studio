# 同款脚本生成 / 一键追爆工作台设计

## 目标

在 AI 视频日记中新增“同款脚本生成”模块。它不是单纯换脸，也不是只生成一段脚本，而是从对标视频链接出发，拆解爆款视频的文案、节奏、镜头和发布包装，再替换成自有产品、人设、声音、数字人素材，形成可剪辑、可发布的新视频资产。

## 当前实现

本轮实现的是可运行的第一版工作台和后端契约：

1. 左侧导航新增“同款脚本生成”，路由为 `/same-style-script`。
2. 页面升级为“一键追爆智能体”一屏工作台，包含文案生成、音频生成、视频生成、字幕生成、背景音乐生成、标题生成、封面制作、视频发布八个区块。
3. 后端新增 `GET /api/same-style/workflow`，返回每个区块的模块名、引擎、输入、输出、主操作和当前可用状态。
4. 前端读取后端契约显示真实能力状态；API 不可用时显示离线契约，不伪装外部服务已接入。
5. 后端新增 `POST /api/same-style/reference-jobs`，用于创建对标视频参考导入任务。
6. 文案生成表单已经接入真实 Server Action，提交后会调用参考导入接口并在页面展示任务结果。
7. 后端新增 `GET /api/same-style/reference-jobs/{job_id}`，用于读取已保存的 reference manifest、转写文本和同款脚本草案。
8. 后端新增 `POST /api/same-style/video-jobs` 与 `GET /api/same-style/video-jobs/{job_id}`，用于基于宫格参考图提交 Seedance / 图生视频任务。
9. 后端新增 `POST /api/same-style/audio-jobs` 与 `GET /api/same-style/audio-jobs/{job_id}`，用于把同款脚本草案提交到 CosyVoice / TTS 任务。
10. 后端新增 `POST /api/same-style/digital-human-jobs` 与 `GET /api/same-style/digital-human-jobs/{job_id}`，用于把 CosyVoice 音频提交到 HeyGem/Duix 数字人口播视频任务。
11. 后端新增 `POST /api/same-style/subtitle-jobs` 与 `GET /api/same-style/subtitle-jobs/{job_id}`，用于从同款口播文本或仿写脚本生成 `subtitle.srt`。
12. 后端新增 `POST /api/same-style/bgm-jobs` 与 `GET /api/same-style/bgm-jobs/{job_id}`，用于跳过或混入本地 BGM 文件并保存 `bgm_job_manifest.json`。
13. 后端新增 `POST /api/same-style/publish-copy-jobs` 与 `GET /api/same-style/publish-copy-jobs/{job_id}`，用于生成标题、标签、发布文案和评论引导。
14. 后端新增 `POST /api/same-style/cover-jobs` 与 `GET /api/same-style/cover-jobs/{job_id}`，用于生成同款封面 `cover.png`。
15. 后端新增 `POST /api/same-style/publish-jobs` 与 `GET /api/same-style/publish-jobs/{job_id}`，用于汇总最终视频、封面、标题标签和平台选择；带字幕任务时会先烧录 `final_video.mp4`，再生成发布包 manifest。

## 八个模块

### 1. 文案生成

模块名：`reference_video_importer`

输入对标视频链接、自有产品信息和仿写模式。落地时先下载或登记参考视频，用 FFmpeg 抽取音频和关键帧，用腾讯云 ASR 或本地 Whisper 转写口播，再让大模型输出对标文案、爆款结构和自有产品版本文案。

当前已正式实现第一段：

- 外部链接：已接 `yt-dlp` 适配器；优先使用系统 `yt-dlp` 命令，找不到时使用项目虚拟环境里的 `python -m yt_dlp`。下载成功后进入抽音频/ASR，缺 cookie 或平台拦截时返回 `failed` 并写明失败原因。
- 任务追踪：即使处于 `download_required`，也会写入 `reference_manifest.json`，刷新页面后仍可通过 job id 查回状态。
- 失败留痕：本地视频路径错误、外链下载失败、FFmpeg 抽音失败都会返回 `failed` 任务并写入 `reference_manifest.json`，前端展示具体失败原因和 manifest 路径。
- 本地视频：支持填写视频待处理目录、视频输出目录或草稿目录下的视频文件名/路径。
- 产物：在 `draft_output_path/_same_style_jobs/<job_id>/` 下生成 `audio_16k.wav`、可选 `subtitle.srt` 和 `reference_manifest.json`。
- 视频抽帧：导入成功后会用 FFmpeg 按 1 fps 生成 `reference_frames/frame_0001.jpg...`，并在 manifest / 前端 Shot Manifest 中显示抽帧数量。
- 宫格参考图：有效参考帧会用 Pillow 合成为 `reference_grid.jpg`，作为后续 Seedance / 图生视频接口的多帧视觉约束输入。
- 产品参考图：可填写素材目录内的产品图路径，系统会生成 `product_reference_grid.jpg`，优先作为 Seedance 图生视频参考图，降低自有产品外观漂移。
- ASR：如果腾讯云 ASR 或本地 Whisper 已配置，会写入 `transcript`、SRT 字幕和基础 `shot_manifest`。
- 抖音 cookie：可配置 `YT_DLP_COOKIES_PATH`，下载外链时会传给 `yt-dlp`；没有 cookie 或平台拦截时仍会保留失败 manifest。
- 安全边界：本地视频路径必须位于项目配置的素材目录内，不接受任意系统路径。
- 运行状态：`GET /api/same-style/workflow` 会显示 `yt-dlp` 命令或 Python 模块是否可用，前端能力条自动展示该状态。
- 脚本草案：完成转写后会生成 `script_draft`，包含开头钩子、同款改写段落、标题候选、封面文案、发布文案和评论引导。已抽出 `same_style_script_generator`，配置 DeepSeek 时走结构化 JSON 生成，未配置或调用失败时使用可编辑兜底草案。
- 前端回显：页面带 `reference_job` 参数时会读取 job manifest，把 `transcript` 填入“对标文案”，把 `script_draft` 填入“仿写文案”。

### 2. 音频生成

模块名：`cosyvoice_adapter`

输入改写口播稿、音色、参考音频路径和语速，调用 CosyVoice 或同类 TTS/声音克隆服务输出 `voice.wav`。当前已落地 `cosyvoice_adapter` 任务契约：未配置 `COSYVOICE_BASE_URL` 时返回 `configuration_required` 并写入 `audio_job_manifest.json`；配置后可通过 HTTP 适配器提交任务。配置 `COSYVOICE_STATUS_URL_TEMPLATE` 后，读取任务时会刷新 submitted 状态，并把 provider 返回的音频 URL 下载为本地 `voice.wav`。Whisper 只负责识别，不负责声音克隆。

### 3. 视频生成

模块名：`heygem_digital_human_job`

输入数字人模型、口播音频和输出比例，调用 HeyGem/Duix Avatar 类本地数字人服务生成 `talking_head.mp4`；或输入 `product_reference_grid.jpg` / `reference_grid.jpg`、分镜提示词和比例，调用 Seedance 图生视频生成同款镜头视频。当前已落地两条视频任务契约：`heygem_digital_human_job` 未配置 `HEYGEM_BASE_URL` 时返回 `configuration_required` 并写入 `digital_human_job_manifest.json`，配置后可通过 HTTP 适配器提交数字人口播任务；`seedance_video_job` 未配置 `SEEDANCE_API_KEY` / `SEEDANCE_BASE_URL` 时返回 `configuration_required` 并写入 manifest，配置后可通过 HTTP 适配器提交图生视频任务。配置 `HEYGEM_STATUS_URL_TEMPLATE` / `SEEDANCE_STATUS_URL_TEMPLATE` 后，读取任务会刷新 provider 状态并下载远程视频结果。

### 4. 字幕生成

模块名：`subtitle_artifact_writer`

复用现有 SRT 字幕切分和格式化能力，生成字幕文件和字幕样式参数。当前已落地 `subtitle_artifact_writer` 任务契约：优先读取 CosyVoice 音频任务中的同款口播文本生成 `subtitle.srt`，没有音频任务时回退到参考任务的 `script_draft` 估算字幕时间轴。字体名称、字幕颜色和描边颜色会写入字幕 manifest，并在发布包生成时用于 FFmpeg 字幕烧录。

### 5. 背景音乐生成

模块名：`bgm_mix_job`

当前已落地 `bgm_mix_job` 任务契约：选择“不使用”时会把已有成片视频登记为下一步输入；填写本地 BGM 文件路径时会调用 FFmpeg `amix` 生成 `mixed_video.mp4`，并写入 `bgm_job_manifest.json`。选择“素材库”时会读取 `BGM_LIBRARY_PATH`，支持按文件名选择或自动选第一首可用音频。

### 6. 标题生成

模块名：`publish_copy_generator`

当前已落地 `publish_copy_generator` 任务契约：读取同款脚本草案中的标题候选、封面文案、发布文案和评论引导，补充平台标签，写入 `publish_copy_job_manifest.json`。后续可再接 LLM 做二次平台化改写。

### 7. 封面制作

模块名：`cover_artifact_writer`

当前已落地 `cover_artifact_writer` 任务契约：复用现有 Pillow 像素封面模板，读取发布文案或同款脚本中的 `cover_copy`，输出 `cover.png` 和 `cover_job_manifest.json`。后续可增加从成片抽帧、产品图、宫格参考图中自动取封面素材。

### 8. 视频发布

模块名：`social_auto_upload_adapter`

当前已落地 `social_auto_upload_adapter` 的安全任务契约：汇总最终视频、封面、标题标签、发布文案和平台选择，生成 `publish_job_manifest.json`。默认状态为 `ready_for_review`，等待人工确认；如果用户勾选自动提交且配置了 `SOCIAL_AUTO_UPLOAD_BASE_URL`，会把发布包提交到外部自动上传服务，并记录 `provider_job_id` 与 `submitted` / `published` 状态。

## 现有可复用能力

- `apps/api/app/media_commands.py`：FFmpeg 音频抽取、裁剪、转码、BGM 混音和字幕烧录。
- `apps/api/app/asr.py`：腾讯云 ASR 与本地 Whisper 转写。
- `apps/api/app/draft_artifacts.py`：SRT 字幕、音频提取和封面产物。
- `apps/api/app/cover_artifacts.py`：Pillow 封面生成。
- `apps/api/app/drafts.py`：剪映草稿素材包和 manifest。
- `apps/api/app/same_style_audio_job.py`：CosyVoice 音频任务 manifest 和提交适配器。
- `apps/api/app/same_style_digital_human_job.py`：HeyGem/Duix 数字人口播视频任务 manifest 和提交适配器。
- `apps/api/app/same_style_subtitle_job.py`：同款字幕 SRT 任务 manifest 和字幕文件生成。
- `apps/api/app/same_style_bgm_job.py`：同款视频 BGM 跳过/混音任务 manifest 和 FFmpeg 混音。
- `apps/api/app/same_style_publish_copy_job.py`：同款标题、标签和发布文案任务 manifest。
- `apps/api/app/same_style_cover_job.py`：同款封面任务 manifest 和 Pillow 封面生成。
- `apps/api/app/same_style_publish_job.py`：最终视频合成、待人工确认发布包 manifest 和可选自动上传提交。
- `apps/api/app/same_style_video_job.py`：Seedance 图生视频任务 manifest 和提交适配器。
- `apps/api/app/same_style_provider_tasks.py`：外部 provider 状态归一、状态查询和远程媒体下载。
- `apps/web/src/app/same-style-script/page.tsx`：一键追爆工作台 UI。

## 外部配置边界

- `cosyvoice_adapter`：已接入提交、状态刷新、远程音频下载和参考音频路径；真实生成仍需要填入 `COSYVOICE_BASE_URL` / 可选 `COSYVOICE_API_KEY` / 可选 `COSYVOICE_STATUS_URL_TEMPLATE`。
- `heygem_digital_human_job`：已接入提交、状态刷新和远程视频下载；真实生成仍需要填入 `HEYGEM_BASE_URL` / 可选 `HEYGEM_API_KEY` / `HEYGEM_AVATAR_ID` / 可选 `HEYGEM_STATUS_URL_TEMPLATE`。
- `seedance_video_job`：已接入产品宫格优先、提交、状态刷新和远程视频下载；真实生成仍需要填入 `SEEDANCE_API_KEY` / `SEEDANCE_BASE_URL` / 可选 `SEEDANCE_STATUS_URL_TEMPLATE`。
- `bgm_mix_job`：已接入跳过 BGM、本地 BGM 文件混音和 `BGM_LIBRARY_PATH` 素材库选择；生成式 BGM 服务不在当前本地闭环内。
- `social_auto_upload_adapter`：已接入发布包 manifest、字幕烧录后的最终视频和外部自动上传提交；真实发布仍需要用户配置自己的 `SOCIAL_AUTO_UPLOAD_BASE_URL`、平台账号和上传服务。

## 产品边界

系统复刻的是镜头结构、文案节奏和商业表达方式，不复制原视频原文案、人物肖像、品牌素材和未授权声音。外部平台发布前默认保留人工确认。
