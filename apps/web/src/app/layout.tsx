import type { Metadata } from "next";
import { ToastProvider } from "@/components/PixelToast";
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
      <body>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
