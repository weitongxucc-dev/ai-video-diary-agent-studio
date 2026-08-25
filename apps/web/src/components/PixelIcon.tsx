import {
  Archive,
  Bell,
  Bot,
  CalendarDays,
  Check,
  Clapperboard,
  Code2,
  FileImage,
  FileText,
  Folder,
  Image,
  Inbox,
  Link2,
  MonitorPlay,
  NotebookPen,
  Package,
  PanelTop,
  PersonStanding,
  Search,
  Settings,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Upload,
  Video,
  type LucideIcon,
} from "lucide-react";

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

const ICONS: Record<PixelIconName, LucideIcon> = {
  agent: Bot,
  archive: Archive,
  bell: Bell,
  calendar: CalendarDays,
  check: Check,
  clip: Clapperboard,
  code: Code2,
  cover: FileImage,
  desk: PanelTop,
  doc: FileText,
  folder: Folder,
  image: Image,
  ingest: Inbox,
  link: Link2,
  note: NotebookPen,
  person: PersonStanding,
  product: Package,
  publish: MonitorPlay,
  script: FileText,
  search: Search,
  settings: Settings,
  spark: Sparkles,
  "thumb-down": ThumbsDown,
  "thumb-up": ThumbsUp,
  upload: Upload,
  video: Video,
};

/** Compatibility name retained while older routes migrate; renders modern line icons. */
export function PixelIcon({ name, className, title }: { name: PixelIconName; className?: string; title?: string }) {
  const Icon = ICONS[name];
  return <Icon aria-hidden={title ? undefined : true} aria-label={title} className={`modern-route-icon${className ? ` ${className}` : ""}`} role={title ? "img" : undefined} size={17} strokeWidth={1.8} />;
}
