import type { Metadata } from "next";
import { ToastProvider } from "@/components/PixelToast";
import "./globals.css";
import "./modern-overrides.css";

export const metadata: Metadata = {
  title: "智能视频日记",
  description: "从素材到成片的本机视频创作工具",
  icons: {
    icon: "/favicon.svg"
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var m=localStorage.getItem('ai-video-diary-theme')||'system';var d=m==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):m;document.documentElement.dataset.theme=d;document.documentElement.dataset.themeMode=m}catch(e){}})()`,
          }}
        />
      </head>
      <body>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
