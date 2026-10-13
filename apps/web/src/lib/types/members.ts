export type LocalMember = {
    member_id: string;
    name: string;
    role: "owner" | "member";
    created_at: string;
};
export type LocalMemberListResponse = {
    members: LocalMember[];
};
