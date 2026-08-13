"use client";

import { AppShell } from "@/components/AppShell";
import { classifyRouteError, RouteError } from "@/components/RouteError";

export default function EditPlanError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <AppShell>
      <div className="edit-plan-page">
        <section className="edit-plan-main">
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
