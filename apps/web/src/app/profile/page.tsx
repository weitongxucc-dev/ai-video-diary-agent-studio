import { updateCreatorProfileAction } from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { getCreatorProfile } from "@/lib/api";
import type { CreatorProfile } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadCreatorProfile(): Promise<{
  profile: CreatorProfile | null;
  offline: boolean;
}> {
  try {
    return { profile: await getCreatorProfile(), offline: false };
  } catch {
    return { profile: null, offline: true };
  }
}

function joinLines(values: string[] | undefined): string {
  return (values ?? []).join("\n");
}

function formatDate(value: string | undefined): string {
  if (!value) {
    return "--";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function payloadList(profile: CreatorProfile | null, key: string): string {
  const value = profile?.prompt_payload[key];
  if (Array.isArray(value)) {
    return value.join(" / ") || "--";
  }
  if (typeof value === "string") {
    return value || "--";
  }
  return "--";
}

export default async function ProfilePage() {
  const { profile, offline } = await loadCreatorProfile();

  return (
    <AppShell>
      <div className="workspace-page profile-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">创作者画像</span>
              <h1>声音与边界</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : "这些稳定偏好会以短摘要进入选题、口播和段落重写。"}
              </p>
            </div>
          </div>

          <form action={updateCreatorProfileAction} className="settings-form">
            <div className="settings-grid">
              <label className="settings-field wide">
                <span>identity</span>
                <textarea
                  defaultValue={profile?.identity ?? ""}
                  name="identity"
                  rows={3}
                />
              </label>
              <label className="settings-field">
                <span>content_domains</span>
                <textarea
                  defaultValue={joinLines(profile?.content_domains)}
                  name="content_domains"
                  rows={7}
                />
              </label>
              <label className="settings-field">
                <span>tone_keywords</span>
                <textarea
                  defaultValue={joinLines(profile?.tone_keywords)}
                  name="tone_keywords"
                  rows={7}
                />
              </label>
              <label className="settings-field">
                <span>preferred_phrases</span>
                <textarea
                  defaultValue={joinLines(profile?.preferred_phrases)}
                  name="preferred_phrases"
                  rows={7}
                />
              </label>
              <label className="settings-field">
                <span>banned_phrases</span>
                <textarea
                  defaultValue={joinLines(profile?.banned_phrases)}
                  name="banned_phrases"
                  rows={7}
                />
              </label>
              <label className="settings-field">
                <span>good_examples</span>
                <textarea
                  defaultValue={joinLines(profile?.good_examples)}
                  name="good_examples"
                  rows={8}
                />
              </label>
              <label className="settings-field">
                <span>bad_examples</span>
                <textarea
                  defaultValue={joinLines(profile?.bad_examples)}
                  name="bad_examples"
                  rows={8}
                />
              </label>
            </div>
            <div className="settings-actions">
              <button className="pixel-button" disabled={offline} type="submit">
                保存画像
              </button>
              <span>更新 {formatDate(profile?.updated_at)}</span>
            </div>
          </form>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>Prompt 短摘要</h2>
            <p>{payloadList(profile, "identity")}</p>
          </div>
          <div className="pixel-card side-note">
            <h2>内容边界</h2>
            <p>{payloadList(profile, "content_domains")}</p>
          </div>
          <div className="pixel-card side-note">
            <h2>声音稳定器</h2>
            <p>{payloadList(profile, "voice")}</p>
          </div>
          <div className="pixel-card side-note">
            <h2>偏好表达</h2>
            <p>{payloadList(profile, "prefer")}</p>
          </div>
          <div className="pixel-card side-note">
            <h2>避开表达</h2>
            <p>{payloadList(profile, "avoid")}</p>
          </div>
          <div className="pixel-card side-note">
            <h2>画像状态</h2>
            <p>{offline ? "离线" : "已连接"}</p>
            <span>
              <PixelIcon name="person" /> Creator Profile v1
            </span>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
