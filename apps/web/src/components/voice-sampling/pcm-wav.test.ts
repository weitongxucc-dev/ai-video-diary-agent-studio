import { describe, expect, it } from "vitest";
import { encodePcmWav } from "./pcm-wav";

describe("recorded PCM serialization", () => {
  it("writes real channel count, sample rate, frame size and signed samples", async () => {
    const samples = new Float32Array(8000); samples[0] = -1; samples[1] = 1;
    const blob = encodePcmWav({numberOfChannels:1,length:8000,sampleRate:8000,getChannelData:()=>samples});
    const data = await new Promise<ArrayBuffer>((resolve,reject) => { const reader = new FileReader(); reader.onload=()=>resolve(reader.result as ArrayBuffer);reader.onerror=()=>reject(reader.error);reader.readAsArrayBuffer(blob); });
    const view = new DataView(data);
    expect(blob.type).toBe("audio/wav");
    expect(data.byteLength).toBe(16044);
    expect(view.getUint16(22,true)).toBe(1);
    expect(view.getUint32(24,true)).toBe(8000);
    expect(view.getUint32(40,true)).toBe(16000);
    expect(view.getInt16(44,true)).toBe(-32768);
    expect(view.getInt16(46,true)).toBe(32767);
  });
  it("rejects unusable duration and unsupported channel counts", () => {
    const audio={numberOfChannels:1,length:4000,sampleRate:8000,getChannelData:()=>new Float32Array(4000)};
    expect(()=>encodePcmWav(audio)).toThrow("1–120");
    expect(()=>encodePcmWav({...audio,length:8000,numberOfChannels:3})).toThrow("单声道或双声道");
  });
});
