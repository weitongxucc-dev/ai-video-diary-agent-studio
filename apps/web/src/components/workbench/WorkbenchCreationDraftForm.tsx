"use client";

import { useContext } from "react";
import { useRouter } from "next/navigation";
import { CreateProductionTaskForm, type CreateProductionTaskFormProps } from "../CreateProductionTaskForm";
import { WorkbenchRegistry, useWorkspacePage } from "./context";

export function WorkbenchCreationDraftForm(props: CreateProductionTaskFormProps) {
  const router = useRouter();
  const registry = useContext(WorkbenchRegistry);
  const page = useWorkspacePage();

  function isCurrentDraftAddress() {
    const current = new URL(window.location.href);
    return current.pathname === "/production" && current.searchParams.get("draft_id") === props.draftId;
  }

  function navigate(href: string) {
    if (page && (href === "/production" || href.startsWith("/tasks/"))) registry?.forget(page.id);
    if (isCurrentDraftAddress()) router.push(href);
  }

  function updateDraftAddress(href: string) {
    if (isCurrentDraftAddress()) {
      window.history.replaceState(null, "", href);
    }
  }

  return <CreateProductionTaskForm {...props} assignLocation={navigate} onDraftSaved={updateDraftAddress} />;
}
