"""Shared paths; source assets and mutable local analysis data stay separate."""
import os
from pathlib import Path

def _project_root():
    if os.environ.get("BFSI_PROJECT_ROOT"):
        return Path(os.environ["BFSI_PROJECT_ROOT"]).expanduser().resolve()
    for candidate in (Path(__file__).resolve().parents[2], Path.cwd().resolve()):
        if (candidate / "pyproject.toml").is_file() and (candidate / "web").is_dir() and (candidate / "examples").is_dir():
            return candidate
    return Path.cwd().resolve()


ROOT = _project_root()
RESOURCES = Path(__file__).resolve().parent / "resources"
SCHEMAS = RESOURCES
DATA_DIR = Path(os.environ.get("BFSI_DATA_DIR", str(ROOT / "var"))).expanduser().resolve()
RUNS = DATA_DIR / "runs"
WEB_DIST = ROOT / "web" / "dist"
DEMO_PAYLOAD = ROOT / "examples" / "demo" / "payload.json"
