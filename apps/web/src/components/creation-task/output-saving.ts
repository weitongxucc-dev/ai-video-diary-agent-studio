import type { CreationTaskSubtitleTrack } from "@/lib/types";
import { errorDetails } from "./request-errors";
export type SaveFeedback = {
    error: ReturnType<typeof errorDetails> | null;
    notice: string | null;
};
export async function saveTaskVideo(taskId: string): Promise<SaveFeedback> {
    let error: SaveFeedback["error"] = null;
    let notice: string | null = null;
    const saver = window.electronAPI?.saveRenderedVideo;
    try {
        if (!saver)
            throw new Error("请在桌面客户端中使用原生保存功能。浏览器预览不会接收文件路径。");
        const result = await saver(taskId);
        if (result.error)
            throw new Error(result.error);
        if (result.saved)
            notice = `已保存${result.fileName ? `：${result.fileName}` : "到所选位置"}`;
        else if (result.canceled)
            notice = "已取消保存成片。";
    }
    catch (cause) {
        const details = errorDetails(cause);
        // 保存失败必须同时给普通人三件能判断的事：没保存成、为什么、接下来怎么做。
        // 桌面端返回的原始报错保留在 details.message 里，不被通用文案吞掉。
        error = saver
            ? { ...details, message: `成片没有保存到本地。${details.message}`, repairAction: "请先检查当前成片和本地服务是否可用（成片是否还在、本地服务是否已停止或返回错误），再检查保存位置是否可写（例如外接盘仍已连接、目录还有剩余空间）；问题恢复后再次点击「保存到本地」。" }
            : { ...details, message: `成片没有保存到本地。${details.message}` };
    }
    return { error, notice };
}
export async function saveTaskCover(taskId: string, coverId: string): Promise<SaveFeedback> {
    let error: SaveFeedback["error"] = null;
    let notice: string | null = null;
    try {
        const saver = window.electronAPI?.saveCoverImage;
        if (!saver)
            throw new Error("请在桌面客户端中保存封面；浏览器预览不支持本地保存。");
        const result = await saver(taskId, coverId);
        if (result.error)
            throw new Error(result.error);
        if (result.saved)
            notice = `封面已保存${result.fileName ? `：${result.fileName}` : "到所选位置"}`;
        else if (result.canceled)
            notice = "已取消保存封面。";
    }
    catch (cause) {
        error = errorDetails(cause);
    }
    return { error, notice };
}
export async function saveTaskSubtitles(taskId: string, mode: "zh" | "en" | "zh-en", versionId: string | null, tracks: CreationTaskSubtitleTrack[], activeTrack: CreationTaskSubtitleTrack | null): Promise<SaveFeedback> {
    let error: SaveFeedback["error"] = null;
    let notice: string | null = null;
    try {
        const usableTrack = (language: "zh-CN" | "en-US") => [activeTrack, ...tracks].find((track) => track?.language === language && track.status === "valid" && track.cues.length > 0);
        const chinese = usableTrack("zh-CN");
        const english = usableTrack("en-US");
        if (!versionId || (mode !== "en" && !chinese) || (mode !== "zh" && !english)) {
            throw new Error("当前版本缺少所选语言的有效字幕，请先检查或生成字幕。");
        }
        const saver = window.electronAPI?.saveSubtitleFile;
        if (!saver)
            throw new Error("请在桌面客户端中保存字幕；浏览器预览不支持本地保存。");
        const result = await saver({
            taskId: taskId,
            expectedVersionId: versionId,
            mode,
            ...(mode !== "en" ? { zhTrackId: chinese!.id } : {}),
            ...(mode !== "zh" ? { enTrackId: english!.id } : {}),
        });
        if (result.error)
            throw new Error(result.error);
        if (result.saved)
            notice = `字幕已保存${result.fileName ? `：${result.fileName}` : "到所选位置"}`;
        else if (result.canceled)
            notice = "已取消保存字幕。";
    }
    catch (cause) {
        const details = errorDetails(cause);
        error = { ...details, message: `字幕没有保存到本地。${details.message}`, repairAction: "当前任务与字幕草稿未修改；检查保存位置后可直接重试保存。" };
    }
    return { error, notice };
}
