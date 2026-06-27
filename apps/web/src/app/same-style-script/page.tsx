import Link from "next/link";
import type { ReactNode } from "react";
import {
  startSameStyleAudioJobAction,
  startSameStyleBgmJobAction,
  startSameStyleCoverJobAction,
  startSameStyleDigitalHumanJobAction,
  startSameStylePublishCopyJobAction,
  startSameStylePublishJobAction,
  startSameStyleReferenceJobAction,
  startSameStyleSubtitleJobAction,
  startSameStyleVideoJobAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";
import {
  getSameStyleAudioJob,
  getSameStyleBgmJob,
  getSameStyleCoverJob,
  getSameStyleDigitalHumanJob,
  getSameStylePublishCopyJob,
  getSameStylePublishJob,
  getSameStyleReferenceJob,
  getSameStyleSubtitleJob,
  getSameStyleVideoJob,
  getSameStyleWorkflow,
} from "@/lib/api";
import type {
  SameStyleAudioJob,
  SameStyleBgmJob,
  SameStyleCoverJob,
  SameStyleDigitalHumanJob,
  SameStylePublishCopyJob,
  SameStylePublishJob,
  SameStyleReferenceJob,
  SameStyleStageStatus,
  SameStyleSubtitleJob,
  SameStyleVideoJob,
  SameStyleWorkflow,
  SameStyleWorkflowSection,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type SameStyleScriptPageProps = {
  searchParams?: Promise<{
    reference_error?: string;
    reference_job?: string;
    reference_status?: string;
    audio_error?: string;
    audio_job?: string;
    audio_status?: string;
    bgm_error?: string;
    bgm_job?: string;
    bgm_status?: string;
    cover_error?: string;
    cover_job?: string;
    cover_status?: string;
    digital_human_error?: string;
    digital_human_job?: string;
    digital_human_status?: string;
    publish_copy_error?: string;
    publish_copy_job?: string;
    publish_copy_status?: string;
    publish_error?: string;
    publish_job?: string;
    publish_status?: string;
    subtitle_error?: string;
    subtitle_job?: string;
    subtitle_status?: string;
    video_error?: string;
    video_job?: string;
    video_status?: string;
  }>;
};

const fallbackWorkflow: SameStyleWorkflow = {
  title: "一键追爆智能体",
  summary: "从对标视频链接生成仿写文案、音频、数字人口播视频、字幕、封面和发布素材。",
  sections: [
    {
      id: "script_generation",
      label: "文案生成",
      status: "partial",
      engine: "FFmpeg + ASR + DeepSeek",
      module_name: "reference_video_importer",
      description: "导入对标视频，抽音频、转写口播，并由大模型生成仿写文案。",
      inputs: ["对标视频链接", "自有产品信息", "仿写模式"],
      outputs: ["对标文案", "改写口播稿", "参考帧", "宫格参考图", "标题标签候选"],
      primary_action: "提取对标文案",
    },
    {
      id: "audio_generation",
      label: "音频生成",
      status: "missing",
      engine: "CosyVoice",
      module_name: "cosyvoice_adapter",
      description: "把改写后的口播稿合成为指定音色的音频。",
      inputs: ["口播稿", "音色", "语速"],
      outputs: ["voice.wav", "音频时长"],
      primary_action: "生成音频",
    },
    {
      id: "digital_human_video",
      label: "视频生成",
      status: "missing",
      engine: "HeyGem / Duix Avatar / Seedance",
      module_name: "heygem_digital_human_job",
      description: "可用数字人模型生成口播视频，也可用 Seedance 读取宫格参考图生成同款镜头视频。",
      inputs: ["数字人模型或宫格参考图", "voice.wav 或分镜提示词", "输出比例"],
      outputs: ["talking_head.mp4 或 seedance_video.mp4", "生成任务ID"],
      primary_action: "生成视频",
    },
    {
      id: "subtitle_generation",
      label: "字幕生成",
      status: "partial",
      engine: "ASR + SRT + FFmpeg",
      module_name: "subtitle_artifact_writer",
      description: "复用现有 ASR 和字幕产物链路生成 SRT/ASS 字幕。",
      inputs: ["成片视频", "字幕样式"],
      outputs: ["subtitle.srt", "字幕样式参数"],
      primary_action: "生成字幕",
    },
    {
      id: "bgm_generation",
      label: "背景音乐生成",
      status: "partial",
      engine: "BGM library + FFmpeg",
      module_name: "bgm_mix_job",
      description: "选择或生成 BGM，并按音量参数混入成片。",
      inputs: ["成片视频", "BGM 策略", "音量"],
      outputs: ["mixed_video.mp4", "BGM 使用记录"],
      primary_action: "混入背景音乐",
    },
    {
      id: "title_generation",
      label: "标题生成",
      status: "missing",
      engine: "DeepSeek",
      module_name: "publish_copy_generator",
      description: "根据成片内容生成标题、话题标签和平台发布文案。",
      inputs: ["改写口播稿", "平台", "目标人群"],
      outputs: ["标题候选", "标签", "发布文案"],
      primary_action: "生成标题标签",
    },
    {
      id: "cover_generation",
      label: "封面制作",
      status: "ready",
      engine: "Pillow pixel template",
      module_name: "cover_artifact_writer",
      description: "复用现有封面模板生成可进入草稿包的封面图。",
      inputs: ["标题", "副标题", "封面模板"],
      outputs: ["cover.png", "封面文案"],
      primary_action: "生成封面",
    },
    {
      id: "publisher",
      label: "视频发布",
      status: "partial",
      engine: "social-auto-upload",
      module_name: "social_auto_upload_adapter",
      description: "汇总视频、封面、标题和标签生成发布包；自动上传前保留人工确认。",
      inputs: ["最终视频", "封面", "标题标签", "平台账号"],
      outputs: ["发布包 manifest", "人工确认状态"],
      primary_action: "生成发布包",
    },
  ],
  capabilities: [
    {
      id: "api",
      label: "API",
      status: "missing",
      detail: "本地 API 暂不可用，页面使用离线契约。",
    },
  ],
  safety_rules: [
    "复刻镜头结构和表达节奏，不复制原视频原文案。",
    "默认替换为自有产品、自己的人设和授权素材。",
    "外部平台发布前保留人工确认。",
  ],
};

const sectionIcon: Record<string, PixelIconName> = {
  script_generation: "script",
  audio_generation: "bell",
  digital_human_video: "person",
  subtitle_generation: "doc",
  bgm_generation: "spark",
  title_generation: "note",
  cover_generation: "cover",
  publisher: "publish",
};

const statusLabel: Record<SameStyleStageStatus, string> = {
  ready: "可用",
  partial: "部分就绪",
  missing: "待接入",
};

async function loadWorkflow(): Promise<SameStyleWorkflow> {
  try {
    return await getSameStyleWorkflow();
  } catch {
    return fallbackWorkflow;
  }
}

async function loadReferenceJob(jobId?: string): Promise<SameStyleReferenceJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStyleReferenceJob(jobId);
  } catch {
    return null;
  }
}

