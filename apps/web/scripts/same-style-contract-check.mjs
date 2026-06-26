import { readFile } from "node:fs/promises";

const sameStylePage = await readFile(
  new URL("../src/app/same-style-script/page.tsx", import.meta.url),
  "utf8",
);
const apiFile = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const actionsFile = await readFile(
  new URL("../src/app/actions.ts", import.meta.url),
  "utf8",
);

const failures = [];

for (const required of [
  "同款脚本生成",
  "reference_job",
  "audio_job",
  "digital_human_job",
  "subtitle_job",
  "bgm_job",
  "publish_copy_job",
  "cover_job",
  "publish_job",
  "video_job",
  "scriptDraftText",
  "startSameStyleReferenceJobAction",
  "startSameStyleAudioJobAction",
  "startSameStyleDigitalHumanJobAction",
  "startSameStyleSubtitleJobAction",
  "startSameStyleBgmJobAction",
  "startSameStylePublishCopyJobAction",
  "startSameStyleCoverJobAction",
  "startSameStylePublishJobAction",
  "startSameStyleVideoJobAction",
]) {
  if (!sameStylePage.includes(required)) {
    failures.push(`/same-style-script must include ${required}.`);
  }
}

for (const required of [
  "createSameStyleAudioJob",
  "getSameStyleAudioJob",
  "createSameStyleDigitalHumanJob",
  "getSameStyleDigitalHumanJob",
  "createSameStyleSubtitleJob",
  "getSameStyleSubtitleJob",
  "createSameStyleBgmJob",
  "getSameStyleBgmJob",
  "createSameStylePublishCopyJob",
  "getSameStylePublishCopyJob",
  "createSameStyleCoverJob",
  "getSameStyleCoverJob",
  "createSameStylePublishJob",
  "getSameStylePublishJob",
  "createSameStyleVideoJob",
  "getSameStyleVideoJob",
]) {
  if (!apiFile.includes(required)) {
    failures.push(`same-style API helpers must include ${required}.`);
  }
}

for (const required of [
  "startSameStyleAudioJobAction",
  "audio_status",
  "startSameStyleDigitalHumanJobAction",
  "digital_human_status",
  "startSameStyleSubtitleJobAction",
  "subtitle_status",
  "startSameStyleBgmJobAction",
  "bgm_status",
  "startSameStylePublishCopyJobAction",
  "publish_copy_status",
  "startSameStyleCoverJobAction",
  "cover_status",
  "startSameStylePublishJobAction",
  "publish_status",
  "startSameStyleVideoJobAction",
  "video_status",
]) {
  if (!actionsFile.includes(required)) {
    failures.push(`same-style server actions must include ${required}.`);
  }
}

for (const required of [
  "referenceJob?.message",
  "referenceJob?.manifest_path",
  "referenceJob?.reference_frames",
  "referenceJob?.reference_grid_path",
  "referenceJob?.status === \"failed\"",
]) {
  if (!sameStylePage.includes(required)) {
    failures.push(`/same-style-script failed job banner must use ${required}.`);
  }
}

if (failures.length > 0) {
  console.error(failures.map((failure) => `- ${failure}`).join("\n"));
  process.exit(1);
}

console.log("same-style contract checks passed");
