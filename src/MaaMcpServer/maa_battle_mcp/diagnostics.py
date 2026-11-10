"""Read-only package identity and exact stage-data diagnostics."""

import hashlib
import json
from importlib.metadata import version

from .models import Settings


def provenance(settings: Settings) -> dict:
    manifest_path = settings.core_dir.parent / "BUILD-INFO.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else None
    )
    overview = settings.resource_dir / "resource/Arknights-Tile-Pos/overview.json"
    return {
        "project_version": version("maa-battle-mcp"),
        "build": manifest,
        "resources": {
            "root": str(settings.resource_dir),
            "map_catalog_sha256": (
                hashlib.sha256(overview.read_bytes()).hexdigest()
                if overview.is_file()
                else None
            ),
        },
    }


def stage_data(settings: Settings, stage_name: str) -> dict:
    root = settings.resource_dir / "resource/Arknights-Tile-Pos"
    overview = root / "overview.json"
    try:
        catalog = json.loads(overview.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Cannot read map catalog for stage {stage_name!r} "
            f"under resource root {settings.resource_dir}: {exc}"
        ) from exc
    matches = [
        entry
        for entry in catalog.values()
        if stage_name in (entry["stageId"], entry["levelId"])
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Stage {stage_name!r} has {len(matches)} exact stage/level ID matches "
            f"under resource root {settings.resource_dir}; "
            "use a unique stage/level ID, not a display code"
        )
    entry = matches[0]
    path = (root / entry["filename"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(
            f"Missing map data for stage {stage_name!r} "
            f"under resource root {settings.resource_dir}: {path}"
        )
    return {
        "stage_id": entry["stageId"],
        "level_id": entry["levelId"],
        "map_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
