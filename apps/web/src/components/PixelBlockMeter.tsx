type PixelBlockMeterProps = {
  value: number;
  blocks?: number;
};

export function PixelBlockMeter({ value, blocks = 8 }: PixelBlockMeterProps) {
  const safeValue = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
  const filledBlocks = Math.round((safeValue / 100) * blocks);

  return (
    <span
      aria-label={`进度 ${Math.round(safeValue)}%`}
      className="pixel-block-meter"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(safeValue)}
    >
      {Array.from({ length: blocks }, (_, index) => (
        <span
          aria-hidden="true"
          className={index < filledBlocks ? "filled" : undefined}
          key={index}
        />
      ))}
    </span>
  );
}
