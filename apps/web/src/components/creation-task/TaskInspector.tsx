"use client";
import { UserRound } from "lucide-react";
import type { CreationTaskSubtitleCue, CreationTaskSubtitleTrack, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import type { WorkspaceView } from './stage-navigation';
import type { SelectedEditContext } from './edit-context';
import { stageLabels } from './stage-navigation';
import { capabilityLabel, lipsyncLabel, narrationSegmentStatus } from './stage-labels';
export function Inspector({ activeView, busy, task, plan, topic, scripts, narration, montage, personRenders, personRenderCapability, onPreparePersonEdit, selectedContext }: {
    activeView: WorkspaceView;
    busy: boolean;
    task: WorkspaceState["task"];
    plan: WorkspaceState["plan"];
    topic: WorkspaceState["selected_topic"];
    scripts: WorkspaceState["scripts"];
    narration: WorkspaceState["narration"];
    montage: WorkspaceState["montage"];
    personRenders: WorkspaceState["person_renders"];
    personRenderCapability: WorkspaceState["person_render_capability"];
    onPreparePersonEdit: (clipId: string, command: string) => void;
    selectedContext: SelectedEditContext | null;
}) {
    const latestScript = scripts[0] ?? null;
    const currentNarration = narration?.task.id === task.id && narration.script_id === task.confirmed_script_id
        ? narration
        : null;
    const viewingCurrentStage = activeView === task.stage;
    const selectedNarration = selectedContext?.kind === "narration" ? narration?.segments.find((segment) => segment.id === selectedContext.id) ?? null : null;
    const selectedClip = selectedContext?.kind === "clip" ? montage?.clips.find((clip) => clip.id === selectedContext.id) ?? null : null;
    const selectedPersonRender = selectedClip?.person_render_id
        ? personRenders.find((render) => render.id === selectedClip.person_render_id) ?? null
        : null;
    const selectedPersonNarration = selectedPersonRender
        ? narration?.segments.find((segment) => segment.id === selectedPersonRender.narration_segment_id) ?? null
        : null;
    const personRenderCanRegenerate = selectedPersonRender?.status === "ready" || selectedPersonRender?.status === "failed";
    const personRenderStatus = selectedPersonRender?.status === "ready"
        ? "已写入当前版本"
        : selectedPersonRender?.status === "failed"
            ? "生成失败，可重新对口型"
            : selectedPersonRender?.status === "running"
                ? "正在生成口型"
                : selectedPersonRender?.status === "pending"
                    ? "等待生成口型"
                    : selectedClip?.person_render_id
                        ? "人物片段记录缺失"
                        : "尚未写入";
    return (<div className={styles.inspector}>
      <section>
        <span className={styles.eyebrow}>{viewingCurrentStage ? "当前状态" : "正在回看"}</span>
        <h3>{stageLabels[activeView]}</h3>
        <p>{viewingCurrentStage ? (task.stage_status === "failed" ? "当前步骤需要处理" : task.stage_status === "waiting_for_user" ? "正在等待你的确认" : task.stage_status === "running" ? "正在处理" : "可以开始下一步") : "这是已经持久化的任务结果，不会因为回看而重新运行。"}</p>
      </section>
      {selectedNarration ? <section aria-label="已选配音详情"><span className={styles.eyebrow}>已选配音</span><h3>{selectedContext?.label}</h3><p>{selectedNarration.text}</p><dl><div><dt>片段时长</dt><dd>{selectedNarration.duration_seconds?.toFixed(1) ?? "—"} 秒</dd></div><div><dt>声音版本</dt><dd>{selectedNarration.voice_version}</dd></div><div><dt>模型</dt><dd>{selectedNarration.model_id}</dd></div><div><dt>状态</dt><dd>{narrationSegmentStatus(selectedNarration.status)}</dd></div></dl></section> : null}
      {selectedClip ? <section aria-label="已选镜头详情">
        <span className={styles.eyebrow}>已选镜头</span><h3>{selectedContext?.label}</h3>
        <dl>
          <div><dt>用途</dt><dd>{selectedClip.person_render_id ? "人物口型" : selectedClip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div>
          <div><dt>成片位置</dt><dd>{selectedClip.timeline_start_seconds.toFixed(1)}–{selectedClip.timeline_end_seconds.toFixed(1)} 秒</dd></div>
          <div><dt>人物版本</dt><dd>{selectedPersonRender ? `${selectedPersonRender.person_profile_id} · ${selectedPersonRender.person_version}` : selectedClip.person_render_id ? "未读取到人物片段记录" : plan.selected_person ? `${plan.selected_person.profile_id} · ${plan.selected_person.version}` : "尚未选择人物版本"}</dd></div>
          {selectedPersonRender ? <>
            <div><dt>声音版本</dt><dd>{selectedPersonRender.voice_profile_id} · {selectedPersonRender.voice_version}</dd></div>
            <div><dt>语言</dt><dd>{selectedPersonNarration?.language === "zh" ? "中文" : "未记录"}</dd></div>
            <div><dt>口型效果</dt><dd>{lipsyncLabel(selectedPersonRender.model_id)}</dd></div>
            <div><dt>动作</dt><dd>{selectedPersonRender.motion === "natural" ? "自然（固定）" : selectedPersonRender.motion}</dd></div>
            <div><dt>表情</dt><dd>{selectedPersonRender.expression === "neutral" ? "中性（固定）" : selectedPersonRender.expression}</dd></div>
            <div><dt>口型偏移</dt><dd>自动匹配（暂不可手动调整）</dd></div>
          </> : null}
          <div><dt>片段状态</dt><dd>{personRenderStatus}</dd></div>
          <div><dt>锁定</dt><dd>{selectedClip.locked ? "已锁定" : "可调整"}</dd></div>
          {(plan.presenter_share_target ?? montage?.presenter_share_target) !== 0 ? <div><dt>当前口型</dt><dd>{lipsyncLabel(personRenderCapability.model_id)} · {personRenderCapability.available ? "可用" : "不可用"}</dd></div> : null}
        </dl>
        {(plan.presenter_share_target ?? montage?.presenter_share_target) !== 0 && !personRenderCapability.available ? <div aria-label="人物口型不可用原因" className={styles.personCapabilityIssue} role="status"><strong>人物口型暂不可用</strong><p>{personRenderCapability.message}</p>{personRenderCapability.repair_action ? <small>处理方式：{personRenderCapability.repair_action}</small> : null}</div> : null}
        {selectedPersonRender ? <p className={styles.personCapability}>动作/表情沿用原人物素材，口型匹配当前配音。</p> : null}
        {(plan.presenter_share_target ?? montage?.presenter_share_target) !== 0 ? !selectedClip.person_render_id
                ? <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available} onClick={() => onPreparePersonEdit(selectedClip.id, "这一段换成当前人物出镜")} type="button"><UserRound size={15}/>换成当前人物出镜</button>
                : <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available || !personRenderCanRegenerate} onClick={() => onPreparePersonEdit(selectedClip.id, "这里只重做口型，其他都不要动")} type="button"><UserRound size={15}/>重新对口型</button>
                : null}
      </section> : null}
      {activeView === "topic" ? <section><span className={styles.eyebrow}>事实来源</span><dl><div><dt>知识库</dt><dd>{plan.knowledge_references.length} 条</dd></div><div><dt>信息源</dt><dd>{plan.source_references.length} 条</dd></div><div><dt>已确认选题</dt><dd>{topic?.title ?? "尚未确认"}</dd></div></dl></section> : null}
      {activeView === "script" ? <section><span className={styles.eyebrow}>文案版本</span><dl><div><dt>标题</dt><dd>{latestScript?.title ?? "尚未生成"}</dd></div><div><dt>段落</dt><dd>{latestScript?.sections.length ?? 0} 段</dd></div><div><dt>状态</dt><dd>{latestScript?.selection_status === "confirmed" ? "已确认" : "等待确认"}</dd></div></dl></section> : null}
      {activeView === "narration" ? <section><span className={styles.eyebrow}>{currentNarration?.source_kind === "recorded" ? "本人录音" : "声音与授权"}</span>
        {currentNarration?.source_kind === "recorded" ? <dl><div><dt>来源</dt><dd>本人录音 · 已采用</dd></div><div><dt>录音</dt><dd>{currentNarration.source_artifact_ref ?? "任务录音"}</dd></div><div><dt>实际时长</dt><dd>{currentNarration.duration_seconds.toFixed(1)} 秒</dd></div><div><dt>声音模型</dt><dd>本人录音，不使用声音克隆模型</dd></div></dl>
                : <dl><div><dt>声音版本</dt><dd>{plan.selected_voice?.version ?? "未选择"}</dd></div><div><dt>语言</dt><dd>中文</dd></div><div><dt>模型</dt><dd>{currentNarration?.model_id ?? plan.selected_voice?.model_id ?? "未就绪"}</dd></div><div><dt>授权</dt><dd>{plan.selected_voice ? "已纳入方案" : "缺失"}</dd></div></dl>}</section> : null}
      {(activeView === "montage" || activeView === "review") && montage ? <section><span className={styles.eyebrow}>剪辑证据</span><dl><div><dt>镜头</dt><dd>{montage.clips.length} 个</dd></div><div><dt>露脸</dt><dd>目标 {montage.presenter_share_target ?? 0}% · 实际 {montage.presenter_share_actual ?? 0}%</dd></div><div><dt>需要确认</dt><dd>{montage.low_confidence_clip_count} 个</dd></div><div><dt>时长</dt><dd>{montage.duration_seconds.toFixed(1)} 秒</dd></div></dl></section> : null}
      {activeView === "prepare" ? <section><span className={styles.eyebrow}>本次素材</span><dl><div><dt>方案匹配</dt><dd>{plan.material_matches.length} 个片段</dd></div><div><dt>方案可用素材</dt><dd>{plan.approved_asset_count} 个</dd></div><div><dt>方案可用镜头</dt><dd>{plan.approved_clip_count} 个</dd></div></dl></section> : null}
      {activeView === "publish" ? <section><span className={styles.eyebrow}>发布能力</span><dl><div><dt>发布记录</dt><dd>在发布工作区读取</dd></div><div><dt>账号与确认</dt><dd>发布前核对</dd></div><div><dt>本任务结果</dt><dd>未返回发布结果</dd></div></dl></section> : null}
    </div>);
}
export function SubtitleCoverInspector({ selectedCover, selectedCue, selectedTrack, cover, translationCapability, frameCapability, imageCapability, }: {
    selectedCover: boolean;
    selectedCue: CreationTaskSubtitleCue | null;
    selectedTrack: CreationTaskSubtitleTrack | null;
    cover: WorkspaceState["cover_asset"];
    translationCapability: WorkspaceState["subtitle_translation_capability"];
    frameCapability: WorkspaceState["cover_frame_capability"];
    imageCapability: WorkspaceState["image_generation_capability"];
}) {
    if (selectedCue && selectedTrack) {
        return (<div className={styles.inspector}>
        <section aria-label="已选字幕详情">
          <span className={styles.eyebrow}>{selectedTrack.language === "zh-CN" ? "中文字幕" : "英文字幕"}</span>
          <h3>{selectedCue.text}</h3>
          <dl>
            <div><dt>时间</dt><dd>{selectedCue.start_seconds.toFixed(1)}–{selectedCue.end_seconds.toFixed(1)} 秒</dd></div>
            <div><dt>样式</dt><dd>{selectedTrack.style.name === "emphasis" ? "重点强调" : "清爽底栏"}</dd></div>
            <div><dt>断行</dt><dd>{selectedCue.line_breaks.length ? `${selectedCue.line_breaks.length + 1} 行` : "单行"}</dd></div>
            <div><dt>强调</dt><dd>{selectedCue.emphasis.length ? `${selectedCue.emphasis.length} 处` : "无"}</dd></div>
            <div><dt>字幕产物</dt><dd>{selectedTrack.artifact_ref ?? "尚未生成"}</dd></div>
            <div><dt>运行证据</dt><dd>{selectedTrack.transform_id ?? "尚无"}</dd></div>
            {selectedTrack.language === "en-US" ? <div><dt>翻译能力</dt><dd>{capabilityLabel(translationCapability.available)} · {translationCapability.model_id ?? "未配置模型"}</dd></div> : null}
          </dl>
          {selectedTrack.language === "en-US" && !translationCapability.available && translationCapability.repair_action ? <p className={styles.inspectorRepair}>处理方式：{translationCapability.repair_action}</p> : null}
        </section>
      </div>);
    }
    if (selectedCover) {
        return (<div className={styles.inspector}>
        <section aria-label="已选封面详情">
          <span className={styles.eyebrow}>封面参数</span>
          <h3>{cover?.title || "待生成封面"}</h3>
          <dl>
            <div><dt>真实画面</dt><dd>{cover ? `${cover.frame_time_seconds.toFixed(1)} 秒` : "尚未确认"}</dd></div>
            <div><dt>标题位置</dt><dd>{cover ? cover.layout.title_position === "center" ? "画面中部" : "画面底部" : "尚未确认"}</dd></div>
            <div><dt>安全区</dt><dd>{cover ? `${cover.safe_area.top}/${cover.safe_area.right}/${cover.safe_area.bottom}/${cover.safe_area.left}` : "默认安全区"}</dd></div>
            <div><dt>图片产物</dt><dd>{cover?.artifact_ref ?? "尚未生成"}</dd></div>
            <div><dt>运行证据</dt><dd>{cover?.transform_id ?? "尚无"}</dd></div>
            <div><dt>选帧能力</dt><dd>{capabilityLabel(frameCapability.available)} · {frameCapability.model_id ?? "未配置引擎"}</dd></div>
            <div><dt>AI 生图</dt><dd>{capabilityLabel(imageCapability.available)} · {imageCapability.model_id ?? "未配置模型"}</dd></div>
          </dl>
          {!frameCapability.available && frameCapability.repair_action ? <p className={styles.inspectorRepair}>处理方式：{frameCapability.repair_action}</p> : null}
        </section>
      </div>);
    }
    return null;
}