async function loadAudioJob(jobId?: string): Promise<SameStyleAudioJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStyleAudioJob(jobId);
  } catch {
    return null;
  }
}

async function loadDigitalHumanJob(
  jobId?: string,
): Promise<SameStyleDigitalHumanJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStyleDigitalHumanJob(jobId);
  } catch {
    return null;
  }
}

async function loadVideoJob(jobId?: string): Promise<SameStyleVideoJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStyleVideoJob(jobId);
  } catch {
    return null;
  }
}

async function loadSubtitleJob(jobId?: string): Promise<SameStyleSubtitleJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStyleSubtitleJob(jobId);
  } catch {
    return null;
  }
}

async function loadBgmJob(jobId?: string): Promise<SameStyleBgmJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStyleBgmJob(jobId);
  } catch {
    return null;
  }
}

async function loadPublishCopyJob(
  jobId?: string,
): Promise<SameStylePublishCopyJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStylePublishCopyJob(jobId);
  } catch {
    return null;
  }
}

async function loadCoverJob(jobId?: string): Promise<SameStyleCoverJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStyleCoverJob(jobId);
  } catch {
    return null;
  }
}

async function loadPublishJob(jobId?: string): Promise<SameStylePublishJob | null> {
  if (!jobId) {
    return null;
  }
  try {
    return await getSameStylePublishJob(jobId);
  } catch {
    return null;
  }
}

function sectionById(
  workflow: SameStyleWorkflow,
  id: string,
): SameStyleWorkflowSection {
  return (
    workflow.sections.find((section) => section.id === id) ??
    fallbackWorkflow.sections.find((section) => section.id === id) ??
    fallbackWorkflow.sections[0]
  );
}

function scriptDraftText(referenceJob: SameStyleReferenceJob | null): string {
  if (!referenceJob?.script_draft) {
    return "等待生成自有产品版本...";
  }
  const draft = referenceJob.script_draft;
  return [
    `开头：${draft.hook}`,
    ...draft.sections.map(
      (section, index) =>
        `${index + 1}. ${section.title}（${section.estimated_seconds}s）\n${section.content}`,
    ),
    `标题：${draft.title_options.join(" / ")}`,
    `发布文案：${draft.publish_copy}`,
  ].join("\n\n");
}

function timeRange(startMs: number, endMs: number): string {
  const format = (value: number) => {
    const seconds = Math.max(0, Math.floor(value / 1000));
    return `00:${String(seconds).padStart(2, "0")}`;
  };
  return `${format(startMs)}-${format(endMs)}`;
}

