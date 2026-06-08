"use client";

import { useFormStatus } from "react-dom";

export function ScriptQuickRewriteButton({
  description,
  intentId,
  label,
}: {
  description: string;
  intentId: string;
  label: string;
}) {
  const { pending } = useFormStatus();

  return (
    <button
      aria-disabled={pending}
      className="pixel-button compact secondary"
      disabled={pending}
      name="rewrite_intent_id"
      title={description}
      type="submit"
      value={intentId}
    >
      {pending ? "处理中" : label}
    </button>
  );
}
