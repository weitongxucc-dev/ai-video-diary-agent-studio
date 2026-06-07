import Link from "next/link";
import {
  confirmEditPlanAction,
  revertEditPlanSegmentDecisionAction,
  updateEditPlanSegmentDecisionAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { PixelMascot } from "@/components/PixelMascot";
import { apiUrl, getEpisodeEditPlan, getTodayEpisode } from "@/lib/api";
import type { EditPlan, EditPlanDecision, EditPlanSegment, Episode } from "@/lib/types";

export const dynamic = "force-dynamic";

type EditPlanLoadState = {
  episode: Episode | null;
  editPlan: EditPlan | null;
  offline: boolean;
};

async function loadEditPlanState(): Promise<EditPlanLoadState> {
  try {
    const episode = await getTodayEpisode();
    const editPlan = await getEpisodeEditPlan(episode.id);
    return { episode, editPlan, offline: false };
  } catch {
    return { episode: null, editPlan: null, offline: true };
  }
}

function formatTimeline(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = (safeSeconds % 60).toFixed(1).padStart(4, "0");
  return `${String(minutes).padStart(2, "0")}:${remainingSeconds}`;
}

function formatSegmentRange(segment: EditPlanSegment): string {
  return `${formatTimeline(segment.start)}-${formatTimeline(segment.end)}`;
}

function segmentFlexBasis(segment: EditPlanSegment, totalSeconds: number): string {
  if (!totalSeconds || totalSeconds <= 0) {
    return "128px";
  }
  return `${Math.max(10, Math.round((segment.duration / totalSeconds) * 100))}%`;
}

function actionLabel(action: EditPlanSegment["action"]): string {
  const labels: Record<EditPlanSegment["action"], string> = {
    cut_silence: "删静音",
    keep: "保留",
    manual_review: "复核",
    remove_retake: "删重录",
  };
  return labels[action];
}

function actionTone(action: EditPlanSegment["action"]): string {
  if (action === "keep") {
    return "keep";
  }
  if (action === "manual_review") {
    return "review";
  }
  return "cut";
}

function commandLabel(command: EditPlanDecision["command"]): string {
  const labels: Record<EditPlanDecision["command"], string> = {
    cut_silence: "timeline_cut_silence",
    keep_segment: "keep_segment",
    manual_review: "manual_review",
    remove_retake: "timeline_cut_retake",
  };
  return labels[command];
}

function confidenceLabel(value: number | null): string {
  return value === null ? "--" : `${Math.round(value * 100)}%`;
}

function userDecisionLabel(
  action: NonNullable<EditPlanSegment["user_decision"]>["action"],
): string {
  const labels: Record<NonNullable<EditPlanSegment["user_decision"]>["action"], string> = {
    keep: "人工保留",
    remove: "人工删除",
    review: "人工复核",
  };
  return labels[action];
}

function getCurrentSegment(editPlan: EditPlan | null): EditPlanSegment | null {
  return (
    editPlan?.segments.find((segment) => segment.status === "kept") ??
    editPlan?.segments[0] ??
    null
  );
}

function getPrimaryDecision(editPlan: EditPlan | null): EditPlanDecision | null {
  return (
    editPlan?.decisions.find((decision) => decision.command !== "keep_segment") ??
    editPlan?.decisions[0] ??
    null
  );
}

function countSegments(
  editPlan: EditPlan | null,
  predicate: (segment: EditPlanSegment) => boolean,
): number {
  return editPlan?.segments.filter(predicate).length ?? 0;
}

export default async function EditPlanPage() {
  const { episode, editPlan, offline } = await loadEditPlanState();
  const currentSegment = getCurrentSegment(editPlan);
  const primaryDecision = getPrimaryDecision(editPlan);
  const keptCount = countSegments(editPlan, (segment) => segment.status === "kept");
  const removedCount = countSegments(
    editPlan,
    (segment) => segment.status === "auto_removed",
  );
  const reviewCount = countSegments(
    editPlan,
    (segment) => segment.status === "manual_review",
  );
  const segmentsById = new Map(
    editPlan?.segments.map((segment) => [segment.id, segment]) ?? [],
  );
  const canConfirm =
    Boolean(editPlan?.available) && editPlan?.jianying.status !== "draft_ready";

  return (
    <AppShell>
      <div className="edit-plan-page">
        <section className="edit-plan-main pixel-card">
          <div className="edit-plan-header">
            <div>
              <span className="section-kicker">剪辑计划</span>
              <h1>自动剪辑可视化控制台</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : editPlan?.message ??
                    "Agent 会把口播转写、重录检测、剪辑动作和剪映草稿导入计划摆出来。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/video">
              返回视频处理
            </Link>
          </div>

          <div className="edit-plan-status-grid" aria-label="自动剪辑阶段">
            <div className={`edit-plan-status ${editPlan?.available ? "active" : ""}`}>
              <PixelIcon name="script" />
              <strong>1 转写对齐</strong>
              <span>{editPlan?.available ? "剪辑计划已读取" : "等待处理视频"}</span>
            </div>
            <div className={`edit-plan-status ${removedCount + reviewCount > 0 ? "active" : ""}`}>
              <PixelIcon name="clip" />
              <strong>2 重录检测</strong>
              <span>
                {removedCount + reviewCount > 0
                  ? `${removedCount} 段自动剪 / ${reviewCount} 段复核`
                  : "暂无重录动作"}
              </span>
            </div>
            <div className={`edit-plan-status ${editPlan?.available ? "active" : ""}`}>
              <PixelIcon name="spark" />
              <strong>3 效果建议</strong>
              <span>{editPlan?.available ? "等待下一阶段生成" : "未开始"}</span>
            </div>
            <div className={`edit-plan-status ${editPlan?.jianying.status === "draft_ready" ? "active" : ""}`}>
              <PixelIcon name="publish" />
              <strong>4 草稿导入</strong>
              <span>
                {editPlan?.jianying.status === "draft_ready" ? "已进入剪映流程" : "等待确认"}
              </span>
            </div>
          </div>

          <section className="video-preview-window" aria-label="视频浏览窗口">
            <div className="pixel-window-title">
              <strong>视频浏览窗口</strong>
              <span>
                {editPlan?.output_video?.file_name ??
                  editPlan?.source_video?.file_name ??
                  "等待处理后视频"}
              </span>
            </div>
            <div className="video-preview-body">
              <div className="video-preview-screen" aria-label="当前视频画面">
                <div className="video-scanline" />
                <span className="preview-rec">PREVIEW</span>
                <span className="preview-timecode">
                  {currentSegment ? formatTimeline(currentSegment.start) : "--:--"}
                </span>
                {editPlan?.output_video ? (
                  <video
                    className="video-preview-player"
                    controls
                    preload="metadata"
                    src={apiUrl(editPlan.output_video.url)}
                  >
                    当前浏览器无法播放这个视频。
                  </video>
                ) : (
                  <div className="preview-frame-card">
                    <PixelIcon name="video" />
                    <strong>{offline ? "API 未连接" : "还没有可浏览的视频"}</strong>
                    <p>
                      {editPlan?.message ??
                        "处理视频后，这里会直接加载本地生成的 preview 或 dedup 成片。"}
                    </p>
                  </div>
                )}
              </div>

              <div className="video-preview-inspector">
                <strong>当前片段</strong>
                <dl>
                  <div>
                    <dt>动作</dt>
                    <dd>{currentSegment ? actionLabel(currentSegment.action) : "--"}</dd>
                  </div>
                  <div>
                    <dt>范围</dt>
                    <dd>{currentSegment ? formatSegmentRange(currentSegment) : "--"}</dd>
                  </div>
                  <div>
                    <dt>来源</dt>
                    <dd>{editPlan?.source_files.join(" / ") || "本地录制"}</dd>
                  </div>
                  <div>
                    <dt>策略</dt>
                    <dd>{editPlan?.strategy ?? "--"}</dd>
                  </div>
                </dl>
                <div className="video-preview-controls" aria-label="视频浏览控制">
                  <button className="pixel-button secondary" disabled type="button">
                    上一段
                  </button>
                  <button className="pixel-button" disabled type="button">
                    使用视频控件播放
                  </button>
                  <button className="pixel-button secondary" disabled type="button">
                    下一段
                  </button>
                </div>
              </div>
            </div>
            <div className="source-marker-strip" aria-label="原片标记">
              <div className="source-marker-title">
                <strong>原片标记</strong>
                <span>只用于浏览跳转，不代表剪辑动作</span>
              </div>
              <div className="source-marker-list">
                {editPlan?.markers.length ? (
                  editPlan.markers.map((marker, index) => (
                    <button className="source-marker" key={marker.segment_id} type="button">
                      <span>M{index + 1}</span>
                      <strong>{marker.time}</strong>
                      <em>{marker.label}</em>
                      <small>跳转 · {marker.memo}</small>
                    </button>
                  ))
                ) : (
                  <div className="source-marker empty">等待剪辑计划生成</div>
                )}
              </div>
            </div>
          </section>

          <section className="pixel-window-panel" aria-label="剪辑计划时间轴">
            <div className="pixel-window-title">
              <strong>剪辑计划时间轴</strong>
              <span>{editPlan?.output_video?.file_name ?? "无可用计划"}</span>
            </div>
            <div className="edit-plan-timeline">
              {editPlan?.segments.length ? (
                editPlan.segments.map((segment) => (
                  <div
                    className="timeline-segment"
                    key={segment.id}
                    style={{ flexBasis: segmentFlexBasis(segment, editPlan.duration_seconds) }}
                  >
                    <strong>{segment.label}</strong>
                    <span>{formatSegmentRange(segment)}</span>
                    <small>{segment.reason}</small>
                    <em className={`timeline-action-chip ${actionTone(segment.action)}`}>
                      {actionLabel(segment.action)}
                    </em>
                  </div>
                ))
              ) : (
                <div className="timeline-segment">
                  <strong>等待计划</strong>
                  <span>--</span>
                  <small>先在视频处理页生成 preview 或完整包。</small>
                  <em className="timeline-action-chip review">待处理</em>
                </div>
              )}
            </div>
            <div className="timeline-ruler">
              <span>00:00</span>
              <span>{formatTimeline((editPlan?.duration_seconds ?? 0) * 0.25)}</span>
              <span>{formatTimeline((editPlan?.duration_seconds ?? 0) * 0.5)}</span>
              <span>{formatTimeline((editPlan?.duration_seconds ?? 0) * 0.75)}</span>
              <span>{formatTimeline(editPlan?.duration_seconds ?? 0)}</span>
            </div>
          </section>

          <section className="edit-decision-grid" aria-label="剪辑动作清单">
            {editPlan?.decisions.length ? (
              editPlan.decisions.map((decision) => {
                const segment = segmentsById.get(decision.segment_id);
                const decisionReason = `用户在剪辑计划工作台调整 ${decision.range}`;

                return (
                  <article className="edit-decision-card" key={decision.segment_id}>
                    <div>
                      <PixelIcon
                        name={
                          decision.command === "remove_retake" ||
                          decision.command === "cut_silence"
                            ? "thumb-down"
                            : decision.command === "manual_review"
                              ? "note"
                              : "check"
                        }
                      />
                      <strong>{decision.title}</strong>
                    </div>
                    <dl>
                      <div>
                        <dt>动作</dt>
                        <dd>{commandLabel(decision.command)}</dd>
                      </div>
                      <div>
                        <dt>范围</dt>
                        <dd>{decision.range}</dd>
                      </div>
                      <div>
                        <dt>置信度</dt>
                        <dd>{confidenceLabel(decision.confidence)}</dd>
                      </div>
                    </dl>
                    <p>{decision.reason}</p>
                    {segment?.user_decision ? (
                      <div className="edit-decision-override">
                        <strong>{userDecisionLabel(segment.user_decision.action)}</strong>
                        <span>{segment.user_decision.reason ?? "未填写原因"}</span>
                      </div>
                    ) : (
                      <div className="edit-decision-override pending">
                        <strong>沿用自动判断</strong>
                        <span>可在这里覆盖 Agent 的剪辑动作。</span>
                      </div>
                    )}
                    {episode && segment ? (
                      <div className="edit-decision-actions">
                        <form action={updateEditPlanSegmentDecisionAction}>
                          <input name="episode_id" type="hidden" value={episode.id} />
                          <input name="segment_id" type="hidden" value={segment.id} />
                          <input name="action" type="hidden" value="keep" />
                          <input name="reason" type="hidden" value={decisionReason} />
                          <button className="pixel-button secondary compact" type="submit">
                            保留
                          </button>
                        </form>
                        <form action={updateEditPlanSegmentDecisionAction}>
                          <input name="episode_id" type="hidden" value={episode.id} />
                          <input name="segment_id" type="hidden" value={segment.id} />
                          <input name="action" type="hidden" value="remove" />
                          <input name="reason" type="hidden" value={decisionReason} />
                          <button className="pixel-button danger compact" type="submit">
                            删除
                          </button>
                        </form>
                        <form action={updateEditPlanSegmentDecisionAction}>
                          <input name="episode_id" type="hidden" value={episode.id} />
                          <input name="segment_id" type="hidden" value={segment.id} />
                          <input name="action" type="hidden" value="review" />
                          <input name="reason" type="hidden" value={decisionReason} />
                          <button className="pixel-button compact" type="submit">
                            复核
                          </button>
                        </form>
                        {segment.user_decision ? (
                          <form action={revertEditPlanSegmentDecisionAction}>
                            <input name="episode_id" type="hidden" value={episode.id} />
                            <input name="segment_id" type="hidden" value={segment.id} />
                            <button className="pixel-button secondary compact" type="submit">
                              撤销
                            </button>
                          </form>
                        ) : null}
                      </div>
                    ) : null}
                  </article>
                );
              })
            ) : (
              <article className="edit-decision-card">
                <div>
                  <PixelIcon name="note" />
                  <strong>暂无剪辑动作</strong>
                </div>
                <p>处理视频后，Agent 会在这里列出保留、删除、复核和剪映导入动作。</p>
              </article>
            )}
          </section>
        </section>

        <aside className="edit-plan-side">
          <section className="pixel-card edit-agent-next">
            <div className="edit-agent-robot">
              <PixelMascot />
            </div>
            <div>
              <h2>Agent 下一步</h2>
              <p>{editPlan?.jianying.message ?? "等待视频处理完成后生成剪辑计划。"}</p>
            </div>
            {episode && canConfirm ? (
              <form action={confirmEditPlanAction}>
                <input name="episode_id" type="hidden" value={episode.id} />
                <button className="pixel-button" type="submit">
                  确认剪辑计划
                </button>
              </form>
            ) : (
              <button className="pixel-button" disabled type="button">
                {editPlan?.jianying.status === "draft_ready" ? "已生成草稿" : "等待剪辑计划"}
              </button>
            )}
          </section>

          <section className="pixel-card edit-side-panel">
            <h2>语音定位结果</h2>
            <dl>
              <div>
                <dt>原始指令</dt>
                <dd>把开头废话剪掉</dd>
              </div>
              <div>
                <dt>命中片段</dt>
                <dd>{primaryDecision?.range ?? "--"}</dd>
              </div>
              <div>
                <dt>解析动作</dt>
                <dd>{primaryDecision ? commandLabel(primaryDecision.command) : "timeline_cut"}</dd>
              </div>
              <div>
                <dt>风险等级</dt>
                <dd>{primaryDecision?.command === "manual_review" ? "中" : "低"}</dd>
              </div>
            </dl>
          </section>

          <section className="pixel-card edit-side-panel">
            <h2>剪映轨道预览</h2>
            <ul className="track-preview">
              <li><span>V1</span><strong>主视频</strong><em>{keptCount || 0} 段</em></li>
              <li><span>S1</span><strong>字幕</strong><em>等待 ASR</em></li>
              <li><span>E1</span><strong>剪辑动作</strong><em>{removedCount + reviewCount} 个</em></li>
              <li>
                <span>C1</span>
                <strong>草稿</strong>
                <em>{editPlan?.jianying.status === "draft_ready" ? "已导入" : "待确认"}</em>
              </li>
            </ul>
          </section>
        </aside>
      </div>
    </AppShell>
  );
}
