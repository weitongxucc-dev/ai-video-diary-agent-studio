"use client";

import type { CreationPlanInput } from "@/lib/types";

export type PresenterShare = NonNullable<CreationPlanInput["presenter_share_target"]>;

const stops = [0, 20, 40, 60, 80, 100] as const;

export function PresenterShareSlider({ value, onChange, disabled = false }: {
  value: PresenterShare;
  onChange: (value: PresenterShare) => void;
  disabled?: boolean;
}) {
  return <div className="presenter-share">
    <label htmlFor="presenter-share"><span>露脸占比</span><strong>{value}%</strong></label>
    <input aria-label="露脸占比" disabled={disabled} id="presenter-share" max="100" min="0" onChange={(event) => onChange(Number(event.target.value) as PresenterShare)} step="20" type="range" value={value} />
    <div aria-hidden="true" className="presenter-share__stops">{stops.map((stop) => <span key={stop}>{stop}%</span>)}</div>
    <p>仅露脸段需制作口型，其他画面沿用配音。</p>
  </div>;
}
