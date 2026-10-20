"""CI smoke check: import every module in the `naboo` package.

Modules listed in SKIP need packages that are not installed from uv.lock
(camera / ML stack that only exists on the robot or training box). They are
skipped, not stubbed. Every other module must import cleanly, so a dependency
bump that breaks an import fails CI.
"""

import importlib
import pkgutil
import sys

import naboo

SKIP = {
    "naboo.cnn_drive": "needs numpy/torch (on-robot ML stack, not in pyproject)",
    "naboo.export_tinynav": "needs numpy/torch (training stack, not in pyproject)",
    "naboo.tinynav_numpy": "needs numpy (on-robot ML stack, not in pyproject)",
    "naboo.train_tinynav": "needs numpy/torch (training stack, not in pyproject)",
    "naboo.collect_service": "needs cv2 (camera stack, not in pyproject)",
    "naboo.detect_service": "needs cv2/ultralytics (camera stack, not in pyproject)",
    "naboo.record_video": "needs cv2 (camera stack, not in pyproject)",
    "naboo.web_controller": "needs fastapi, which pyproject declares but uv.lock is missing",
}

failed = []
modules = ["naboo"] + [
    m.name for m in pkgutil.walk_packages(naboo.__path__, prefix="naboo.")
]
for name in sorted(modules):
    if name in SKIP:
        print(f"SKIP {name}: {SKIP[name]}")
        continue
    try:
        importlib.import_module(name)
        print(f"OK   {name}")
    except Exception as exc:  # noqa: BLE001 - report every failure
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        failed.append(name)

stale = set(SKIP) - set(modules)
if stale:
    print(f"FAIL SKIP entries for modules that no longer exist: {sorted(stale)}")
    failed.extend(sorted(stale))

sys.exit(1 if failed else 0)
