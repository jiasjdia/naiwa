"""Keep bundled resources separate from persistent per-user state."""
import os
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent
if FROZEN:
    override = os.environ.get("NAIWA_DATA_DIR")
    DATA_ROOT = (Path(override).expanduser().resolve() if override else
                 Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "naiwa")
else:
    DATA_ROOT = ROOT
