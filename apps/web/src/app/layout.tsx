import type { Metadata } from "next";
import { WorkspaceSessionBoundary } from "@/components/WorkspaceSessionBoundary";
import { ToastProvider } from "@/components/PixelToast";
import { APPEARANCE_PALETTE_KEY, DEFAULT_PALETTE, WARM_PALETTE } from "@/lib/appearance-preferences";
import "./globals.css";
import "./modern-overrides.css";

export const metadata: Metadata = {
  title: "曜核AI剪辑",
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
            __html: `(function(){
              var mode = 'system';
              var palette = '${DEFAULT_PALETTE}';
              try {
                var savedMode = localStorage.getItem('ai-video-diary-theme');
                if (savedMode === 'light' || savedMode === 'dark') mode = savedMode;
                if (localStorage.getItem('${APPEARANCE_PALETTE_KEY}') === '${WARM_PALETTE}') palette = '${WARM_PALETTE}';
              } catch {}
              var appearance = document.documentElement.dataset;
              appearance.theme = mode === 'system'
                ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
                : mode;
              appearance.themeMode = mode;
              appearance.palette = palette;
            })()`,
          }}
        />
      </head>
      <body>
        <ToastProvider><WorkspaceSessionBoundary>{children}</WorkspaceSessionBoundary></ToastProvider>
      </body>
    </html>
  );
}
