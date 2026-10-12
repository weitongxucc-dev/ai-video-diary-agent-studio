"use client";
import { toggleHelpPanel } from "@/lib/helpPanel";
export function SettingsHelpButton() { return <button type="button" onClick={toggleHelpPanel}>打开使用帮助</button>; }
