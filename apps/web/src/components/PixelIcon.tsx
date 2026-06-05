export type PixelIconName =
  | "agent"
  | "archive"
  | "bell"
  | "calendar"
  | "check"
  | "clip"
  | "code"
  | "cover"
  | "desk"
  | "doc"
  | "folder"
  | "image"
  | "ingest"
  | "link"
  | "note"
  | "person"
  | "product"
  | "publish"
  | "script"
  | "search"
  | "settings"
  | "spark"
  | "thumb-down"
  | "thumb-up"
  | "upload"
  | "video";

type PixelIconProps = {
  name: PixelIconName;
  className?: string;
  title?: string;
};

export function PixelIcon({ name, className, title }: PixelIconProps) {
  return (
    <span
      aria-hidden={title ? undefined : "true"}
      aria-label={title}
      className={`pixel-sprite pixel-sprite-${name}${className ? ` ${className}` : ""}`}
      role={title ? "img" : undefined}
    >
      <span className="pixel-sprite-core" />
    </span>
  );
}
