"use client";
import { toggleHelpPanel } from "@/lib/helpPanel";
import { Button } from "./ui/button";
export function SettingsHelpButton() { return <Button variant="outline" type="button" onClick={toggleHelpPanel}>打开使用帮助</Button>; }
