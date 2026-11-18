"use client";

import { useState } from "react";
import { scanMaterialIntake } from "@/lib/api";
import { HomeCardDialog } from "../HomeCardDialog";
import { MaterialIntakePanel } from "../material-library/MaterialIntakePanel";
import { visibleError } from "../material-library/material-errors";
import { useLocalMaterialImport } from "../material-library/useLocalMaterialImport";
import { useMaterialIntake } from "../material-library/useMaterialIntake";
import { Button } from "../ui/button";

function IntakeControls() {
  const directory = useMaterialIntake();
  const { intake } = directory;
  const files = useLocalMaterialImport(Boolean(intake?.bound_to_current_workspace && intake.directory_state === "ready"));
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");

  async function scan() {
    setScanning(true);
    setScanError("");
    try {
      await scanMaterialIntake();
      await directory.readIntakeAfterScan();
    } catch (error) {
      setScanError(visibleError(error, "检查素材目录失败，请重试。"));
    } finally {
      setScanning(false);
    }
  }

  return <>
    <MaterialIntakePanel intake={intake} intakeError={directory.intakeError} intakeBusy={directory.intakeBusy}
      busy={scanning ? "scan" : null} importBusy={files.importBusy} importResult={files.importResult}
      onRefresh={directory.refreshIntakeStatus} onBind={directory.bindIntake} onLocalImport={files.runImport}
      onScan={scan} onConfirmCopy={directory.confirmCopy} mediaLinkImport={null} />
    {scanError && <p role="alert">{scanError}</p>}
  </>;
}

export function WorkMaterialImport() {
  const [open, setOpen] = useState(false);
  return <>
    <Button type="button" variant="outline" onClick={() => setOpen(true)}>接入本地素材</Button>
    {open && <HomeCardDialog title="接入本地素材" onClose={() => setOpen(false)}><IntakeControls /></HomeCardDialog>}
  </>;
}
