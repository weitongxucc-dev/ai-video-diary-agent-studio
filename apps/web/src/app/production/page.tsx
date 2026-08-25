import {
  analyzeProductionMaterialAction,
  confirmMontageClipsAction,
  createMontagePlanAction,
  createProductionBatchAction,
  renderMontagePlanAction,
  reviewProductionMaterialAction,
  runProductionBatchAction,
  scanProductionMaterialsAction,
  updateProductionMaterialSegmentAction,
  updateProductionProfileAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { ProcessingRefresh } from "@/components/ProcessingRefresh";
import {
  PixelBlockedState,
  PixelEmptyState,
  PixelPanel,
  PixelStatus,
} from "@/components/PixelPrimitives";
import {
  getProductionFoundation,
  getProductionMaterials,
} from "@/lib/api.server";
import type {
  MaterialAsset,
  MaterialLibrary,
  ProductionFoundation,
} from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadProduction(): Promise<{
  foundation: ProductionFoundation | null;
  library: MaterialLibrary | null;
}> {
  try {
    const [foundation, library] = await Promise.all([
      getProductionFoundation(),
      getProductionMaterials(),
    ]);
    return { foundation, library };
  } catch {
    return { foundation: null, library: null };
  }
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} 小时 ${minutes} 分` : `${minutes} 分钟`;
}

function statusKind(status: string) {
  if (status === "ready" || status === "approved") return "succeeded" as const;
  if (status === "failed" || status === "rejected") return "failed" as const;
  if (status === "blocked") return "blocked" as const;
  if (status === "waiting_for_user" || status === "interrupted") return "waiting_for_user" as const;
  if (status === "running") return "running" as const;
  return "idle" as const;
}

function usageRoleLabel(role: "unassigned" | "a_roll" | "b_roll"): string {
  if (role === "a_roll") return "人物主镜头";
  if (role === "b_roll") return "B-roll";
  return "需人工判断";
}

function materialReviewPriority(asset: MaterialAsset): number {
  if (asset.suggested_usage_role === "b_roll") return 0;
  if (asset.suggested_usage_role === "unassigned") return 1;
  return 2;
}

export default async function ProductionPage() {
  const { foundation, library } = await loadProduction();

  if (!foundation || !library) {
    return (
      <AppShell>
        <div className="production-page">
          <PixelBlockedState
            action={{ href: "/health", kind: "link", label: "检查运行状态" }}
            description="本地 API 未返回生产基础数据。修复运行环境后重试。"
            title="无法打开批量生产台"
          />
        </div>
      </AppShell>
    );
  }

  const { profile, readiness } = foundation;
  const episodes = foundation.eligible_episodes;
  const nextBlocker = readiness.blockers[0] ?? null;
  const materialBlocker = readiness.blockers.some(
    (blocker) => blocker.code === "approved_materials_empty",
  );
  const onlyMaterialBlocked = materialBlocker && readiness.blockers.length === 1;
  const pendingAssets = library.assets
    .filter((asset) => asset.review_status === "raw" && asset.usage_role !== "exclude")
    .sort((left, right) => materialReviewPriority(left) - materialReviewPriority(right));
  const processedAssets = library.assets.filter(
    (asset) => asset.review_status !== "raw" || asset.usage_role === "exclude",
  );
  const approvedARollCount = library.assets.filter(
    (asset) => asset.review_status === "approved" && asset.usage_role === "a_roll",
  ).length;
  const approvedBRollCount = library.assets.filter(
    (asset) => asset.review_status === "approved" && asset.usage_role === "b_roll",
  ).length;

  return (
    <AppShell
      inspector={
        <div className="production-inspector">
          <PixelStatus
            kind={readiness.ready ? "succeeded" : "blocked"}
            label={readiness.ready ? "可创建生产批次" : "生产基础未就绪"}
          />
          <dl>
            <div><dt>待审核素材</dt><dd>{pendingAssets.length}</dd></div>
            <div><dt>人物主镜头</dt><dd>{approvedARollCount}</dd></div>
            <div><dt>B-roll 镜头</dt><dd>{readiness.approved_segment_count}</dd></div>
            <div><dt>素材时长</dt><dd>{formatDuration(readiness.total_duration_seconds)}</dd></div>
          </dl>
        </div>
      }
      inspectorTitle="批量生产就绪度"
    >
      <div className="production-page">
        <header className="production-page__header">
          <span className="section-kicker">本地 AI 内容生产工作站</span>
          <h1>批量生产台</h1>
          <p>从维护的信息源和已审核素材中，生成人物主镜头与 B-roll 组合的抖音初版成片。</p>
        </header>

        {readiness.ready ? (
          <PixelPanel
            className="production-primary-task"
            description="系统将自动找选题、生成文案，并在最终成片阶段等待你集中审核。"
            title="创建下一个生产批次"
          >
            <form action={createProductionBatchAction} className="production-inline-form">
              <label>
                <span>批次名称</span>
                <input name="name" placeholder="例如：本周 AI 工具观察" />
              </label>
              <label>
                <span>成片数量</span>
                <input defaultValue="10" max="50" min="1" name="target_count" type="number" />
              </label>
              <button className="pixel-control-button pixel-control-button--primary" type="submit">
                创建批次
              </button>
            </form>
          </PixelPanel>
        ) : (
          <PixelBlockedState
            description={nextBlocker?.message ?? "还有生产基础未配置。"}
            title={onlyMaterialBlocked ? "当前只差 B-roll 审核" : "批次生产已阻断"}
          >
            {!onlyMaterialBlocked ? (
              <ol className="production-blocker-list">
                {readiness.blockers.map((blocker) => (
                  <li key={blocker.code}>{blocker.message}</li>
                ))}
              </ol>
            ) : null}
            {materialBlocker ? (
              <form action={scanProductionMaterialsAction} className="production-next-action">
                <button className="pixel-control-button pixel-control-button--primary" type="submit">
                  扫描 B-roll 素材目录
                </button>
              </form>
            ) : null}
          </PixelBlockedState>
        )}

        <PixelPanel
          actions={
            readiness.ready ? (
              <form action={scanProductionMaterialsAction}>
                <button className="pixel-control-button pixel-control-button--secondary" type="submit">
                  扫描素材目录
                </button>
              </form>
            ) : undefined
          }
          description="先处理待审核项。AI 会给出用途建议，但只有你确认版权、品牌安全和 B-roll 用途后，镜头才会进入自动混剪。"
          title="素材审核队列"
        >
          {library.assets.length ? (
            <>
              <dl className="production-review-summary">
                <div><dt>待审核</dt><dd>{pendingAssets.length}</dd></div>
                <div><dt>人物主镜头</dt><dd>{approvedARollCount}</dd></div>
                <div><dt>可用 B-roll</dt><dd>{approvedBRollCount}</dd></div>
                <div><dt>已处理</dt><dd>{processedAssets.length}</dd></div>
              </dl>
              {pendingAssets.length ? (
                <div className="production-asset-list">
                  {pendingAssets.slice(0, 20).map((asset) => (
                <article className="production-asset-row" key={asset.id}>
                  <div className="production-asset-row__main">
                    <div>
                      <strong>{asset.file_name}</strong>
                      <span>{asset.duration_seconds ? formatDuration(asset.duration_seconds) : "尚未探测时长"}</span>
                      {asset.review_status === "raw" && asset.analysis_status === "ready" ? (
                        <span title={asset.usage_role_reason}>
                          AI 建议：{usageRoleLabel(asset.suggested_usage_role)}
                          {asset.suggested_usage_role !== "unassigned"
                            ? ` · ${Math.round(asset.usage_role_confidence * 100)}%`
                            : ""}
                        </span>
                      ) : null}
                    </div>
                    <PixelStatus
                      kind={statusKind(asset.review_status === "raw" ? asset.analysis_status : asset.review_status)}
                      label={
                        asset.review_status === "approved"
                          ? asset.usage_role === "a_roll" ? "已标记为人物主镜头" : "已进入 B-roll 库"
                          : asset.usage_role === "exclude" ? "已标记不使用" : asset.analysis_message
                      }
                    />
                  </div>
                  <div className="production-asset-row__actions">
                    {asset.usage_role === "exclude" ? (
                      <span className="production-muted">已排除，不参与分析或混剪。</span>
                    ) : asset.analysis_status !== "ready" ? (
                      <form action={analyzeProductionMaterialAction}>
                        <input name="asset_id" type="hidden" value={asset.id} />
                        <button className="pixel-control-button pixel-control-button--secondary" type="submit">
                          分析镜头
                        </button>
                      </form>
                    ) : (
                      <form action={reviewProductionMaterialAction} className="production-review-form">
                        <input name="asset_id" type="hidden" value={asset.id} />
                        <input name="review_status" type="hidden" value="approved" />
                        <label>
                          <span>素材用途</span>
                          <select
                            defaultValue={
                              asset.usage_role === "unassigned"
                                ? asset.suggested_usage_role
                                : asset.usage_role
                            }
                            name="usage_role"
                            required
                          >
                            <option value="unassigned">请选择</option>
                            <option value="a_roll">人物主镜头</option>
                            <option value="b_roll">B-roll 补充画面</option>
                            <option value="exclude">不使用</option>
                          </select>
                        </label>
                        <label><input defaultChecked={asset.rights_confirmed} name="rights_confirmed" type="checkbox" /> 版权已确认</label>
                        <label><input defaultChecked={asset.brand_safe} name="brand_safe" type="checkbox" /> 品牌安全</label>
                        <button className="pixel-control-button pixel-control-button--secondary" type="submit">
                          保存素材用途
                        </button>
                      </form>
                    )}
                  </div>
                  {asset.analysis_status === "ready" && asset.segments.length ? (
                    <details className="production-segment-editor">
                      <summary>
                        编辑镜头语义（{asset.segments.length} 个镜头）
                      </summary>
                      <div className="production-segment-list">
                        {asset.segments.slice(0, 8).map((segment, index) => (
                          <form
                            action={updateProductionMaterialSegmentAction}
                            className="production-segment-row"
                            key={segment.id}
                          >
                            <input name="asset_id" type="hidden" value={asset.id} />
                            <input name="segment_id" type="hidden" value={segment.id} />
                            <input
                              name="review_status"
                              type="hidden"
                              value={asset.review_status === "approved" ? segment.review_status : "raw"}
                            />
                            <label className="production-segment-role">
                              <strong>镜头 {index + 1}</strong>
                              <select
                                defaultValue={
                                  segment.usage_role === "unassigned"
                                    ? segment.suggested_usage_role
                                    : segment.usage_role
                                }
                                name="usage_role"
                              >
                                <option value="unassigned">待判断</option>
                                <option value="a_roll">人物主镜头</option>
                                <option value="b_roll">B-roll</option>
                                <option value="exclude">不使用</option>
                              </select>
                              <span title={segment.usage_role_reason}>
                                建议 {usageRoleLabel(segment.suggested_usage_role)}
                                {segment.suggested_usage_role !== "unassigned"
                                  ? ` ${Math.round(segment.usage_role_confidence * 100)}%`
                                  : ""}
                              </span>
                            </label>
                            <span>
                              {segment.start_seconds.toFixed(1)}–{segment.end_seconds.toFixed(1)} 秒
                            </span>
                            <label>
                              <span>画面描述</span>
                              <input
                                defaultValue={segment.description}
                                name="description"
                                placeholder="例如：手机界面展示自动化流程"
                              />
                            </label>
                            <label>
                              <span>画面台词</span>
                              <input defaultValue={segment.transcript} name="transcript" />
                            </label>
                            <button className="pixel-control-button pixel-control-button--secondary" type="submit">
                              保存镜头依据
                            </button>
                          </form>
                        ))}
                      </div>
                      {asset.segments.length > 8 ? (
                        <p className="production-muted">当前先显示前 8 个镜头；完整镜头库将在素材处理页继续维护。</p>
                      ) : null}
                    </details>
                  ) : null}
                </article>
                  ))}
                </div>
              ) : (
                <p className="production-muted">没有待审核素材。扫描新文件后会在这里出现。</p>
              )}
              {processedAssets.length ? (
                <details className="production-processed-materials">
                  <summary>已处理素材（{processedAssets.length}）</summary>
                  <div className="production-asset-list">
                    {processedAssets.slice(0, 20).map((asset) => (
                      <article className="production-asset-row production-asset-row--compact" key={asset.id}>
                        <div className="production-asset-row__main">
                          <div>
                            <strong>{asset.file_name}</strong>
                            <span>{asset.duration_seconds ? formatDuration(asset.duration_seconds) : "尚未探测时长"}</span>
                          </div>
                          <PixelStatus
                            kind={statusKind(asset.review_status)}
                            label={
                              asset.usage_role === "a_roll"
                                ? "人物主镜头"
                                : asset.usage_role === "b_roll"
                                  ? "B-roll"
                                  : "不使用"
                            }
                          />
                        </div>
                        {asset.analysis_status === "ready" ? (
                          <details className="production-reopen-review">
                            <summary>更改素材用途</summary>
                            <form action={reviewProductionMaterialAction} className="production-review-form">
                              <input name="asset_id" type="hidden" value={asset.id} />
                              <input name="review_status" type="hidden" value="approved" />
                              <label>
                                <span>素材用途</span>
                                <select defaultValue={asset.usage_role} name="usage_role" required>
                                  <option value="unassigned">请选择</option>
                                  <option value="a_roll">人物主镜头</option>
                                  <option value="b_roll">B-roll 补充画面</option>
                                  <option value="exclude">不使用</option>
                                </select>
                              </label>
                              <label><input defaultChecked={asset.rights_confirmed} name="rights_confirmed" type="checkbox" /> 版权已确认</label>
                              <label><input defaultChecked={asset.brand_safe} name="brand_safe" type="checkbox" /> 品牌安全</label>
                              <button className="pixel-control-button pixel-control-button--secondary" type="submit">
                                保存更改
                              </button>
                            </form>
                          </details>
                        ) : null}
                      </article>
                    ))}
                  </div>
                </details>
              ) : null}
            </>
          ) : (
            <PixelEmptyState
              description="将 MP4、MOV 或 M4V 放入已配置的视频待处理目录，然后扫描。"
              title="素材库还是空的"
            />
          )}
        </PixelPanel>

        <PixelPanel
          description="每个镜头都保留文案、素材区间、匹配分数和选择依据；低置信匹配必须确认后才能渲染。"
          title="混剪时间线"
        >
          {foundation.recent_montage_plans.length ? (
            <div className="production-montage-list">
              {foundation.recent_montage_plans.map((plan) => {
                const pendingClipIds = plan.clips
                  .filter((clip) => clip.needs_confirmation)
                  .map((clip) => clip.id);
                return (
                  <article className="production-montage-row" key={plan.id}>
                    <header>
                      <div>
                        <strong>{plan.script_title}</strong>
                        <span>{formatDuration(plan.duration_seconds)} · {plan.clips.length} 个镜头</span>
                      </div>
                      <PixelStatus kind={statusKind(plan.status)} label={plan.message} />
                    </header>
                    <ol className="production-timeline-list">
                      {plan.clips.slice(0, 6).map((clip) => {
                        const cue = plan.cues.find((item) => item.id === clip.cue_id);
                        return (
                          <li key={clip.id}>
                            <span>{clip.timeline_start_seconds.toFixed(1)}–{clip.timeline_end_seconds.toFixed(1)} 秒</span>
                            <strong>{cue?.text ?? "未找到旁白段落"}</strong>
                            <span>
                              {clip.role === "a_roll" ? "人物主镜头" : "B-roll"}
                              {clip.role === "b_roll" ? ` · 匹配 ${Math.max(0, Math.round(clip.match_score * 100))}%` : ""}
                            </span>
                            <small>{clip.match_reasons.join("；")}</small>
                          </li>
                        );
                      })}
                    </ol>
                    <div className="production-montage-actions">
                      {pendingClipIds.length ? (
                        <form action={confirmMontageClipsAction}>
                          <input name="plan_id" type="hidden" value={plan.id} />
                          <input name="clip_ids" type="hidden" value={pendingClipIds.join(",")} />
                          <button className="pixel-control-button pixel-control-button--primary" type="submit">
                            确认这些低置信镜头
                          </button>
                        </form>
                      ) : plan.status === "ready" ? (
                        <form action={renderMontagePlanAction} className="production-render-form">
                          <input name="plan_id" type="hidden" value={plan.id} />
                          <label>
                            <span>旁白音频路径</span>
                            <input
                              defaultValue={plan.narration_audio_path ?? ""}
                              name="narration_audio_path"
                              required
                            />
                          </label>
                          <button className="pixel-control-button pixel-control-button--primary" type="submit">
                            渲染 9:16 初版成片
                          </button>
                        </form>
                      ) : null}
                      {plan.output_path ? (
                        <p className="production-output-paths">
                          成片：{plan.output_path}<br />字幕：{plan.subtitle_path}
                        </p>
                      ) : null}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <PixelEmptyState
              description="先创建生产批次，再为已确认文案的 Episode 生成可审阅时间线。"
              title="还没有混剪时间线"
            />
          )}
        </PixelPanel>

        <PixelPanel
          description="档案已经配置时保持收起，避免它打断当前素材审核。"
          title="生产档案"
        >
          <details className="production-profile-editor">
            <summary>
              {profile.brand_name || "未命名品牌"} · {profile.brand_template_name || "未配置模板"} · {profile.voice_name || "未配置声音"}
            </summary>
            <form action={updateProductionProfileAction} className="production-profile-form">
              <label><span>工作区名称</span><input defaultValue={profile.workspace_name} name="workspace_name" required /></label>
              <label><span>品牌名称</span><input defaultValue={profile.brand_name} name="brand_name" /></label>
              <label><span>固定模板名称</span><input defaultValue={profile.brand_template_name} name="brand_template_name" /></label>
              <label><span>品牌颜色</span><input defaultValue={profile.brand_colors.join(", ")} name="brand_colors" placeholder="#064A9B, #F4EFD9" /></label>
              <label className="production-field-wide"><span>字幕规则</span><textarea defaultValue={profile.subtitle_style} name="subtitle_style" rows={2} /></label>
              <label><span>片头文字</span><input defaultValue={profile.intro_text} name="intro_text" /></label>
              <label><span>片尾文字</span><input defaultValue={profile.outro_text} name="outro_text" /></label>
              <label className="production-field-wide"><span>BGM 本地路径</span><input defaultValue={profile.background_music_path ?? ""} name="background_music_path" /></label>
              <label><span>授权声音名称</span><input defaultValue={profile.voice_name} name="voice_name" /></label>
              <label><span>声音参考 WAV 文件</span><input defaultValue={profile.voice_reference_path ?? ""} name="voice_reference_path" /></label>
              <label className="production-field-wide"><span>参考音频对应文字</span><textarea defaultValue={profile.voice_reference_text} name="voice_reference_text" rows={2} /></label>
              <label className="production-check"><input defaultChecked={profile.voice_consent_confirmed} name="voice_consent_confirmed" type="checkbox" /> 我已获得该声音的生成与发布授权</label>
              <label className="production-field-wide"><span>来源 URL，每行一个</span><textarea defaultValue={profile.source_urls.join("\n")} name="source_urls" rows={3} /></label>
              <label className="production-check"><input defaultChecked={profile.use_obsidian} name="use_obsidian" type="checkbox" /> 使用 Obsidian 知识库</label>
              <label className="production-check"><input defaultChecked={profile.use_hermes} name="use_hermes" type="checkbox" /> 使用 Hermes 日报</label>
              <label className="production-check"><input defaultChecked={profile.cloud_enhancement_enabled} name="cloud_enhancement_enabled" type="checkbox" /> 允许用户自带密钥的云端增强</label>
              <div className="production-profile-form__submit">
                <button className="pixel-control-button pixel-control-button--primary" type="submit">保存生产档案</button>
              </div>
            </form>
          </details>
        </PixelPanel>

        <PixelPanel title="最近批次" description="当生产基础就绪后，批次才会进入自动选题队列。">
          {foundation.recent_batches.length ? (
            <div className="production-batch-list">
              {foundation.recent_batches.map((batch) => {
                const availableEpisodes = episodes.filter(
                  (episode) => !foundation.recent_montage_plans.some(
                    (plan) => plan.batch_id === batch.id && plan.episode_id === episode.id && plan.status !== "failed",
                  ),
                );
                return (
                <div className="production-batch-row" key={batch.id}>
                  <div className="production-batch-row__status">
                    <div>
                      <strong>{batch.name}</strong>
                      <span>
                        已成片 {batch.rendered_count}/{batch.target_count}
                        {batch.waiting_review_count ? ` · 待确认 ${batch.waiting_review_count}` : ""}
                        {batch.failed_count ? ` · 失败 ${batch.failed_count}` : ""}
                      </span>
                    </div>
                    <PixelStatus kind={statusKind(batch.status)} label={batch.message} />
                  </div>
                  {batch.items.length ? (
                    <ol className="production-blocker-list">
                      {batch.items.map((item) => (
                        <li key={item.episode_id}>
                          {item.episode_id} · {item.message}
                          {item.output_path ? ` · ${item.output_path}` : ""}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  {batch.status === "running" ? <ProcessingRefresh /> : null}
                  {batch.status !== "blocked" && batch.status !== "ready" && batch.status !== "reviewed" && batch.status !== "running" ? (
                    <form action={runProductionBatchAction}>
                      <input name="batch_id" type="hidden" value={batch.id} />
                      <button className="pixel-control-button pixel-control-button--primary" type="submit">
                        {batch.rendered_count || batch.failed_count || batch.status === "interrupted"
                          ? "继续自动批量生产"
                          : "开始自动批量生产"}
                      </button>
                    </form>
                  ) : null}
                  {batch.status !== "blocked" && batch.status !== "running" ? (
                    availableEpisodes.length ? (
                      <form action={createMontagePlanAction} className="production-plan-form">
                        <input name="batch_id" type="hidden" value={batch.id} />
                        <label>
                          <span>已确认文案</span>
                          <select name="episode_id" required>
                            {availableEpisodes.map((episode) => (
                              <option key={episode.id} value={episode.id}>
                                {episode.title}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>旁白音频路径（可稍后填写）</span>
                          <input name="narration_audio_path" />
                        </label>
                        <button className="pixel-control-button pixel-control-button--primary" type="submit">
                          生成配音与混剪时间线
                        </button>
                      </form>
                    ) : (
                      <p className="production-muted">
                        {episodes.length ? "当前已确认文案都已有可恢复时间线。" : "还没有已生成文案的 Episode。"}
                      </p>
                    )
                  ) : null}
                </div>
                );
              })}
            </div>
          ) : (
            <p className="production-muted">还没有生产批次。</p>
          )}
        </PixelPanel>
      </div>
    </AppShell>
  );
}
