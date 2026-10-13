export type KnowledgeGrant = {
    id: string;
    knowledge_item_id: string;
    source_workspace_id: string;
    target_workspace_id: string;
    granted_by: string;
    granted_at: string;
    revoked_by: string | null;
    revoked_at: string | null;
};
export type KnowledgeGrantState = {
    can_manage: boolean;
    source_workspace_id: string;
    access_via_grant: boolean;
    target_resolution: "resolved" | "same_workspace" | "unresolved";
    target_resolution_message: string;
    cross_workspace_status: "AVAILABLE" | "UNVERIFIED";
    targets: {
        workspace_id: string;
        name: string;
    }[];
    grants: KnowledgeGrant[];
};
