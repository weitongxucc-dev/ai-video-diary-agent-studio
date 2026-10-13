export type KnowledgeNote = {
    title: string;
    ref: string;
    excerpt: string;
    modified_at: string;
    tags: string[];
    wikilinks: string[];
    outlinks: string[];
};
export type SourceItem = {
    source_type: string;
    title: string;
    ref: string;
    excerpt: string;
};
export type SourceItems = {
    items: SourceItem[];
    total_count: number;
    source_counts: Record<string, number>;
};
export type SourceSyncStatus = {
    source_type: string;
    configured: boolean;
    last_sync_at: string | null;
    last_success_at: string | null;
    last_failure_at: string | null;
    error_message: string | null;
};
export type SourcesStatus = {
    statuses: SourceSyncStatus[];
};
export type SourceSyncRetryResult = {
    source_type: string;
    status: string;
    message: string;
};
export type SourceEvidenceFreshness = "fresh" | "aging" | "stale" | "unknown";
export type TopicEvidence = {
    source_key: string;
    source_type: string;
    title: string;
    ref: string;
    excerpt: string;
    freshness: SourceEvidenceFreshness;
    freshness_label: string;
    participated_topics: number;
    last_used_at: string | null;
};
export type TopicEvidenceList = {
    episode_id: string;
    total: number;
    items: TopicEvidence[];
};
