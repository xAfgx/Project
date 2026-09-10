from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "requirements-seleniumbase-vision.txt"
DEFAULT_MODEL = "google/siglip2-base-patch16-224"


def _dependencies_ready() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("torch", "PIL", "transformers"))


def _install_dependencies() -> None:
    subprocess.check_call([
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(REQUIREMENTS),
    ])


def _model_status(*, allow_download: bool) -> Dict[str, Any]:
    model_name = os.environ.get("ARES_VISION_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if not _dependencies_ready():
        return {"ready": False, "dependenciesReady": False, "model": model_name, "device": "unknown", "error": "Vision Python dependencies are not installed"}

    try:
        import torch
        from transformers import AutoModel, AutoProcessor

        processor = AutoProcessor.from_pretrained(model_name, local_files_only=not allow_download)
        model = AutoModel.from_pretrained(model_name, local_files_only=not allow_download)
        del processor
        del model
        return {
            "ready": True,
            "dependenciesReady": True,
            "model": model_name,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
            "error": "",
        }
    except Exception as exc:
        return {"ready": False, "dependenciesReady": True, "model": model_name, "device": "unknown", "error": str(exc)}


def status() -> Dict[str, Any]:
    return _model_status(allow_download=False)


def prepare() -> Dict[str, Any]:
    if not REQUIREMENTS.exists():
        return {"ready": False, "dependenciesReady": False, "error": f"Missing {REQUIREMENTS.name}"}
    if not _dependencies_ready():
        _install_dependencies()
    return _model_status(allow_download=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    args = parser.parse_args()

    try:
        result = prepare() if args.prepare else status()
    except Exception as exc:
        result = {"ready": False, "dependenciesReady": False, "error": str(exc)}

    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
