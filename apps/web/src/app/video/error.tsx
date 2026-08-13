"use client";

import { AppShell } from "@/components/AppShell";
import { classifyRouteError, RouteError } from "@/components/RouteError";

export default function VideoError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <AppShell>
      <div className="video-page">
        <section className="video-main">
          <RouteError
            error={error}
            errorCategory={classifyRouteError(error)}
            reset={reset}
          />
        </section>
      </div>
    </AppShell>
  );
}
