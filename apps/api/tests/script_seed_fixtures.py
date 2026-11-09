"""Persist valid test scripts through the current row-backed script owner."""

from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.persistence_json import _with_datetime


def script_seed_payload(repository, connection, script):
    if isinstance(script, dict):
        metadata = _with_datetime({key: value for key, value in script.items() if key != "sections"},
                                  "created_at", "updated_at", "confirmed_at")
        script = CreationTaskScript(**metadata, sections=[CreationTaskScriptSection(**section)
                                                         for section in script["sections"]])
    return repository.script_records._script_payload(connection, script)
