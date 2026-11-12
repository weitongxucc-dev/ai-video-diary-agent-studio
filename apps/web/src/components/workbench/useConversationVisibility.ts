"use client";

import { useEffect, useState } from "react";
import { ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, ASSISTANT_MATERIAL_REFERENCE_EVENT, ASSISTANT_PROFILE_REFERENCE_EVENT, ASSISTANT_SELECTION_EVENT } from "@/lib/assistant-reference";

/** Viewing resources must not inherit a previous work's panel layout. */
export function useConversationVisibility({ pathname, creation, enabled, collapsed, open, toggle, onReference }: {
  pathname: string; creation: boolean; enabled: boolean; collapsed: boolean;
  open: () => void; toggle: () => void; onReference: () => void;
}) {
  const [requestedPage, setRequestedPage] = useState<string | null>(null);
  const available = enabled && (creation || requestedPage === pathname);
  useEffect(() => {
    if (!enabled) return;
    const reference = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (!detail || ("items" in detail && detail.items.length === 0)) return;
      setRequestedPage(pathname);
      open();
      onReference();
    };
    const events = [ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, ASSISTANT_MATERIAL_REFERENCE_EVENT, ASSISTANT_PROFILE_REFERENCE_EVENT, ASSISTANT_SELECTION_EVENT];
    events.forEach(event => window.addEventListener(event, reference));
    return () => events.forEach(event => window.removeEventListener(event, reference));
  }, [pathname, enabled, open, onReference]);
  return {
    hidden: !available || collapsed,
    toggle: () => {
      if (!available) { setRequestedPage(pathname); open(); }
      else toggle();
    },
  };
}
