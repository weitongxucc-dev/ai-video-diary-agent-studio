import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";

type MetricCardProps = {
  icon: PixelIconName;
  value: string;
  label: string;
  tone: string;
  detail?: string;
  progress?: number;
};

export function MetricCard({ icon, value, label, tone, detail, progress = 0 }: MetricCardProps) {
  const safeProgress = Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0;
  const filledBlocks = Math.round((safeProgress / 100) * 8);

  return (
    <div className={`metric-card ui-panel ${tone}`}>
      <PixelIcon name={icon} />
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
        {detail ? <small>{detail}</small> : null}
        <span className="metric-blocks" aria-label={`进度 ${Math.round(safeProgress)}%`}>
          {Array.from({ length: 8 }, (_, index) => (
            <i className={index < filledBlocks ? "filled" : undefined} key={index} />
          ))}
        </span>
      </div>
    </div>
  );
}
