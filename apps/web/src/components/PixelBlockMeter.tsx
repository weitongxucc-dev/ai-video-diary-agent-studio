type PixelBlockMeterProps = {
  value: number;
};

export function PixelBlockMeter({ value }: PixelBlockMeterProps) {
  const safeValue = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;

  return (
    <span
      aria-label={`进度 ${Math.round(safeValue)}%`}
      className="ui-progress-meter"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(safeValue)}
    >
      <span aria-hidden="true" style={{ width: `${safeValue}%` }} />
    </span>
  );
}
