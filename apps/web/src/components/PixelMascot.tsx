export function PixelMascot({ variant = "agent" }: { variant?: "agent" | "creator" }) {
  return (
    <span className={`pixel-mascot pixel-mascot-${variant}`} aria-hidden="true">
      <span className="pixel-mascot-head">
        <span className="pixel-mascot-eye left" />
        <span className="pixel-mascot-eye right" />
        <span className="pixel-mascot-mouth" />
      </span>
      <span className="pixel-mascot-body" />
      <span className="pixel-mascot-arm left" />
      <span className="pixel-mascot-arm right" />
    </span>
  );
}
