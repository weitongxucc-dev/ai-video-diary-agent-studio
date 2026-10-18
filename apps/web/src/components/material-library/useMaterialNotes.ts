"use client";
import { useWorkspaceViewState } from "../workbench/context";
import { useEffect, useRef, useState } from "react";
import { updateMaterialNotes, transcribeMaterialVoiceNote } from "@/lib/api";
import type { MaterialAsset } from "@/lib/types";
import { visibleError } from "./material-errors";
export function useMaterialNotes(selectedAssetId: string | null, onAssetSaved: (asset: MaterialAsset) => void) {
    const [pending, setPending] = useState(false);
    const savedCallback = useRef(onAssetSaved);
    savedCallback.current = onAssetSaved;
    const [voiceNote, setVoiceNote] = useState<{
        assetId: string;
        controller: AbortController;
    } | null>(null);
    const voiceNoteRef = useRef<typeof voiceNote>(null);
    const [voiceNoteNotice, setVoiceNoteNotice] = useState("");
    const [noteDrafts, setNoteDrafts] = useWorkspaceViewState<Record<string, string>>("material-notes.noteDrafts", {});
    const [noteErrors, setNoteErrors] = useState<Record<string, string>>({});
    const [noteSaved, setNoteSaved] = useState<Record<string, boolean>>({});
    const [noteBatchIds, setNoteBatchIds] = useWorkspaceViewState<string[]>("material-notes.noteBatchIds", []);
    const [batchContext, setBatchContext] = useWorkspaceViewState("material-notes.batchContext", "");
    const [batchNoteError, setBatchNoteError] = useState("");
    const [batchNoteSaved, setBatchNoteSaved] = useState(false);
    useEffect(() => {
        const context = voiceNoteRef.current;
        if (context && context.assetId !== selectedAssetId) {
            context.controller.abort();
            voiceNoteRef.current = null;
            setVoiceNote(null);
            setVoiceNoteNotice("");
        }
    }, [selectedAssetId]);
    useEffect(() => () => {
        voiceNoteRef.current?.controller.abort();
        voiceNoteRef.current = null;
    }, []);
    function cancelVoiceNote() {
        voiceNoteRef.current?.controller.abort();
        voiceNoteRef.current = null;
        setVoiceNote(null);
    }
    function startVoiceNote(assetId: string) {
        cancelVoiceNote();
        const context = { assetId, controller: new AbortController() };
        voiceNoteRef.current = context;
        setVoiceNote(context);
        setVoiceNoteNotice("");
    }
    async function receiveVoiceNote(context: NonNullable<typeof voiceNote>, blob: Blob) {
        if (voiceNoteRef.current !== context)
            return;
        try {
            const transcript = await transcribeMaterialVoiceNote(blob, context.controller.signal);
            if (voiceNoteRef.current !== context)
                return;
            setNoteDrafts(current => ({ ...current, [context.assetId]: transcript }));
            setNoteSaved(current => ({ ...current, [context.assetId]: false }));
            setNoteErrors(current => ({ ...current, [context.assetId]: "" }));
            cancelVoiceNote();
            setVoiceNoteNotice("口述已转为可编辑草稿，尚未保存；请核对文字后保存本素材说明。");
        }
        catch (cause) {
            if (voiceNoteRef.current === context) {
                throw new Error(`${visibleError(cause, "转写失败")}。原文字保留，可重试或直接输入说明。`);
            }
        }
    }
    async function saveNotes(targets: MaterialAsset[], context?: string) {
        cancelVoiceNote();
        setVoiceNoteNotice("");
        const batch = context !== undefined;
        const assetId = targets[0].id;
        setPending(true);
        if (batch) {
            setBatchNoteError("");
            setBatchNoteSaved(false);
        }
        else {
            setNoteErrors(current => ({ ...current, [assetId]: "" }));
            setNoteSaved(current => ({ ...current, [assetId]: false }));
        }
        try {
            const result = await updateMaterialNotes(targets.map(asset => ({
                asset_id: asset.id,
                user_description: batch ? asset.user_description : noteDrafts[asset.id] ?? asset.user_description,
                upload_context: batch ? context : asset.upload_context,
            })));
            result.assets.forEach(asset => savedCallback.current(asset));
            if (batch)
                setBatchNoteSaved(true);
            else {
                setNoteDrafts(current => { const next = { ...current }; delete next[assetId]; return next; });
                setNoteSaved(current => ({ ...current, [assetId]: true }));
            }
        }
        catch (cause) {
            const message = `${visibleError(cause, "说明保存失败")}。草稿已保留，请重试保存。`;
            if (batch)
                setBatchNoteError(message);
            else
                setNoteErrors(current => ({ ...current, [assetId]: message }));
        }
        finally {
            setPending(false);
        }
    }
    function changeAssetNote(assetId: string, value: string) {
        if (voiceNoteRef.current) {
            cancelVoiceNote();
            setVoiceNoteNotice("说明已编辑，口述已取消；保留当前文字，可继续编辑保存。");
        }
        setNoteDrafts(current => ({ ...current, [assetId]: value }));
        setNoteSaved(current => ({ ...current, [assetId]: false }));
    }
    function cancelSpokenNote() {
        cancelVoiceNote();
        setVoiceNoteNotice("口述已取消，原文字保留，可直接输入说明。");
    }
    function chooseBatchAsset(assetId: string, included: boolean) {
        setNoteBatchIds(current => included ? [...current, assetId] : current.filter(id => id !== assetId));
        setBatchNoteSaved(false);
    }
    function changeBatchContext(value: string) {
        setBatchContext(value);
        setBatchNoteSaved(false);
    }
    function resetVoiceNote() { cancelVoiceNote(); setVoiceNoteNotice(""); }
    return { resetVoiceNote, pending, voiceNote, voiceNoteNotice, noteDrafts, noteErrors, noteSaved, noteBatchIds, batchContext, batchNoteError, batchNoteSaved,
        changeAssetNote, cancelSpokenNote, startVoiceNote, receiveVoiceNote, saveNotes, chooseBatchAsset, changeBatchContext };
}
