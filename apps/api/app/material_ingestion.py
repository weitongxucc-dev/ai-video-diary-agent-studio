"""Serial background intake for an explicitly bound local material directory."""

from __future__ import annotations

import logging
import threading

from app.production import MaterialAsset, ProductionRepository

logger = logging.getLogger(__name__)


class MaterialIngestionService:
    def __init__(
        self,
        repository: ProductionRepository,
        *,
        scan_interval_seconds: float = 60.0,
        stable_seconds: float = 10.0,
    ) -> None:
        if scan_interval_seconds <= 0 or stable_seconds < 0:
            raise ValueError("素材扫描和稳定等待时间无效")
        self.repository = repository
        self.scan_interval_seconds = scan_interval_seconds
        self.stable_seconds = stable_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._scan_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="material-ingestion", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def wake(self) -> None:
        """Schedule the next scan after binding or retry without blocking the request."""
        self._wake.set()

    def scan_once(self) -> list[MaterialAsset]:
        """Run one bounded intake cycle; safe to call beside the background thread."""
        analyzed: list[MaterialAsset] = []
        with self._scan_lock:
            binding = self.repository.get_material_ingestion_binding()
            if binding is None:
                return analyzed
            workspace_id, _ = binding
            for asset_id, digest in self.repository.scan_material_ingestion(stable_seconds=self.stable_seconds):
                if self._stop.is_set():
                    break
                try:
                    asset = self.repository.analyze_asset(workspace_id, asset_id, expected_source_digest=digest)
                except Exception as exc:
                    logger.exception("Material analysis failed for asset %s", asset_id)
                    self.repository.fail_material_ingestion(workspace_id, asset_id, digest, str(exc))
                    continue
                self.repository.finish_material_ingestion(
                    workspace_id, asset_id, digest, failed=asset.analysis_status == "failed"
                )
                analyzed.append(asset)
        return analyzed

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception("Material ingestion cycle failed")
            self._wake.wait(self.scan_interval_seconds)
            self._wake.clear()
