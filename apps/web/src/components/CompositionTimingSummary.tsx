import { useId } from "react";
import type { VideoTemplateSettings } from "@/lib/types/templates";
import styles from "./CompositionTimingSummary.module.css";

const deliveryFrameRate = 30;
// SplitClip requires at least 0.04 seconds on each side of a cut.
const minimumShotFrames = Math.ceil(0.04 * deliveryFrameRate);

export function CompositionTimingSummary({ timing, version, targetDurationSeconds }: {
  timing: NonNullable<VideoTemplateSettings["composition_timing"]>;
  version?: number;
  targetDurationSeconds?: number;
}) {
  const headingId = useId();
  const targetFrames = Math.round((targetDurationSeconds ?? 0) * deliveryFrameRate);
  const scaled = targetFrames > 0 && timing.duration_frames > 0 && targetFrames !== timing.duration_frames;
  const cutFrames = scaled
    ? timing.cut_frames.map(frame => Math.round(frame * targetFrames / timing.duration_frames))
    : timing.cut_frames;
  const needsAdjustment = cutFrames.map((frame, index) => scaled && (
    frame - (cutFrames[index - 1] ?? 0) < minimumShotFrames ||
    (cutFrames[index + 1] ?? targetFrames) - frame < minimumShotFrames
  ));
  const targetEndMs = Math.round((targetDurationSeconds ?? 0) * 1000);
  function cueTime(ms: number) {
    if (!scaled) return ms;
    const frame = Math.round(ms * deliveryFrameRate * targetFrames / (1000 * timing.duration_frames));
    return Math.min(targetEndMs, Math.round(Math.min(frame, targetFrames) * 1000 / deliveryFrameRate));
  }

  return <section className={styles.timing} aria-labelledby={headingId}>
    <h4 id={headingId}>{version === undefined ? "参考节奏" : `模板来源 · 第${version}版参考节奏`}</h4>
    <dl>
      <div><dt>参考总时长（30 fps）</dt><dd>{(timing.duration_frames / deliveryFrameRate).toFixed(3)} 秒 · {timing.duration_frames} 帧</dd></div>
      {scaled ? <div><dt>本片时长</dt><dd>{(targetFrames / deliveryFrameRate).toFixed(3)} 秒 · {targetFrames} 帧</dd></div> : null}
      <div>
        <dt>{scaled ? "本片建议切点（按比例换算）" : "镜头切点"}</dt>
        <dd>{cutFrames.length ? <ul role="list">{cutFrames.map((frame, index) => (
          <li key={timing.cut_frames[index]}>
            {(frame / deliveryFrameRate).toFixed(3)} 秒 · 第 {frame} 帧{needsAdjustment[index] ? " · 需调整" : ""}
          </li>
        ))}</ul> : "没有记录切点"}
          {needsAdjustment.some(Boolean) ? <p>切点重合或不足 2 帧会产生过短镜头。请与相邻切点、片头和片尾各留至少 2 帧。</p> : null}
        </dd>
      </div>
      <div>
        <dt>{scaled ? "本片建议音乐与音效区间" : "音乐与音效区间"}</dt>
        <dd>{timing.sound_cues.length ? <ul role="list">{timing.sound_cues.map(({ role, start_ms, duration_ms }, index) => {
          const start = cueTime(start_ms);
          const end = cueTime(start_ms + duration_ms);
          return <li key={index}>{role === "music" ? "音乐" : "音效"} {end > start
            ? `${(start / 1000).toFixed(3)}–${(end / 1000).toFixed(3)} 秒`
            : "起止点在同一帧，需另选区间"}</li>;
        })}</ul> : "没有记录音乐或音效区间"}</dd>
      </div>
    </dl>
    <p>在剪辑的「更多剪辑 → 切开镜头」输入切点并确认。音频用本作品的素材，在「音频工具 → 导入音频」选轨，按参考区间调整并确认。</p>
  </section>;
}
