import { afterEach, describe, expect, it, vi } from "vitest";
import { MediaProfilesApiError, createAvatarLipsyncPreview, createVoicePreview, getMediaCapability, importVoiceReference, listReferenceMaterials, mediaProfilesErrorMessage, saveVoiceProfile } from "../media-profiles-api";

afterEach(() => vi.unstubAllGlobals());

describe("media profiles API client", () => {
  it("reads registered materials with bounded pagination without an approval gate", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ assets: [], total_count: 0 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await listReferenceMaterials(100);
    expect(fetchMock).toHaveBeenCalledWith("/api/production/materials?limit=100&offset=100", expect.objectContaining({ cache: "no-store" }));
  });

  it("posts a versioned voice profile to its real endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "voice-1" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await saveVoiceProfile({ id: "voice-1", display_name: "Independent voice", person_profile_id: "person-1", person_version: "v1", version: "v1", language: "zh", reference_audio_path: "workspace://materials/asset-1", reference_text: "测试", model_id: "voice-clone", authorization: { id: "authorization-1", version: "v1", rights_holder: "本人", content_types: ["voice"], usage_scopes: ["narration", "avatar_lipsync"], effective_from: "2026-08-28T00:00:00Z", expires_at: null, evidence_reference: "workspace://proof/1", status: "active" } });
    expect(fetchMock).toHaveBeenCalledWith("/api/media/voice-profiles", expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toMatchObject({ reference_audio_path: "workspace://materials/asset-1" });
  });

  it("uploads WAV bytes without sending an absolute path or forcing a JSON content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ reference: "workspace://approved/voices/voice-safe.wav" }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["wav-bytes"], "owner.wav", { type: "audio/wav" });

    await importVoiceReference(file);

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init.method).toBe("POST");
    expect(init.headers).not.toHaveProperty("Content-Type");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("audio")).toBe(file);
  });

  it("turns an unavailable provider into an explicit product state", async () => {
    expect(mediaProfilesErrorMessage(new MediaProfilesApiError(501, "人物口型能力尚未配置。", "capability_unavailable"))).toBe("人物口型能力尚未配置。");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { error: "capability_unavailable", detail: "声音复制运行环境未配置。" } }), { status: 501 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getMediaCapability("voice-clone")).rejects.toBeInstanceOf(MediaProfilesApiError);
  });

  it("sends the VoicePreviewRequest body exactly", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "preview-2" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await createVoicePreview({ voice_profile_id: "voice-1", voice_version: "v1", preview_text: "测试", retry: true });
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({ voice_profile_id: "voice-1", voice_version: "v1", preview_text: "测试", retry: true });
  });

  it("sends the AvatarLipSyncPreviewRequest body exactly", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "preview-3" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await createAvatarLipsyncPreview({ person_profile_id: "person-1", person_version: "person-v2", voice_profile_id: "voice-1", voice_version: "voice-v3", voice_preview_identity: "voice-run", motion: "natural", expression: "neutral", authorization_id: "auth-1", authorization_version: "auth-v2", model_id: "avatar-model-v1", retry: false });
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({ person_profile_id: "person-1", person_version: "person-v2", voice_profile_id: "voice-1", voice_version: "voice-v3", voice_preview_identity: "voice-run", motion: "natural", expression: "neutral", authorization_id: "auth-1", authorization_version: "auth-v2", model_id: "avatar-model-v1", retry: false });
  });
});
