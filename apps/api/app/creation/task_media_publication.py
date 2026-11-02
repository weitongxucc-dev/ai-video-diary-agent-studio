"""Atomic artifact and provenance publication from existing record stores."""
from __future__ import annotations

from app.creation.contracts import ArtifactRecord as _ArtifactRecord, TransformRun as _TransformRun, DependencyEdge as _DependencyEdge


class TaskMediaPublicationTransaction:
    def __init__(self, *, connection_factory, execution_records, lineage_records):
        self._connect = connection_factory
        self.execution_records = execution_records
        self.lineage_records = lineage_records

    def record_task_media_publication(self, artifact: _ArtifactRecord, transform: _TransformRun, edges: list[_DependencyEdge]):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.execution_records.create_artifact_record(artifact, connection=conn)
            self.lineage_records.create_transform_run(transform, connection=conn)
            for edge in edges:
                self.lineage_records.create_dependency_edge(edge, connection=conn)
        return artifact
