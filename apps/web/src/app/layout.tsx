import type { Metadata } from "next";
import { AssistantConversationProvider } from "@/components/AssistantConversationProvider";
import { ToastProvider } from "@/components/PixelToast";
import "./globals.css";
import "./modern-overrides.css";

export const metadata: Metadata = {
  title: "曜核1.0",
  description: "从素材到成片的本机视频创作工具",
  icons: {
    icon: { url: "/favicon.png", type: "image/png", sizes: "32x32" }
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
        <ToastProvider><AssistantConversationProvider>{children}</AssistantConversationProvider></ToastProvider>
      </body>
    </html>
  );
}
