import { useId } from "react";
import type { VideoTemplateSettings } from "@/lib/types/templates";
import styles from "./CompositionTimingSummary.module.css";

const deliveryFrameRate = 30;

export function CompositionTimingSummary({ timing, version }: {
  timing: NonNullable<VideoTemplateSettings["composition_timing"]>;
  version?: number;
}) {
  const headingId = useId();
  return <section className={styles.timing} aria-labelledby={headingId}>
    <h4 id={headingId}>{version === undefined ? "参考节奏" : `模板来源 · 第${version}版参考节奏`}</h4>
    <dl>
      <div><dt>参考总时长（30 fps）</dt><dd>{(timing.duration_frames / deliveryFrameRate).toFixed(3)} 秒 · {timing.duration_frames} 帧</dd></div>
      <div>
        <dt>镜头切点</dt>
        <dd>{timing.cut_frames.length ? <ul>{timing.cut_frames.map(frame => (
          <li key={frame}>{(frame / deliveryFrameRate).toFixed(3)} 秒 · 第 {frame} 帧</li>
        ))}</ul> : "没有记录切点"}</dd>
      </div>
      <div>
        <dt>音乐与音效区间</dt>
        <dd>{timing.sound_cues.length ? <ul>{timing.sound_cues.map(({ role, start_ms, duration_ms }, index) => (
          <li key={index}>{role === "music" ? "音乐" : "音效"} {(start_ms / 1000).toFixed(3)}–{((start_ms + duration_ms) / 1000).toFixed(3)} 秒</li>
        ))}</ul> : "没有记录音乐或音效区间"}</dd>
      </div>
    </dl>
    <p>在剪辑的「更多剪辑 → 切开镜头」输入切点并确认。音频用本作品的素材，在「音频工具 → 导入音频」选轨，按参考区间调整并确认。</p>
  </section>;
}
