import type { CreationTask, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
export type WorkspaceView = CreationTask["stage"] | "subtitle_cover" | "prepare" | "person" | "publish";
export const productStages = [
    { id: "prepare", label: "资料", title: "资料准备" },
    { id: "topic", label: "选题", title: "选题" },
    { id: "script", label: "文案", title: "文案" },
    { id: "narration", label: "配音", title: "配音" },
    { id: "person", label: "人物口型", title: "人物与口型" },
    { id: "montage", label: "剪辑", title: "视频剪辑" },
    { id: "subtitle_cover", label: "字幕封面", title: "字幕与封面" },
    { id: "review", label: "输出", title: "检查与输出" },
    { id: "publish", label: "发布", title: "发布与数据" },
] as const;
export const stageLabels = {
    prepare: "资料",
    topic: "选题",
    script: "文案",
    narration: "配音",
    person: "人物与口型",
    montage: "视频剪辑",
    subtitle_cover: "字幕与封面",
    review: "检查成片",
    publish: "发布",
};
export function defaultWorkspaceView(workspace: WorkspaceState): WorkspaceView {
    if (workspace.montage) return "montage";
    return workspace.task.stage;
}
export type ProductStageState = "done" | "skipped" | "current" | "upcoming";
// 人物与口型按 PRD 9.1 判断：露脸目标 0% 是明确跳过而不是完成；目标未记录不能当作 0%。
export function personStageFacts(workspace: WorkspaceState): {
    state: ProductStageState;
    detail: string;
} {
    const target = workspace.plan.presenter_share_target ?? workspace.montage?.presenter_share_target ?? null;
    if (target === 0)
        return { state: "skipped", detail: "明确跳过 · 目标 0%" };
    // 只有属于当前配音、人物与声音版本仍是当前方案的口型才算本阶段的当前产物；
    // 旧人物版本留下的记录不代表这一阶段做完了。
    const person = workspace.plan.selected_person;
    const voice = workspace.plan.selected_voice;
    const segmentIds = new Set(workspace.narration?.segments.map((segment) => segment.id) ?? []);
    const renders = workspace.person_renders.filter((render) => segmentIds.has(render.narration_segment_id)
        && Boolean(person)
        && render.person_profile_id === person?.profile_id
        && render.person_version === person?.version
        && (!voice || (render.voice_profile_id === voice.profile_id && render.voice_version === voice.version)));
    // 本任务真正需要的口型由成片镜头引用决定；有失败或缺覆盖证据时不能算整个阶段完成。
    const required = (workspace.montage?.clips ?? [])
        .map((clip) => clip.person_render_id)
        .filter((id): id is string => Boolean(id))
        .map((id) => renders.find((render) => render.id === id));
    if (required.length && required.every((render) => render?.status === "ready"))
        return { state: "done", detail: "已完成" };
    const ready = renders.filter((render) => render.status === "ready").length;
    if (renders.some((render) => render.status === "failed")) {
        return { state: "current", detail: ready ? `已生成 ${ready}/${renders.length} 段 · 仍有口型未完成` : "有口型需要处理" };
    }
    if (renders.length)
        return { state: "current", detail: `已生成 ${ready}/${renders.length} 段 · 仍待处理` };
    if (!workspace.narration)
        return { state: "upcoming", detail: "待配音" };
    if (target === null)
        return { state: "upcoming", detail: "未记录露脸目标" };
    if (!person)
        return { state: "current", detail: "尚未选择人物版本" };
    if (!workspace.person_render_capability.available)
        return { state: "current", detail: "口型能力不可用" };
    return { state: "current", detail: "待生成口型" };
}
// 阶段状态只由任务事实决定，不用阶段序号推断前面的阶段已完成。
export function productStageState(stageId: typeof productStages[number]["id"], workspace: WorkspaceState, subtitleCoverComplete: boolean): {
    state: ProductStageState;
    detail: string;
} {
    const { task, selected_topic: topic, scripts, narration, montage } = workspace;
    const rendered = Boolean(montage?.output_url);
    if (stageId === "prepare") {
        return task.plan_id ? { state: "done", detail: "已完成" } : { state: "upcoming", detail: "未确认方案" };
    }
    if (stageId === "publish")
        return { state: "upcoming", detail: "在发布工作区处理" };
    if (stageId === "topic") {
        if (topic)
            return { state: "done", detail: "已完成" };
        return task.stage === "topic" ? { state: "current", detail: "当前" } : { state: "upcoming", detail: "未开始" };
    }
    if (stageId === "script") {
        if (scripts[0]?.selection_status === "confirmed")
            return { state: "done", detail: "已完成" };
        if (scripts[0]?.selection_status === "draft" && scripts.some(script => script.id === task.confirmed_script_id && script.selection_status === "confirmed"))
            return { state: "current", detail: "有新草稿待确认" };
        if (task.stage === "script")
            return { state: "current", detail: "当前" };
        return scripts.length ? { state: "current", detail: "待确认" } : { state: "upcoming", detail: "未开始" };
    }
    if (stageId === "narration") {
        if (narration) {
            // 配音产物在生成中就已存在，必须按分段状态判定，不能因为对象存在就当成功。
            const segments = narration.segments;
            if (segments.some((segment) => segment.status === "failed"))
                return { state: "current", detail: "有配音段未完成" };
            if (segments.length && segments.every((segment) => segment.status === "ready"))
                return { state: "done", detail: "已完成" };
            if (segments.length)
                return { state: "current", detail: "配音生成中" };
        }
        return task.stage === "narration" ? { state: "current", detail: "当前" } : { state: "upcoming", detail: "未开始" };
    }
    if (stageId === "person")
        return personStageFacts(workspace);
    if (stageId === "montage") {
        if (rendered)
            return { state: "done", detail: "已完成" };
        if (montage)
            return { state: "current", detail: montage.status === "waiting_for_user" ? "待确认" : "故事板已生成" };
        return task.stage === "montage" ? { state: "current", detail: "当前" } : { state: "upcoming", detail: "未开始" };
    }
    if (stageId === "subtitle_cover") {
        if (subtitleCoverComplete)
            return { state: "done", detail: "已完成" };
        if (!rendered)
            return { state: "upcoming", detail: "未开始" };
        return { state: "current", detail: workspace.cover_asset?.status === "valid" ? "待补字幕" : "待补封面" };
    }
    if (!subtitleCoverComplete)
        return { state: "upcoming", detail: "待上一步" };
    if (task.stage === "review")
        return { state: "current", detail: "当前" };
    return rendered ? { state: "done", detail: "已完成" } : { state: "upcoming", detail: "未开始" };
}
export function canOpenStage(view: WorkspaceView | null, state: WorkspaceState): view is WorkspaceView {
    if (!view)
        return false;
    if (view === state.task.stage)
        return true;
    if (view === "prepare")
        return true;
    if (view === "topic")
        return Boolean(state.selected_topic);
    if (view === "script")
        return Boolean(state.scripts[0]);
    if (view === "narration")
        return Boolean(state.narration);
    if (view === "person")
        return Boolean(state.narration || state.person_renders.length);
    if (view === "montage")
        return Boolean(state.montage);
    if (view === "subtitle_cover")
        return Boolean(state.montage && state.subtitle_tracks.length);
    return (view === "review" || view === "publish") && Boolean(state.montage?.output_url);
}
