from __future__ import annotations

import os
import shutil
from pathlib import Path

import certifi


def curl_ca_bundle() -> str:
    """curl on Windows cannot open a CA path containing non-ASCII characters."""
    source = Path(certifi.where())
    if str(source).isascii():
        return str(source)
    root = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "BeautyInspector"
    target = root / "cacert.pem"
    root.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, target)
    return str(target)
