"use client";

import { createContext } from "react";

/** Kept-mounted overlays must release media while their content is hidden. */
export const MediaVisibilityContext = createContext(true);
