"""
utils.py — Helpers comunes (rutas, configuración, logging, acceso al portal, I/O ligero).

Centraliza:
    - Rutas base del proyecto (evita rutas hardcodeadas dispersas).
    - Configuración del portal datosabiertos.gob.pe (CKAN), override por env var.
    - Logging que escribe a stderr (importante: los servidores MCP usan stdout
      para el protocolo, así que los logs NUNCA deben ir a stdout).
    - Acceso HTTP JSON al portal.
    - Guardado de snapshots con tope de filas (regla anti-context-flooding).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any

import requests

# Cabecera estándar para todas las peticiones al portal.
DEFAULT_HEADERS = {"User-Agent": "mef-subnational-efficiency-mcp/0.1"}

# --------------------------------------------------------------------------- #
# Rutas base del proyecto
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_PDFS_DIR = DATA_DIR / "raw_pdfs"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
PROCESSED_DIR = DATA_DIR / "processed"

# --------------------------------------------------------------------------- #
# Configuración del portal de datos abiertos (CKAN)
# --------------------------------------------------------------------------- #
# Override posible con la variable de entorno MEF_PORTAL_BASE_URL.
PORTAL_BASE_URL = os.environ.get(
    "MEF_PORTAL_BASE_URL", "https://www.datosabiertos.gob.pe"
)
CKAN_API = f"{PORTAL_BASE_URL}/api/3/action"

# Tope duro de filas que devolvemos al LLM como snapshot (regla anti-flooding).
MAX_SNAPSHOT_ROWS = 10

# --------------------------------------------------------------------------- #
# Logging (a stderr)
# --------------------------------------------------------------------------- #
def get_logger(name: str = "mef_mcp") -> logging.Logger:
    """Devuelve un logger que escribe a stderr (no interfiere con el stdio MCP)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# --------------------------------------------------------------------------- #
# Acceso HTTP al portal
# --------------------------------------------------------------------------- #
def http_get_json(url: str, params: dict | None = None, timeout: int = 45) -> dict:
    """GET a una URL que responde JSON. Lanza excepción en error HTTP."""
    resp = requests.get(url, params=params, timeout=timeout, headers=DEFAULT_HEADERS)
    resp.raise_for_status()
    return resp.json()


def ckan_action(action: str, params: dict | None = None, timeout: int = 45) -> Any:
    """
    Llama a una acción de la API estilo CKAN del portal DKAN
    (p. ej. 'package_list', 'package_show', 'datastore_search').

    Devuelve el contenido de la clave 'result' de la respuesta.
    """
    data = http_get_json(f"{CKAN_API}/{action}", params=params, timeout=timeout)
    if not data.get("success", False):
        raise RuntimeError(f"Acción '{action}' falló: {data.get('error')}")
    return data.get("result")


def package_show(dataset_id: str) -> dict:
    """
    package_show del portal DKAN, desempaquetando el resultado.

    DKAN devuelve 'result' como una lista de un elemento; aquí se normaliza a
    un único dict con los metadatos del dataset (incluye 'resources').
    """
    result = ckan_action("package_show", {"id": dataset_id})
    if isinstance(result, list):
        return result[0] if result else {}
    return result or {}


def _sniff_delimiter(sample: str) -> str:
    """Heurística simple: en Perú muchos CSV usan ';'. Elige el más frecuente."""
    first_line = sample.splitlines()[0] if sample else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def stream_csv_head(url: str, n_rows: int) -> dict:
    """
    Descarga solo las primeras filas de un CSV remoto, sin traer el archivo
    completo (regla anti-context-flooding). `n_rows` se acota al tope.

    Devuelve {'header': [...], 'sample_rows': [[...], ...]}.
    """
    n = cap_rows(n_rows)
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=60, stream=True)
    resp.raise_for_status()

    buf = b""
    for chunk in resp.iter_content(chunk_size=4096):
        if not chunk:
            continue
        buf += chunk
        if buf.count(b"\n") >= n + 1:  # cabecera + n filas
            break
    resp.close()

    text = buf.decode("utf-8", errors="replace")
    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for i, row in enumerate(reader):
        rows.append(row)
        if i >= n:  # cabecera (i=0) + n filas de datos
            break

    header = rows[0] if rows else []
    sample = rows[1:] if len(rows) > 1 else []
    return {"delimiter": delimiter, "header": header, "sample_rows": sample}


# --------------------------------------------------------------------------- #
# I/O ligero (snapshots)
# --------------------------------------------------------------------------- #
def fold(text: str) -> str:
    """Normaliza texto para comparar sin acentos ni mayúsculas (ej.: 'Ejecución' -> 'ejecucion')."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def cap_rows(rows: int) -> int:
    """Acota el número de filas pedidas al tope anti-flooding (1..MAX_SNAPSHOT_ROWS)."""
    return max(1, min(int(rows), MAX_SNAPSHOT_ROWS))


def save_snapshot(name: str, data: Any) -> Path:
    """
    Guarda un snapshot JSON pequeño en data/snapshots/.

    Pensado para muestras de 5-10 filas o metadatos reducidos, NO para datasets
    completos (regla anti-context-flooding).
    """
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS_DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
