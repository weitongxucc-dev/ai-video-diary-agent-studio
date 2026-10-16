/** Encode the recorded audio locally; no network or server media process runs here. */
export function encodePcmWav(audio: Pick<AudioBuffer, "numberOfChannels" | "length" | "sampleRate" | "getChannelData">): Blob {
  const channels = audio.numberOfChannels;
  if (channels < 1 || channels > 2 || audio.sampleRate <= 0 || audio.length / audio.sampleRate < 1 || audio.length / audio.sampleRate > 120) {
    throw new Error("声音样本须为 1–120 秒的单声道或双声道录音，请重录。");
  }
  const buffer = new ArrayBuffer(44 + audio.length * channels * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, buffer.byteLength - 8, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, channels, true); view.setUint32(24, audio.sampleRate, true);
  view.setUint32(28, audio.sampleRate * channels * 2, true); view.setUint16(32, channels * 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, buffer.byteLength - 44, true);
  const data = Array.from({ length: channels }, (_, index) => audio.getChannelData(index));
  for (let frame = 0; frame < audio.length; frame++) for (let channel = 0; channel < channels; channel++) {
    const sample = Math.max(-1, Math.min(1, data[channel][frame]));
    view.setInt16(44 + (frame * channels + channel) * 2, sample < 0 ? sample * 32768 : sample * 32767, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export async function recordedVoiceWav(recording: Blob): Promise<Blob> {
  if (typeof AudioContext === "undefined") throw new Error("当前环境无法读取录音，请保存录音后导入 PCM WAV。");
  const context = new AudioContext();
  try { return encodePcmWav(await context.decodeAudioData(await recording.arrayBuffer())); }
  finally { await context.close(); }
}
