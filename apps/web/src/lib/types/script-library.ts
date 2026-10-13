export type ScriptLibrarySection = {
    id: string;
    title: string;
    content: string;
    visual_prompt: string;
    reference_ids: string[];
};
export type ScriptLibraryEntry = {
    id: string;
    name: string;
    category: string;
    version: number;
    supersedes_entry_id: string | null;
    source_script_id: string;
    source_task_id: string;
    source_run_id: string;
    source_topic_id: string;
    title: string;
    language: "zh-CN" | "en-US" | "zh-CN/en-US";
    sections: ScriptLibrarySection[];
    created_at: string;
    updated_at: string;
};
export type ScriptLibrarySource = {
    reference_id: string;
    status: "readable" | "missing" | "version_changed";
    title: string | null;
    version_or_identity: string | null;
    source_url: string | null;
    // 引用绑定那一版的真实快照事实（服务端按引用版本回填），用于回查当时内容。
    cited_version: string | null;
    cited_source_url: string | null;
    cited_fetched_at: string | null;
    cited_content_hash: string | null;
    // 引用绑定到具体段落/参数时的定位；引用只记到条目与版本时为 null。
    locator: string | null;
};
