from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GATEWAY = ROOT / "marcap-price-gateway"
for path in (ROOT, GATEWAY):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
