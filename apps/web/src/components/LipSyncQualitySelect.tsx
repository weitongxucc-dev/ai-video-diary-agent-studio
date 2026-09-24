"use client";

export type LipSyncQuality = "high" | "fast";

export function LipSyncQualitySelect({ value, onChange, disabled = false }: {
  value: LipSyncQuality;
  onChange: (value: LipSyncQuality) => void;
  disabled?: boolean;
}) {
  return <fieldset className="lipsync-quality" disabled={disabled}>
    <legend>口型效果</legend>
    <div className="lipsync-quality__options">
      <label className={value === "high" ? "is-selected" : ""}>
        <input checked={value === "high"} name="lipsync-quality" onChange={() => onChange("high")} type="radio" value="high" />
        <span><strong>高精度</strong><small>LatentSync · 等待较久</small></span>
      </label>
      <label className={value === "fast" ? "is-selected" : ""}>
        <input checked={value === "fast"} name="lipsync-quality" onChange={() => onChange("fast")} type="radio" value="fast" />
        <span><strong>低精度</strong><small>MuseTalk · 较快</small></span>
      </label>
    </div>
  </fieldset>;
}
