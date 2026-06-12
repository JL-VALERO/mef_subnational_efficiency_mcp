"""
utils.py — Helpers comunes (rutas, configuración, logging, I/O ligero).

NOTA: scaffold inicial — se completa conforme lo necesiten las demás etapas.
"""

from pathlib import Path

# Rutas base del proyecto (evita rutas hardcodeadas dispersas).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_PDFS_DIR = DATA_DIR / "raw_pdfs"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
PROCESSED_DIR = DATA_DIR / "processed"
