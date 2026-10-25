"""Shared filesystem paths for source and packaged Media Library runs."""
from __future__ import annotations

import os
from pathlib import Path


def get_data_dir(project_root: Path) -> Path:
    """Use portable project data in source mode and a stable user folder in EXE mode."""
    override = os.environ.get("MEDIALIBRARY_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    elif getattr(__import__("sys"), "frozen", False):
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MediaLibrary" / "data"
    else:
        path = project_root / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
