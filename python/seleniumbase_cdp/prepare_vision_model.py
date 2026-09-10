from __future__ import annotations

import os


def main() -> int:
    model_name = os.environ.get("ARES_VISION_MODEL", "google/siglip2-base-patch16-224").strip()
    from transformers import AutoModel, AutoProcessor

    AutoProcessor.from_pretrained(model_name)
    AutoModel.from_pretrained(model_name)
    print(f"ARES vision model cached: {model_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
