from __future__ import annotations

from broad_oopif_grid_probe import main as broad_oopif_main
from test_readiness_grid_probe_legacy import *  # noqa: F401,F403
from test_readiness_grid_probe_legacy import main as legacy_main


def main() -> int:
    legacy_main()
    broad_oopif_main()
    print("PASS: standard and broad OOPIF/direct-children grid readiness verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
