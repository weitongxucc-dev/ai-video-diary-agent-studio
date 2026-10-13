export type MaterialSelection = (
  | { scope: "asset"; assetId: string }
  | { scope: "segment"; assetId: string; segmentId: string }
) & { expectedUpdatedAt?: string; expectedSegmentUpdatedAt?: string };

export type Segment = {
  updated_at: string;
  id: string;
  asset_id: string;
  description: string;
  transcript: string;
  review_status: "raw" | "approved" | "rejected";
  usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
  collection_ids: string[];
  system_tags: string[];
  user_tags: string[];
  start_seconds: number;
  end_seconds: number;
  visual_status: string;
  understanding: {
    status: string; summary: string; facts: { kind: string; label?: string | null; text: string; sample_times: number[] }[];
    unknowns: string[]; message: string; ocr_status: string;
    ocr_frames: { timestamp_seconds: number; observations: { text: string; confidence: number; retrieval_eligible: boolean }[] }[];
  } | null;
};
export type MaterialAsset = {
  updated_at: string;
  id: string; user_tags: string[]; segments: Segment[]; file_name: string; media_type: string;
  duration_seconds: number | null; use_state: string; analysis_status: string; analysis_message: string;
  user_description: string; upload_context: string;
  speech_analysis: { status: string; message: string; observations: { start_ms: number; end_ms: number; text: string }[] } | null;
};
export type MaterialPage = { assets: MaterialAsset[]; total_count: number; limit: number; offset: number; has_more: boolean };
export type MaterialToolsContext = {
  selection: MaterialSelection | null;
  request: (path: string, init?: RequestInit) => Promise<unknown>;
};
