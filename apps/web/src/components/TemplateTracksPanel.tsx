import { describeTrackTone, resolveTemplateTracks } from "@/lib/template-tracks";
import type {
  DraftAsset,
  Episode,
  RuntimeSettings,
  TemplateTrackAvailability,
  TemplateTrackSnapshot,
  TemplateTrackSource,
} from "@/lib/types";

type TemplateTracksPanelProps = {
  episode: Episode | null;
  settings: RuntimeSettings | null;
  latestPackage: DraftAsset | null;
  draftPackages?: DraftAsset[] | null;
};

function availabilityTone(availability: TemplateTrackAvailability): string {
  if (availability === "available") return "ready";
  if (availability === "absent") return "missing";
  return "attention";
}

function availabilityLabel(availability: TemplateTrackAvailability): string {
  if (availability === "available") return "已生成";
  if (availability === "absent") return "未生成";
  return "未确认";
}

function sourceLabel(source: TemplateTrackSource): string {
  if (source === "package-metadata") return "后端真实元数据";
  if (source === "package-fallback-empty") return "后端空数组 (显式 fallback)";
  if (source === "package-fallback-absent") return "后端无元数据 (老包 fallback)";
  if (source === "settings") return "运行时配置 (BGM)";
  return "无数据";
}

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "—";
  }
  try {
    return new Date(value).toLocaleString("zh-CN", {
      hour12: false,
      timeZone: "Asia/Shanghai",
    });
  } catch {
    return value;
  }
}

export function TemplateTracksPanel({
  episode,
  settings,
  latestPackage,
  draftPackages,
}: TemplateTracksPanelProps) {
  const snapshot: TemplateTrackSnapshot = resolveTemplateTracks({
    episode,
    settings,
    preferredPackage: latestPackage,
    draftPackages: draftPackages ?? undefined,
  });
  const summary = describeTrackTone(snapshot);
  const readyPct =
    snapshot.totalCount === 0
      ? 0
      : Math.round((snapshot.readyCount / snapshot.totalCount) * 100);

  return (
    <section
      aria-labelledby="template-tracks-heading"
      className={`template-tracks-panel tone-${summary.tone}`}
      data-testid="template-tracks-panel"
    >
      <div className="template-tracks-header">
        <div>
          <span className="section-kicker">Native 模板轨道</span>
          <h2 id="template-tracks-heading">剪映草稿轨道清单</h2>
          <p>
            优先使用后端 DraftAsset.template_tracks 真实元数据；对老包或无 metadata 的场景保留
            显式 fallback
            <strong>（不发明任何轨道状态，不暴露 secret）</strong>。
            {snapshot.source === "none"
              ? "需要先连通本地 API 才能看到状态。"
              : null}
          </p>
        </div>
        <a
          aria-label="查看模板与轨道实现"
          className="ui-button secondary"
          href="/templates"
        >
          查看模板
        </a>
      </div>

      <div
        aria-live="polite"
        className={`template-tracks-summary tone-${summary.tone}`}
        role="status"
      >
        <div className="template-tracks-summary-headline">
          <strong>{summary.headline}</strong>
          <span>{summary.detail}</span>
        </div>
        <div className="template-tracks-progress">
          <span
            aria-label="轨道就绪度"
            aria-valuenow={readyPct}
            aria-valuemin={0}
            aria-valuemax={100}
            className="template-tracks-progress-bar"
            role="progressbar"
            style={{ width: `${readyPct}%` }}
          />
          <em>{readyPct}%</em>
        </div>
        <p className="template-tracks-summary-meta">
          元数据：<code>
            {snapshot.packageMetadata === null
              ? "null（老包 / 无字段）"
              : snapshot.packageMetadata.length === 0
                ? "[]（空 manifest 显式 fallback）"
                : `[${snapshot.packageMetadata.join(", ")}]`}
          </code>
          {snapshot.contentTemplateId ? (
            <>
              {" · "}模板 ID：<code>{snapshot.contentTemplateId}</code>
            </>
          ) : null}
          {snapshot.backgroundMusicConfigured ? " · BGM 路径：已配置" : null}
          {" · "}最近导入：<code>{formatTimestamp(snapshot.lastPackageImportedAt)}</code>
        </p>
      </div>

      <ul className="template-tracks-list">
        {snapshot.tracks.map((track) => (
          <li
            className={`template-track-row tone-${availabilityTone(track.availability)}`}
            key={track.kind}
          >
            <div className="template-track-row-main">
              <div className="template-track-row-title">
                <span
                  aria-hidden="true"
                  className={`template-track-light tone-${availabilityTone(track.availability)}`}
                />
                <strong>{track.label}</strong>
                <span
                  className={`template-track-state tone-${availabilityTone(track.availability)}`}
                >
                  {availabilityLabel(track.availability)}
                </span>
                <span
                  className="template-track-source"
                  title={track.raw ?? sourceLabel(track.source)}
                >
                  {sourceLabel(track.source)}
                  {track.raw ? ` · ${track.raw}` : null}
                </span>
              </div>
              <p className="template-track-row-description">{track.description}</p>
              <p className="template-track-row-detail">{track.detail}</p>
              <p className="template-track-row-impact">{track.impact}</p>
            </div>
            {track.fixHref ? (
              <a
                className="ui-button secondary"
                href={track.fixHref}
              >
                去设置
              </a>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
