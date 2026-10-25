#!/usr/bin/env python3
"""Check the installed package before starting or building the application."""
from importlib.resources import files

import bfsi
import jsonschema
import PIL

for name in ("standard_comment_schema_v0.1.json", "source_file_manifest_schema_v0.1.json", "simplified_semantic_schema_v0.1.json", "bulldozer_prompt_v0.1.md", "target_aliases_v0.1.json"):
    if not (files("bfsi") / "resources" / name).is_file():
        raise SystemExit("BFSI安装缺少资源：" + name)
print("BFSI installation OK; packaged contracts, prompt and aliases available.")