export default async function SameStyleScriptPage({
  searchParams,
}: SameStyleScriptPageProps) {
  const workflow = await loadWorkflow();
  const params = await searchParams;
  const referenceJob = await loadReferenceJob(params?.reference_job);
  const audioJob = await loadAudioJob(params?.audio_job);
  const digitalHumanJob = await loadDigitalHumanJob(params?.digital_human_job);
  const videoJob = await loadVideoJob(params?.video_job);
  const subtitleJob = await loadSubtitleJob(params?.subtitle_job);
  const bgmJob = await loadBgmJob(params?.bgm_job);
  const publishCopyJob = await loadPublishCopyJob(params?.publish_copy_job);
  const coverJob = await loadCoverJob(params?.cover_job);
  const publishJob = await loadPublishJob(params?.publish_job);
  const sections = {
    script: sectionById(workflow, "script_generation"),
    audio: sectionById(workflow, "audio_generation"),
    video: sectionById(workflow, "digital_human_video"),
    subtitle: sectionById(workflow, "subtitle_generation"),
    bgm: sectionById(workflow, "bgm_generation"),
    title: sectionById(workflow, "title_generation"),
    cover: sectionById(workflow, "cover_generation"),
    publish: sectionById(workflow, "publisher"),
  };
  const digitalHumanSection: SameStyleWorkflowSection = {
    ...sections.video,
    label: "数字人口播视频",
    engine: "HeyGem / Duix Avatar",
    description: "把口播音频和数字人模型提交给 HeyGem/Duix，生成 talking_head.mp4。",
    inputs: ["音频任务 voice.wav", "数字人 Avatar ID", "输出比例"],
    outputs: ["talking_head.mp4", "数字人任务ID"],
    primary_action: "生成数字人视频",
  };
  const seedanceSection: SameStyleWorkflowSection = {
    ...sections.video,
    label: "同款镜头视频",
    engine: "Seedance",
    module_name: "seedance_video_job",
    description: "读取宫格参考图和分镜提示词，生成同款镜头节奏的新视频。",
    inputs: ["宫格参考图", "分镜提示词", "时长和比例"],
    outputs: ["seedance_video.mp4", "图生视频任务ID"],
    primary_action: "生成同款镜头",
  };
  const shotRows = referenceJob?.shot_manifest.length
    ? referenceJob.shot_manifest.map((shot) => [
        timeRange(shot.start_ms, shot.end_ms),
        shot.text,
        "口播节奏",
        shot.purpose,
      ])
    : [
        ["00:00-00:02", "强钩子近景", "手持推进", "停留"],
        ["00:02-00:06", "产品操作", "俯拍跟随", "演示"],
        ["00:06-00:10", "结果对比", "快速切换", "转化"],
      ];
  const referenceFrameHint = referenceJob?.reference_frames.length
    ? `已抽取 ${referenceJob?.reference_frames.length} 帧`
    : "等待抽帧参考图";
  const referenceGridHint = referenceJob?.reference_grid_path
    ? "宫格参考图已生成"
    : "宫格待生成";
  const productGridHint = referenceJob?.product_reference_grid_path
    ? "产品宫格已生成"
    : "产品宫格待生成";

  return (
    <AppShell>
      <div className="workspace-page same-style-page">
        <section className="same-style-hero retro-panel">
          <div className="same-style-hero-copy">
            <span className="section-kicker">同款脚本生成</span>
            <h1>{workflow.title}</h1>
            <p>{workflow.summary}</p>
          </div>
          <aside className="same-style-environment-panel inspector-panel" aria-label="环境状态">
            <h2>环境状态</h2>
            <div className="same-style-environment-list">
            {workflow.capabilities.map((capability) => (
              <div key={capability.id}>
                <span className={`status-dot ${capability.status}`} />
                <strong>{capability.label}</strong>
                <em>{statusLabel[capability.status]}</em>
              </div>
            ))}
            </div>
          </aside>
          <Link className="pixel-button secondary" href="/script">
            查看口播稿
            </Link>
        </section>

        <section className="same-style-step-grid" aria-label="同款脚本生成步骤">
          <article className="same-style-step-card retro-panel-secondary">
            <span className="section-kicker">Step 1</span>
            <h2>输入对标视频</h2>
            <p>粘贴抖音、视频号、B站等链接或本地视频路径，补充自有产品名称、卖点、人群和禁用词。</p>
            <ul className="same-style-step-meta">
              <li><span className="status-dot processing" /> 抽帧参考图</li>
              <li><span className="status-dot processing" /> ASR 转写原文案</li>
              <li><span className="status-dot processing" /> 拆解镜头节奏</li>
            </ul>
          </article>
          <article className="same-style-step-card retro-panel-secondary">
            <span className="section-kicker">Step 2</span>
            <h2>文案结果</h2>
            <p>大模型保留爆款结构、冲突节奏和镜头逻辑，只替换为你自己的产品信息与表达口吻。</p>
            <ul className="same-style-step-meta">
              <li><span className="status-dot ready" /> 对标文案</li>
              <li><span className="status-dot partial" /> 仿写口播稿</li>
              <li><span className="status-dot partial" /> 标题标签候选</li>
            </ul>
          </article>
          <article className="same-style-step-card retro-panel-secondary">
            <span className="section-kicker">Step 3</span>
            <h2>衍生素材</h2>
            <p>继续生成音频、字幕、宫格参考图、Seedance 同款镜头视频、封面和发布素材包。</p>
            <ul className="same-style-step-meta">
              <li><span className="status-dot partial" /> CosyVoice 音频</li>
              <li><span className="status-dot missing" /> Seedance 视频</li>
              <li><span className="status-dot partial" /> 发布包</li>
            </ul>
          </article>
        </section>

        <ReferenceJobBanner
          error={params?.reference_error}
          jobId={params?.reference_job}
          referenceJob={referenceJob}
          status={params?.reference_status}
        />
        <AudioJobBanner
          audioJob={audioJob}
          error={params?.audio_error}
          jobId={params?.audio_job}
          status={params?.audio_status}
        />
        <DigitalHumanJobBanner
          digitalHumanJob={digitalHumanJob}
          error={params?.digital_human_error}
          jobId={params?.digital_human_job}
          status={params?.digital_human_status}
        />
        <VideoJobBanner
          error={params?.video_error}
          jobId={params?.video_job}
          status={params?.video_status}
          videoJob={videoJob}
        />
        <SubtitleJobBanner
          error={params?.subtitle_error}
          jobId={params?.subtitle_job}
          status={params?.subtitle_status}
          subtitleJob={subtitleJob}
        />
        <BgmJobBanner
          bgmJob={bgmJob}
          error={params?.bgm_error}
          jobId={params?.bgm_job}
          status={params?.bgm_status}
        />
        <PublishCopyJobBanner
          error={params?.publish_copy_error}
          jobId={params?.publish_copy_job}
          publishCopyJob={publishCopyJob}
          status={params?.publish_copy_status}
        />
        <CoverJobBanner
          coverJob={coverJob}
          error={params?.cover_error}
          jobId={params?.cover_job}
          status={params?.cover_status}
        />
        <PublishJobBanner
          error={params?.publish_error}
          jobId={params?.publish_job}
          publishJob={publishJob}
          status={params?.publish_status}
        />

        <section className="same-style-studio" aria-label="一键追爆生产台">
          <div className="same-style-column same-style-column-primary">
            <ProductionPanel
              action={startSameStyleReferenceJobAction}
              section={sections.script}
            >
              <div className="same-style-control-grid">
                <label className="settings-field wide">
                  <strong>对标视频链接</strong>
                  <input
                    name="reference_url"
                    placeholder="benchmark.mp4 或 https://www.douyin.com/video/..."
                    required
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <strong>产品名称</strong>
                  <input name="product_name" placeholder="AI视频日记" type="text" />
                </label>
                <label className="settings-field">
                  <strong>仿写模式</strong>
                  <select defaultValue="auto" name="rewrite_mode">
                    <option value="auto">自动仿写</option>
                    <option value="custom">自定义口吻</option>
                    <option value="shot">只拆镜头</option>
                  </select>
                </label>
                <label className="settings-field">
                  <strong>输出比例</strong>
                  <select defaultValue="9:16" name="aspect_ratio">
                    <option value="9:16">9:16</option>
                    <option value="1:1">1:1</option>
                    <option value="16:9">16:9</option>
                  </select>
                </label>
                <label className="settings-field wide">
                  <strong>自有产品信息</strong>
                  <textarea
                    name="product_brief"
                    placeholder="产品名称、核心卖点、适用人群、禁用词"
                    rows={5}
                  />
                </label>
                <label className="settings-field wide">
                  <strong>产品参考图路径</strong>
                  <input
                    name="product_image_path"
                    placeholder="product.jpg 或 /Users/.../product.png"
                    type="text"
                  />
                </label>
              </div>
              <div className="same-style-copy-grid">
                <label className="settings-field">
                  <strong>对标文案</strong>
                  <textarea
                    defaultValue={referenceJob?.transcript ?? "等待提取对标视频口播..."}
                    rows={7}
                  />
                </label>
                <label className="settings-field">
                  <strong>仿写文案</strong>
                  <textarea
                    defaultValue={scriptDraftText(referenceJob)}
                    rows={7}
                  />
                </label>
              </div>
            </ProductionPanel>

            <section className="same-style-shot-console pixel-card" aria-label="镜头结构">
              <div className="same-style-section-head">
                  <PixelIcon name="video" />
                  <div>
                    <strong>镜头结构</strong>
                    <span>
                      Shot Manifest / {referenceFrameHint} / {referenceGridHint} / {productGridHint}
                    </span>
                  </div>
                </div>
              <div className="same-style-shot-list">
                {shotRows.map(([time, scene, motion, intent]) => (
                  <div className="same-style-shot-row" key={time}>
                    <strong>{time}</strong>
                    <span>{scene}</span>
                    <span>{motion}</span>
                    <em>{intent}</em>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div className="same-style-column">
            <ProductionPanel
              action={startSameStyleAudioJobAction}
              actionDisabled={!referenceJob?.script_draft}
              section={sections.audio}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <div className="same-style-control-grid compact">
                <label className="settings-field">
                  <strong>声音模型</strong>
                  <select defaultValue="default" name="voice_id">
                    <option value="default">默认音色</option>
                    <option value="clone">克隆音色</option>
                  </select>
                </label>
                <label className="settings-field">
                  <strong>语速</strong>
                  <input defaultValue="1.0" inputMode="decimal" name="speed" />
                </label>
                <label className="settings-field wide">
                  <strong>参考音频路径</strong>
                  <input
                    name="reference_audio_path"
                    placeholder="voice.wav 或 /Users/.../voice.wav"
                    type="text"
                  />
                </label>
                <label className="settings-field wide">
                  <strong>口播文本</strong>
                  <textarea
                    name="audio_text"
                    placeholder="默认读取仿写文案生成音频"
                    rows={4}
                  />
                </label>
              </div>
            </ProductionPanel>

            <ProductionPanel
              action={startSameStyleDigitalHumanJobAction}
              actionDisabled={!audioJob?.audio_path}
              section={digitalHumanSection}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <input name="audio_job_id" type="hidden" value={audioJob?.id ?? ""} />
              <div className="same-style-control-grid compact">
                <label className="settings-field">
                  <strong>Avatar ID</strong>
                  <input
                    defaultValue={digitalHumanJob?.avatar_id ?? ""}
                    name="avatar_id"
                    placeholder="默认读取 HEYGEM_AVATAR_ID"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <strong>输出比例</strong>
                  <select defaultValue="9:16" name="aspect_ratio">
                    <option value="9:16">9:16</option>
                    <option value="1:1">1:1</option>
                    <option value="16:9">16:9</option>
                  </select>
                </label>
                <label className="settings-field wide">
                  <strong>口播音频</strong>
                  <input
                    readOnly
                    type="text"
                    value={audioJob?.audio_path ?? "请先完成音频生成"}
                  />
                </label>
              </div>
            </ProductionPanel>

            <ProductionPanel
              action={startSameStyleVideoJobAction}
              actionDisabled={!referenceJob?.reference_grid_path}
              section={seedanceSection}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <input name="audio_job_id" type="hidden" value={audioJob?.id ?? ""} />
              <input
                name="digital_human_job_id"
                type="hidden"
                value={digitalHumanJob?.id ?? ""}
              />
              <div className="same-style-control-grid compact">
                <label className="settings-field">
                  <strong>时长</strong>
                  <input defaultValue="5" inputMode="numeric" name="duration_seconds" />
                </label>
                <label className="settings-field">
                  <strong>输出比例</strong>
                  <select defaultValue="9:16" name="aspect_ratio">
                    <option value="9:16">9:16</option>
                    <option value="1:1">1:1</option>
                    <option value="16:9">16:9</option>
                  </select>
                </label>
                <label className="settings-field wide">
                  <strong>宫格参考图</strong>
                  <input
                    readOnly
                    type="text"
                    value={
                      referenceJob?.product_reference_grid_path ??
                      referenceJob?.reference_grid_path ??
                      "请先完成抽帧和宫格参考图生成"
                    }
                  />
                </label>
                <label className="settings-field wide">
                  <strong>图生视频提示词</strong>
                  <textarea
                    name="video_prompt"
                    placeholder="默认读取仿写文案和宫格参考图生成"
                    rows={4}
                  />
                </label>
              </div>
            </ProductionPanel>

            <ProductionPanel
              action={startSameStyleSubtitleJobAction}
              actionDisabled={!referenceJob?.script_draft && !audioJob?.text}
              section={sections.subtitle}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <input name="audio_job_id" type="hidden" value={audioJob?.id ?? ""} />
              <input
                name="digital_human_job_id"
                type="hidden"
                value={digitalHumanJob?.id ?? ""}
              />
              <input name="video_job_id" type="hidden" value={videoJob?.id ?? ""} />
              <div className="same-style-control-grid compact">
                <label className="settings-field">
                  <strong>字体名称</strong>
                  <input
                    defaultValue={subtitleJob?.font_name ?? "PingFang SC"}
                    name="subtitle_font_name"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <strong>字幕样式</strong>
                  <select
                    defaultValue={subtitleJob?.style ?? "douyin_clear"}
                    name="subtitle_style"
                  >
                    <option value="douyin_clear">抖音黑体</option>
                    <option value="system">系统默认</option>
                  </select>
                </label>
                <label className="settings-field">
                  <strong>最大时长</strong>
                  <input
                    defaultValue={String(Math.ceil(audioJob?.duration_seconds ?? 60))}
                    inputMode="numeric"
                    name="max_duration_seconds"
                  />
                </label>
                <label className="settings-field">
                  <strong>字幕颜色</strong>
                  <input
                    defaultValue={subtitleJob?.font_color ?? "#fff4d8"}
                    name="subtitle_font_color"
                    type="color"
                  />
                </label>
                <label className="settings-field">
                  <strong>描边颜色</strong>
                  <input
                    defaultValue={subtitleJob?.outline_color ?? "#111111"}
                    name="subtitle_outline_color"
                    type="color"
                  />
                </label>
              </div>
            </ProductionPanel>

            <ProductionPanel
              action={startSameStyleBgmJobAction}
              actionDisabled={!digitalHumanJob?.video_path && !videoJob?.video_path}
              section={sections.bgm}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <input name="audio_job_id" type="hidden" value={audioJob?.id ?? ""} />
              <input
                name="digital_human_job_id"
                type="hidden"
                value={digitalHumanJob?.id ?? ""}
              />
              <input name="video_job_id" type="hidden" value={videoJob?.id ?? ""} />
              <input name="subtitle_job_id" type="hidden" value={subtitleJob?.id ?? ""} />
              <div className="same-style-control-grid compact">
                <label className="settings-field">
                  <strong>BGM 策略</strong>
                  <select
                    defaultValue={bgmJob?.strategy ?? "none"}
                    name="bgm_strategy"
                  >
                    <option value="none">不使用</option>
                    <option value="upload">本地文件</option>
                    <option value="library">素材库</option>
                  </select>
                </label>
                <label className="settings-field">
                  <strong>音量</strong>
                  <input
                    defaultValue={String(bgmJob?.volume ?? 0.18)}
                    inputMode="decimal"
                    name="bgm_volume"
                  />
                </label>
                <label className="settings-field wide">
                  <strong>BGM 文件路径 / 素材库文件名</strong>
                  <input
                    defaultValue={bgmJob?.bgm_path ?? ""}
                    name="bgm_path"
                    placeholder="/Users/.../music.mp3 或 library-track.mp3"
                    type="text"
                  />
                </label>
              </div>
            </ProductionPanel>
          </div>

          <div className="same-style-column">
            <ProductionPanel
              action={startSameStylePublishCopyJobAction}
              actionDisabled={!referenceJob?.script_draft}
              section={sections.title}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <input name="audio_job_id" type="hidden" value={audioJob?.id ?? ""} />
              <input
                name="digital_human_job_id"
                type="hidden"
                value={digitalHumanJob?.id ?? ""}
              />
              <input name="video_job_id" type="hidden" value={videoJob?.id ?? ""} />
              <input name="subtitle_job_id" type="hidden" value={subtitleJob?.id ?? ""} />
              <input name="bgm_job_id" type="hidden" value={bgmJob?.id ?? ""} />
              <div className="same-style-control-grid compact">
                <label className="settings-field">
                  <strong>平台</strong>
                  <select
                    defaultValue={publishCopyJob?.platform ?? "douyin"}
                    name="platform"
                  >
                    <option value="douyin">抖音</option>
                    <option value="xiaohongshu">小红书</option>
                    <option value="bilibili">B站</option>
                  </select>
                </label>
                <label className="settings-field">
                  <strong>目标人群</strong>
                  <input
                    defaultValue={publishCopyJob?.audience ?? ""}
                    name="audience"
                    placeholder="创作者 / 商家 / 私域团队"
                    type="text"
                  />
                </label>
              </div>
              <label className="settings-field">
                <strong>标题与标签</strong>
                <textarea
                  defaultValue={
                    publishCopyJob
                      ? [
                          ...publishCopyJob.title_options,
                          publishCopyJob.tags.join(" "),
                          publishCopyJob.publish_copy,
                        ].join("\n")
                      : "等待生成标题...\n#AI视频日记 #同款复刻"
                  }
                  rows={5}
                />
              </label>
            </ProductionPanel>

            <ProductionPanel
              action={startSameStyleCoverJobAction}
              actionDisabled={!referenceJob?.script_draft && !publishCopyJob?.cover_copy}
              section={sections.cover}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <input name="audio_job_id" type="hidden" value={audioJob?.id ?? ""} />
              <input
                name="digital_human_job_id"
                type="hidden"
                value={digitalHumanJob?.id ?? ""}
              />
              <input name="video_job_id" type="hidden" value={videoJob?.id ?? ""} />
              <input name="subtitle_job_id" type="hidden" value={subtitleJob?.id ?? ""} />
              <input name="bgm_job_id" type="hidden" value={bgmJob?.id ?? ""} />
              <input
                name="publish_copy_job_id"
                type="hidden"
                value={publishCopyJob?.id ?? ""}
              />
              <div className="same-style-cover-preview" aria-label="封面预览">
                <strong>{coverJob?.cover_copy.split("\n")[0] ?? "爆款结构"}</strong>
                <span>{coverJob?.cover_path ?? "自有产品版本"}</span>
                <em>
                  {coverJob?.width && coverJob?.height
                    ? `${coverJob.width}x${coverJob.height}`
                    : "9:16 COVER"}
                </em>
              </div>
              <div className="same-style-control-grid compact">
                <label className="settings-field">
                  <strong>封面模板</strong>
                  <select
                    defaultValue={coverJob?.template_id ?? "pixel_diary"}
                    name="template_id"
                  >
                    <option value="pixel_diary">像素日记</option>
                    <option value="tool_review">工具测评</option>
                    <option value="tutorial_steps">教程步骤</option>
                  </select>
                </label>
                <label className="settings-field">
                  <strong>封面文案来源</strong>
                  <input
                    readOnly
                    value={
                      publishCopyJob?.cover_copy ??
                      referenceJob?.script_draft?.cover_copy ??
                      "等待生成"
                    }
                  />
                </label>
              </div>
            </ProductionPanel>

            <ProductionPanel
              action={startSameStylePublishJobAction}
              actionDisabled={!bgmJob?.mixed_video_path}
              section={sections.publish}
            >
              <input
                name="reference_job_id"
                type="hidden"
                value={referenceJob?.id ?? ""}
              />
              <input name="audio_job_id" type="hidden" value={audioJob?.id ?? ""} />
              <input
                name="digital_human_job_id"
                type="hidden"
                value={digitalHumanJob?.id ?? ""}
              />
              <input name="video_job_id" type="hidden" value={videoJob?.id ?? ""} />
              <input name="subtitle_job_id" type="hidden" value={subtitleJob?.id ?? ""} />
              <input name="bgm_job_id" type="hidden" value={bgmJob?.id ?? ""} />
              <input
                name="publish_copy_job_id"
                type="hidden"
                value={publishCopyJob?.id ?? ""}
              />
              <input name="cover_job_id" type="hidden" value={coverJob?.id ?? ""} />
              <div className="same-style-platform-list">
                {[
                  ["douyin", "抖音"],
                  ["bilibili", "B站"],
                  ["xiaohongshu", "小红书"],
                  ["shipinhao", "视频号"],
                ].map(([value, label]) => (
                  <label className="same-style-check-row" key={value}>
                    <input
                      defaultChecked={value === "douyin"}
                      name="platforms"
                      type="checkbox"
                      value={value}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              <label className="same-style-check-row">
                <input name="auto_submit" type="checkbox" />
                <span>确认后提交到已配置的自动上传服务</span>
              </label>
            </ProductionPanel>

            <section className="same-style-log pixel-card" aria-label="处理状态">
              <div className="same-style-section-head">
                <PixelIcon name="code" />
                <div>
                  <strong>处理状态</strong>
                  <span>任务队列</span>
                </div>
              </div>
              <div className="same-style-log-list">
                {workflow.sections.map((section, index) => (
                  <div className="same-style-log-row" key={section.id}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <code>{section.module_name}</code>
                    <em className={`same-style-status-text ${section.status}`}>
                      {statusLabel[section.status]}
                    </em>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </section>

        <section className="same-style-safety-strip pixel-card" aria-label="产品边界">
          {workflow.safety_rules.map((rule) => (
            <span key={rule}>
              <PixelIcon name="check" />
              {rule}
            </span>
          ))}
        </section>
      </div>
    </AppShell>
  );
}

function ProductionPanel({
  action,
  actionDisabled,
  children,
  section,
}: {
  action?: (formData: FormData) => Promise<void>;
  actionDisabled?: boolean;
  children: ReactNode;
  section: SameStyleWorkflowSection;
}) {
  const icon = sectionIcon[section.id] ?? "spark";
  const content = (
    <>
      <div className="same-style-module-head">
        <PixelIcon name={icon} />
        <div>
          <strong>{section.label}</strong>
          <span>{section.engine}</span>
        </div>
        <em className={`same-style-status-pill ${section.status}`}>
          {statusLabel[section.status]}
        </em>
      </div>
      <p>{section.description}</p>
      {children}
      <div className="same-style-module-io">
        <span>输入：{section.inputs.join(" / ")}</span>
        <span>输出：{section.outputs.join(" / ")}</span>
      </div>
      <div className="same-style-module-action">
        <code>{section.module_name}</code>
        <button
          className="pixel-button compact"
          disabled={actionDisabled ?? section.status === "missing"}
          type={action ? "submit" : "button"}
        >
          {section.primary_action}
        </button>
      </div>
    </>
  );

  if (action) {
    return (
      <form
        action={action}
        className={`same-style-module ${section.status}`}
        aria-label={section.label}
      >
        {content}
      </form>
    );
  }

  return (
    <section className={`same-style-module ${section.status}`} aria-label={section.label}>
      {content}
    </section>
  );
}

function AudioJobBanner({
  audioJob,
  error,
  jobId,
  status,
}: {
  audioJob: SameStyleAudioJob | null;
  error?: string;
  jobId?: string;
  status?: string;
}) {
  const effectiveStatus = audioJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "completed"
      ? {
          tone: "ready",
          title: "口播音频已生成",
          detail: audioJob?.audio_path
            ? `任务 ${jobId ?? "--"} 已生成：${audioJob.audio_path}`
            : `任务 ${jobId ?? "--"} 已完成。`,
        }
      : effectiveStatus === "submitted"
        ? {
            tone: "ready",
            title: "口播音频任务已提交",
            detail: audioJob?.provider_job_id
              ? `任务 ${jobId ?? "--"} 已提交到 CosyVoice：${audioJob.provider_job_id}`
              : `任务 ${jobId ?? "--"} 已提交。`,
          }
        : effectiveStatus === "configuration_required"
          ? {
              tone: "partial",
              title: "CosyVoice 待配置",
              detail: audioJob?.manifest_path
                ? `${audioJob.message} 任务 ${jobId ?? "--"} 已写入：${audioJob.manifest_path}`
                : "请配置 COSYVOICE_BASE_URL。",
            }
          : {
              tone: "missing",
              title: "口播音频任务失败",
              detail: audioJob?.message ?? error ?? "请检查仿写文案和 CosyVoice 配置。",
            };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="音频生成任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function DigitalHumanJobBanner({
  digitalHumanJob,
  error,
  jobId,
  status,
}: {
  digitalHumanJob: SameStyleDigitalHumanJob | null;
  error?: string;
  jobId?: string;
  status?: string;
}) {
  const effectiveStatus = digitalHumanJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "completed"
      ? {
          tone: "ready",
          title: "数字人口播视频已生成",
          detail: digitalHumanJob?.video_path
            ? `任务 ${jobId ?? "--"} 已生成：${digitalHumanJob.video_path}`
            : `任务 ${jobId ?? "--"} 已完成。`,
        }
      : effectiveStatus === "submitted"
        ? {
            tone: "ready",
            title: "数字人口播视频任务已提交",
            detail: digitalHumanJob?.provider_job_id
              ? `任务 ${jobId ?? "--"} 已提交到 HeyGem/Duix：${digitalHumanJob.provider_job_id}`
              : `任务 ${jobId ?? "--"} 已提交。`,
          }
        : effectiveStatus === "configuration_required"
          ? {
              tone: "partial",
              title: "HeyGem/Duix 待配置",
              detail: digitalHumanJob?.manifest_path
                ? `${digitalHumanJob.message} 任务 ${jobId ?? "--"} 已写入：${digitalHumanJob.manifest_path}`
                : "请配置 HEYGEM_BASE_URL。",
            }
          : {
              tone: "missing",
              title: "数字人口播视频任务失败",
              detail:
                digitalHumanJob?.message ??
                error ??
                "请检查口播音频和 HeyGem/Duix 配置。",
            };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="数字人口播视频任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function VideoJobBanner({
  error,
  jobId,
  status,
  videoJob,
}: {
  error?: string;
  jobId?: string;
  status?: string;
  videoJob: SameStyleVideoJob | null;
}) {
  const effectiveStatus = videoJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "submitted"
      ? {
          tone: "ready",
          title: "图生视频任务已提交",
          detail: videoJob?.provider_job_id
            ? `任务 ${jobId ?? "--"} 已提交到 Seedance：${videoJob.provider_job_id}`
            : `任务 ${jobId ?? "--"} 已提交。`,
        }
      : effectiveStatus === "completed"
        ? {
            tone: "ready",
            title: "图生视频已生成",
            detail: videoJob?.video_path ?? "Seedance 返回了完成状态。",
          }
        : effectiveStatus === "configuration_required"
          ? {
              tone: "partial",
              title: "Seedance 待配置",
              detail: videoJob?.manifest_path
                ? `${videoJob.message} 任务 ${jobId ?? "--"} 已写入：${videoJob.manifest_path}`
                : "请配置 SEEDANCE_API_KEY 和 SEEDANCE_BASE_URL。",
            }
          : {
              tone: "missing",
              title: "图生视频任务失败",
              detail: videoJob?.message ?? error ?? "请检查宫格参考图和 Seedance 配置。",
            };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="图生视频任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function SubtitleJobBanner({
  error,
  jobId,
  status,
  subtitleJob,
}: {
  error?: string;
  jobId?: string;
  status?: string;
  subtitleJob: SameStyleSubtitleJob | null;
}) {
  const effectiveStatus = subtitleJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "completed"
      ? {
          tone: "ready",
          title: "字幕文件已生成",
          detail: subtitleJob?.subtitle_path
            ? `任务 ${jobId ?? "--"} 已生成：${subtitleJob.subtitle_path}`
            : `任务 ${jobId ?? "--"} 已完成。`,
        }
      : {
          tone: "missing",
          title: "字幕生成失败",
          detail: subtitleJob?.message ?? error ?? "请检查音频任务或仿写文案。",
        };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="字幕生成任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function BgmJobBanner({
  bgmJob,
  error,
  jobId,
  status,
}: {
  bgmJob: SameStyleBgmJob | null;
  error?: string;
  jobId?: string;
  status?: string;
}) {
  const effectiveStatus = bgmJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "completed"
      ? {
          tone: "ready",
          title: bgmJob?.bgm_source === "none" ? "BGM 已跳过" : "BGM 已混入",
          detail: bgmJob?.mixed_video_path
            ? `任务 ${jobId ?? "--"} 输出：${bgmJob.mixed_video_path}`
            : `任务 ${jobId ?? "--"} 已完成。`,
        }
      : {
          tone: "missing",
          title: "BGM 任务失败",
          detail: bgmJob?.message ?? error ?? "请检查成片视频和 BGM 文件路径。",
        };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="背景音乐任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function PublishCopyJobBanner({
  error,
  jobId,
  publishCopyJob,
  status,
}: {
  error?: string;
  jobId?: string;
  publishCopyJob: SameStylePublishCopyJob | null;
  status?: string;
}) {
  const effectiveStatus = publishCopyJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "completed"
      ? {
          tone: "ready",
          title: "标题与发布文案已生成",
          detail: publishCopyJob?.title_options.length
            ? `任务 ${jobId ?? "--"}：${publishCopyJob.title_options[0]}`
            : `任务 ${jobId ?? "--"} 已完成。`,
        }
      : {
          tone: "missing",
          title: "标题生成失败",
          detail: publishCopyJob?.message ?? error ?? "请先完成同款脚本生成。",
        };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="标题生成任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function CoverJobBanner({
  coverJob,
  error,
  jobId,
  status,
}: {
  coverJob: SameStyleCoverJob | null;
  error?: string;
  jobId?: string;
  status?: string;
}) {
  const effectiveStatus = coverJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "completed"
      ? {
          tone: "ready",
          title: "封面已生成",
          detail: coverJob?.cover_path
            ? `任务 ${jobId ?? "--"} 输出：${coverJob.cover_path}`
            : `任务 ${jobId ?? "--"} 已完成。`,
        }
      : {
          tone: "missing",
          title: "封面生成失败",
          detail: coverJob?.message ?? error ?? "请先生成同款脚本或发布文案。",
        };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="封面生成任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function PublishJobBanner({
  error,
  jobId,
  publishJob,
  status,
}: {
  error?: string;
  jobId?: string;
  publishJob: SameStylePublishJob | null;
  status?: string;
}) {
  const effectiveStatus = publishJob?.status ?? status;
  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "published"
      ? {
          tone: "ready",
          title: "视频已提交发布",
          detail: publishJob?.provider_job_id
            ? `任务 ${jobId ?? "--"} 已由自动上传服务处理：${publishJob.provider_job_id}`
            : `任务 ${jobId ?? "--"} 已完成发布提交。`,
        }
      : effectiveStatus === "submitted"
        ? {
            tone: "ready",
            title: "自动上传任务已提交",
            detail: publishJob?.provider_job_id
              ? `任务 ${jobId ?? "--"} 已提交：${publishJob.provider_job_id}`
              : `任务 ${jobId ?? "--"} 已提交到自动上传服务。`,
          }
        : effectiveStatus === "ready_for_review"
      ? {
          tone: "ready",
          title: "发布包已生成",
          detail: publishJob?.manifest_path
            ? `任务 ${jobId ?? "--"} 待人工确认：${publishJob.manifest_path}`
            : `任务 ${jobId ?? "--"} 已进入人工确认。`,
        }
        : {
            tone: "missing",
            title: "发布包生成失败",
            detail: publishJob?.message ?? error ?? "请先完成最终视频、封面和发布文案。",
          };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="视频发布任务结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

function ReferenceJobBanner({
  error,
  jobId,
  referenceJob,
  status,
}: {
  error?: string;
  jobId?: string;
  referenceJob: SameStyleReferenceJob | null;
  status?: string;
}) {
  const effectiveStatus = referenceJob?.status ?? status;

  if (!effectiveStatus) {
    return null;
  }

  const copy =
    effectiveStatus === "transcribed"
      ? {
          tone: "ready",
          title: "对标视频已完成转写",
          detail: referenceJob?.manifest_path
            ? `任务 ${jobId ?? "--"} 已生成：${referenceJob.manifest_path}`
            : `任务 ${jobId ?? "--"} 已生成音频、字幕和 reference_manifest.json。`,
        }
      : status === "audio_ready"
        ? {
            tone: "partial",
            title: "已抽取音频",
            detail: referenceJob?.audio_path
              ? `任务 ${jobId ?? "--"} 已生成：${referenceJob.audio_path}`
              : `任务 ${jobId ?? "--"} 已生成 audio_16k.wav，配置 ASR 后可继续转写。`,
          }
        : effectiveStatus === "download_required"
          ? {
              tone: "partial",
              title: "外链下载器待接入",
              detail: referenceJob?.manifest_path
                ? `任务 ${jobId ?? "--"} 已登记：${referenceJob.manifest_path}`
                : "先把对标视频放到视频待处理目录，再在输入框填写文件名即可跑真实抽音频流程。",
            }
          : referenceJob?.status === "failed"
            ? {
                tone: "missing",
                title: "参考导入失败",
                detail: referenceJob?.manifest_path
                  ? `${referenceJob?.message} 任务 ${jobId ?? "--"} 已写入：${referenceJob.manifest_path}`
                  : referenceJob?.message ?? "请检查参考视频路径和本地 API 状态。",
              }
          : {
              tone: "missing",
              title: "参考导入失败",
              detail: error ?? "请检查参考视频路径和本地 API 状态。",
            };

  return (
    <section className={`same-style-job-banner ${copy.tone}`} aria-label="参考导入结果">
      <PixelIcon name={copy.tone === "missing" ? "settings" : "check"} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}
