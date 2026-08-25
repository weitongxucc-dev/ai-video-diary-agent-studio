import { Bot, UserRound } from "lucide-react";

/** Compatibility name retained for route stability; the pixel artwork is retired. */
export function PixelMascot({ variant = "agent" }: { variant?: "agent" | "creator" }) {
  const Icon = variant === "creator" ? UserRound : Bot;
  return <span className={`modern-mascot modern-mascot--${variant}`} aria-hidden="true"><Icon size={28} strokeWidth={1.65} /></span>;
}
