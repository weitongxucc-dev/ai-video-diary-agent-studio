"use client";

import { AppShell } from "@/components/AppShell";
import { classifyRouteError, RouteError } from "@/components/RouteError";

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <AppShell>
      <div className="workspace-page">
        <div className="workspace-main">
          <RouteError
            error={error}
            errorCategory={classifyRouteError(error)}
            reset={reset}
          />
        </div>
      </div>
    </AppShell>
  );
}
