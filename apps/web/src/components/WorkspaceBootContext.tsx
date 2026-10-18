'use client';

import { createContext, useContext } from 'react';

// A composition gate for shell background reads, not an authorization source.
// The assembled application supplies it; standalone shells keep their API-owned checks.
export const WorkspaceBootContext = createContext<{ blocked: boolean; message?: string; href?: string; retry?: () => void }>({ blocked: false });
export function useWorkspaceBoot() { return useContext(WorkspaceBootContext); }
