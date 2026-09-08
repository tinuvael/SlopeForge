"""Application orchestration for revisioned Project design/actual surface datasets."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

from application.file_storage import (
    DATABASE_ONLY_STORAGE_MESSAGE,
    FileStorageUnavailableError,
)


class SurfaceImportResultPort(Protocol):
    source_format: str
    source_paths: tuple[Path, ...]

    @property
    def vertex_count(self) -> int: ...

    @property
    def triangle_count(self) -> int: ...


class StoredGeometryFilePort(Protocol):
    def to_dict(self) -> dict[str, object]: ...


class ProjectGeometryStoragePort(Protocol):
    available: bool

    def copy_dataset(
        self,
        site_id: int,
        kind: str,
        logical_id: str,
        source_paths: tuple[Path, ...],
    ) -> list[StoredGeometryFilePort]: ...

    def verify(
        self,
        relative_path: str,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> Path: ...

    def remove_dataset(self, site_id: int, kind: str, logical_id: str) -> None: ...


class ProjectSurfaceRepositoryPort(Protocol):
    def add_dataset(self, site_id: int, **values: Any) -> Any: ...
    def list_for_site(self, site_id: int, *, dataset_kind: str | None = None) -> list[Any]: ...
    def get_current(self, site_id: int, dataset_kind: str) -> Any | None: ...
    def get_by_logical_id(self, site_id: int, logical_id: str) -> Any: ...
    def update_semantic_mapping(self, site_id: int, logical_id: str, mapping: dict[str, object]) -> Any: ...


SurfaceImporter = Callable[[str | Path], SurfaceImportResultPort]


class ProjectSurfaceDatasetService:
    def __init__(
        self,
        repository: ProjectSurfaceRepositoryPort,
        storage: ProjectGeometryStoragePort,
        importer: SurfaceImporter,
    ):
        self.repository = repository
        self.storage = storage
        self.importer = importer

    @property
    def storage_available(self) -> bool:
        return bool(getattr(self.storage, "available", True))

    def _require_storage(self) -> None:
        if not self.storage_available:
            raise FileStorageUnavailableError(DATABASE_ONLY_STORAGE_MESSAGE)

    @staticmethod
    def _logical_id() -> str:
        return f"PG-{uuid4().hex[:8].upper()}"

    def import_dataset(
        self,
        site_id: int,
        dataset_kind: str,
        source_path: str | Path,
        *,
        imported_by_user_id: int | None = None,
    ):
        if dataset_kind not in {"design", "actual"}:
            raise ValueError(f"Unsupported Project surface kind: {dataset_kind!r}")
        self._require_storage()
        imported = self.importer(source_path)
        logical_id = self._logical_id()
        stored_files = self.storage.copy_dataset(
            site_id, dataset_kind, logical_id, imported.source_paths
        )
        try:
            row = self.repository.add_dataset(
                site_id,
                logical_id=logical_id,
                dataset_kind=dataset_kind,
                imported_at=datetime.now(timezone.utc),
                imported_by_user_id=imported_by_user_id,
                source_format=imported.source_format,
                source_files=[item.to_dict() for item in stored_files],
                vertex_count=imported.vertex_count,
                triangle_count=imported.triangle_count,
            )
        except Exception:
            self.storage.remove_dataset(site_id, dataset_kind, logical_id)
            raise
        return row, imported

    def list_for_site(self, site_id: int):
        return self.repository.list_for_site(site_id)

    def current(self, site_id: int, dataset_kind: str):
        return self.repository.get_current(site_id, dataset_kind)

    def load_dataset(self, site_id: int, logical_id: str) -> tuple[object, object]:
        self._require_storage()
        row = self.repository.get_by_logical_id(site_id, logical_id)
        paths = [
            self.storage.verify(
                str(file_metadata["relative_path"]),
                expected_size=int(file_metadata["file_size_bytes"]),
                expected_sha256=str(file_metadata["sha256"]),
            )
            for file_metadata in row.source_files_json
        ]
        if not paths:
            raise ValueError("Project surface dataset has no stored source files")
        result = self.importer(paths[0])
        return row, result

    def load_current(
        self, site_id: int, dataset_kind: str
    ) -> tuple[object, object] | None:
        row = self.repository.get_current(site_id, dataset_kind)
        if row is None:
            return None
        return self.load_dataset(site_id, row.logical_id)

    def save_design_semantic_mapping(self, site_id: int, logical_id: str, mapping):
        from domain.wall_conformance import SurfaceRoleMapping

        if not isinstance(mapping, SurfaceRoleMapping):
            raise TypeError("mapping must be a SurfaceRoleMapping")
        row = self.repository.get_by_logical_id(site_id, logical_id)
        if row.dataset_kind != "design":
            raise ValueError("Surface semantics may be saved only for Design datasets")
        return self.repository.update_semantic_mapping(
            site_id, logical_id, mapping.to_dict()
        )
