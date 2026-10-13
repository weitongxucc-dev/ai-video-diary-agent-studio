"use client";
import { useState } from "react";
import { checkMediaLink, importMediaLink, mediaLinkErrorMessage, type MediaLinkCheck, type MediaLinkImport } from "@/lib/media-link-api";
export function useMediaLinkImport(onImported: () => Promise<void>) {
    const [linkUrl, setLinkUrl] = useState("");
    const [linkBusy, setLinkBusy] = useState<"check" | "import" | null>(null);
    const [linkCheck, setLinkCheck] = useState<MediaLinkCheck | null>(null);
    const [linkImport, setLinkImport] = useState<MediaLinkImport | null>(null);
    const [linkError, setLinkError] = useState("");
    async function runLinkCheck() {
        if (!linkUrl.trim())
            return;
        setLinkBusy("check");
        setLinkError("");
        setLinkImport(null);
        try {
            setLinkCheck(await checkMediaLink(linkUrl.trim()));
        }
        catch (cause) {
            setLinkCheck(null);
            setLinkError(mediaLinkErrorMessage(cause));
        }
        finally {
            setLinkBusy(null);
        }
    }
    async function runLinkImport() {
        if (!linkCheck?.supported)
            return;
        setLinkBusy("import");
        setLinkError("");
        try {
            const result = await importMediaLink(linkUrl.trim());
            setLinkImport(result);
            await onImported();
        }
        catch (cause) {
            setLinkImport(null);
            setLinkError(mediaLinkErrorMessage(cause));
        }
        finally {
            setLinkBusy(null);
        }
    }
    return { linkUrl, changeLinkUrl: (value: string) => setLinkUrl(value), linkBusy, linkCheck, linkImport, linkError, runLinkCheck, runLinkImport };
}
