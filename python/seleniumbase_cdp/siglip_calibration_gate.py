from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "siglip_calibration_latency_probe.py"


def _match(pattern: str, text: str, name: str) -> re.Match[str]:
    match = re.search(pattern, text)
    if match is None:
        raise AssertionError(f"Missing {name} metric in SigLIP calibration output")
    return match


def main() -> int:
    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=str(ROOT),
        env=dict(os.environ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout or ""
    print(output, end="")
    if completed.returncode != 0:
        raise AssertionError(f"SigLIP calibration probe failed with exit code {completed.returncode}")

    production = _match(
        r"baselineCurrent=(\d+)/(\d+)",
        output,
        "production held-out accuracy",
    )
    production_exact = int(production.group(1))
    production_cases = int(production.group(2))

    accuracy = _match(
        r"promptEnsembleCalibrated=(\d+)/(\d+).*?ensembleF1=([0-9.]+)",
        output,
        "held-out ensemble accuracy/F1",
    )
    exact = int(accuracy.group(1))
    cases = int(accuracy.group(2))
    f1 = float(accuracy.group(3))

    ranking = _match(
        r"ensembleSeparable=(\d+)/(\d+).*?ensembleMinMargin=([-0-9.]+)",
        output,
        "held-out ensemble ranking",
    )
    separable = int(ranking.group(1))
    ranking_cases = int(ranking.group(2))
    min_margin = float(ranking.group(3))

    if production_cases <= 0 or cases <= 0 or ranking_cases <= 0:
        raise AssertionError("SigLIP calibration gate received no held-out cases")
    production_exact_rate = production_exact / production_cases
    exact_rate = exact / cases
    separable_rate = separable / ranking_cases

    # The shipped production classifier/threshold must pass independently of
    # whatever threshold the calibration subset happens to fit in this run.
    if production_exact_rate < 0.90:
        raise AssertionError(
            f"Production held-out exact rate too low: {production_exact}/{production_cases} "
            f"({production_exact_rate:.3f})"
        )
    if exact_rate < 0.90:
        raise AssertionError(f"Held-out ensemble exact rate too low: {exact}/{cases} ({exact_rate:.3f})")
    if f1 < 0.95:
        raise AssertionError(f"Held-out ensemble F1 too low: {f1:.4f}")
    if separable_rate < 0.95:
        raise AssertionError(
            f"Held-out ensemble separability too low: {separable}/{ranking_cases} ({separable_rate:.3f})"
        )
    if min_margin < 0.10:
        raise AssertionError(f"Held-out ensemble minimum ranking margin too small: {min_margin:.6f}")

    print(
        "PASS: production and held-out SigLIP quality gates satisfied "
        f"productionExact={production_exact}/{production_cases} "
        f"calibratedExact={exact}/{cases} f1={f1:.4f} "
        f"separable={separable}/{ranking_cases} minMargin={min_margin:.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
