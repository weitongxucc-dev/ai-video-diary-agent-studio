import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI视频日记 Agent Studio",
  description: "Pixel-style Agent workbench for AI video diary production",
  icons: {
    icon: "/favicon.svg"
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
