import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MediaLinkApiError,
  checkMediaLink,
  importMediaLink,
  mediaLinkErrorMessage,
} from "../media-link-api";

afterEach(() => vi.unstubAllGlobals());

describe("media link API client", () => {
  it("checks a link before any download is requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          url: "https://media.example.com/clip.mp4",
          supported: true,
          media_type: "video",
          file_name: "link-abcd.mp4",
          size_bytes: 4096,
          message: "可接入的视频直链，约 4.0 KB",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await checkMediaLink("https://media.example.com/clip.mp4");

    expect(result.media_type).toBe("video");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/production/materials/link/check",
      expect.objectContaining({ method: "POST", cache: "no-store" }),
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      url: "https://media.example.com/clip.mp4",
    });
  });

  it("posts the import to the separate download endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          url: "https://media.example.com/clip.mp4",
          media_type: "video",
          file_name: "link-abcd.mp4",
          size_bytes: 4096,
          file_path: "/Volumes/Test/media/link-abcd.mp4",
          provenance_id: "provenance-1",
          reused_existing_file: false,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await importMediaLink("https://media.example.com/clip.mp4");

    expect(result.provenance_id).toBe("provenance-1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/production/materials/link/import");
  });

  it.each([
    ["social_page", 422, "这是社交平台页面，不是媒体文件直链，暂不支持。"],
    ["web_page_not_media", 422, "这是网页地址，不是媒体文件直链。"],
    ["credentials_required", 422, "该来源需要登录或凭据，暂不支持。"],
    ["private_address", 422, "该地址不是公开可访问的来源，暂不支持。"],
    ["incomplete_download", 502, "下载不完整，未登记。"],
    ["provenance_unavailable", 409, "出处尚未登记，本次导入未完成。"],
  ])("maps %s to a visible reason instead of a success message", async (code, status, expected) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { code, detail: "backend detail" } }), { status }),
      ),
    );

    await expect(checkMediaLink("https://media.example.com/clip.mp4")).rejects.toThrow(
      MediaLinkApiError,
    );
    expect(
      mediaLinkErrorMessage(new MediaLinkApiError("backend detail", status, code as never)),
    ).toBe(expected);
  });

  it("keeps a backend reason that has no known code", () => {
    expect(mediaLinkErrorMessage(new MediaLinkApiError("来源未声明大小", 400))).toBe("来源未声明大小");
  });
});