import type { MediaTypeFilter } from './material-state';
export const mediaTypeOptions: MediaTypeFilter[] = ["all", "video", "audio", "image"];
export const mediaTypeLabels: Record<MediaTypeFilter, string> = {
    all: "全部",
    video: "视频",
    audio: "音频",
    image: "图片",
};
// 类型筛选和标签一样靠 URL 保留下来，刷新后从这里读回；读到的不是三个固定
// 类型之一就当没筛，免得手改地址栏把列表卡在一个服务端不认的值上。
export function readMediaTypeFromUrl(): MediaTypeFilter {
    if (typeof window === "undefined")
        return "all";
    const value = new URLSearchParams(window.location.search).get("media_type");
    return mediaTypeOptions.includes(value as MediaTypeFilter)
        ? (value as MediaTypeFilter)
        : "all";
}
