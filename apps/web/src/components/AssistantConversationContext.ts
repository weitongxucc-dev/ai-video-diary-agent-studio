"use client";

import { createContext } from "react";
import type { useScopedAssistantConversation } from "./useAssistantConversation";

export const AssistantConversationContext = createContext<ReturnType<typeof useScopedAssistantConversation> | null>(null);
