"""Locate editable resources beside the portable executable or source files."""
from pathlib import Path
import sys

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent)
