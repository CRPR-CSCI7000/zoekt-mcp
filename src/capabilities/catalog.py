from pathlib import Path
from typing import Any, Literal

import yaml

from .models import CapabilityDoc, CapabilityHit


class CapabilityCatalog:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self._docs: dict[str, CapabilityDoc] = {}
        self._hit_metadata: dict[str, dict[str, Any]] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        with self.manifest_path.open("r", encoding="utf-8") as manifest_file:
            raw = yaml.safe_load(manifest_file) or {}

        docs: dict[str, CapabilityDoc] = {}
        hit_metadata: dict[str, dict[str, Any]] = {}

        for workflow in raw.get("workflows", []):
            capability_id = workflow["id"]
            docs[capability_id] = self._entry_to_doc(workflow, kind="workflow")
            hit_metadata[capability_id] = self._entry_to_hit_metadata(workflow)

        for runtime_tool in raw.get("runtime_tools", []):
            capability_id = runtime_tool["id"]
            docs[capability_id] = self._entry_to_doc(runtime_tool, kind="runtime_tool")
            hit_metadata[capability_id] = self._entry_to_hit_metadata(runtime_tool)

        for pattern in raw.get("execution_patterns", []):
            capability_id = pattern["id"]
            docs[capability_id] = self._entry_to_doc(pattern, kind="execution_pattern")
            hit_metadata[capability_id] = self._entry_to_hit_metadata(pattern)

        self._docs = docs
        self._hit_metadata = hit_metadata

    @staticmethod
    def _entry_to_doc(entry: dict[str, Any], kind: str) -> CapabilityDoc:
        return CapabilityDoc(
            id=entry["id"],
            kind=kind,
            description=entry.get("description", ""),
            arg_schema=entry.get("arg_schema", {}),
            examples=entry.get("examples", []),
            constraints=entry.get("constraints", []),
            expected_output_shape=entry.get("expected_output_shape", {}),
        )

    @staticmethod
    def _entry_to_hit_metadata(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": entry.get("summary", ""),
            "when_to_use": entry.get("when_to_use", ""),
            "required_args": list(entry.get("required_args", [])),
            "example": entry.get("example", ""),
        }

    def list_capabilities(self) -> list[CapabilityHit]:
        return [self._doc_to_hit(doc, self._hit_metadata.get(doc.id)) for doc in self._docs.values()]

    def read(self, capability_id: str) -> CapabilityDoc | None:
        return self._docs.get(capability_id)

    @staticmethod
    def _doc_to_hit(doc: CapabilityDoc, metadata: dict[str, Any] | None = None) -> CapabilityHit:
        metadata = metadata or {}
        summary = str(metadata.get("summary") or doc.description.split("\n", maxsplit=1)[0] or "")
        when_to_use = str(metadata.get("when_to_use") or summary or "Use when needed.")
        required_args_from_schema = sorted(
            arg_name
            for arg_name, schema in doc.arg_schema.items()
            if isinstance(schema, dict) and schema.get("required")
        )
        required_args = list(metadata.get("required_args") or required_args_from_schema)
        example = str(metadata.get("example") or "")
        if not example and doc.examples:
            example = str(doc.examples[0].get("call") or doc.examples[0].get("args") or "")

        kind: Literal["workflow", "runtime_tool", "execution_pattern"] = "execution_pattern"
        if doc.kind in {"workflow", "runtime_tool", "execution_pattern"}:
            kind = doc.kind
        return CapabilityHit(
            id=doc.id,
            kind=kind,
            summary=summary,
            when_to_use=when_to_use,
            required_args=required_args,
            example=example,
        )
