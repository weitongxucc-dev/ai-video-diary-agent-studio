"use client";

import { createContext, useContext, type ReactNode } from "react";

/** Kept-mounted overlays must release media while their content is hidden. */
export const MediaVisibilityContext = createContext(true);

/** A hidden ancestor cannot be made visible by a nested editing panel. */
export function MediaVisibility({ visible, children }: { visible: boolean; children: ReactNode }) {
  const parentVisible = useContext(MediaVisibilityContext);
  return <MediaVisibilityContext.Provider value={parentVisible && visible}>{children}</MediaVisibilityContext.Provider>;
}
