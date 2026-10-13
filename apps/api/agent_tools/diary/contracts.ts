export type BusinessRequest = (path: string, init?: RequestInit) => Promise<any>;
export type ToolRegistrar = { registerTool: (tool: any) => void };
export type TaskVersion = { timeline_version_id: string | null; timeline_sequence: number };
export type ActionSource = { turnId?: string; userInstruction?: string; creationRequestId?: string };
