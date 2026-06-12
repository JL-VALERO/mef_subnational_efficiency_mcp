"""
data_pipeline.py — Ingesta de datos fiscales del MEF (Presupuesto y Ejecución
de Gasto – Devengado Mensual) desde datosabiertos.gob.pe.

Regla anti-context-flooding: los CSV anuales del MEF son enormes (el de 2025
pesa ~2.8 GB). NUNCA se descargan ni se cargan completos. En su lugar:
    - Se transmiten (streaming) y se procesan por chunks con pandas.
    - Solo se leen las ~20 columnas necesarias (no las 73).
    - Se agrega al vuelo a nivel de entidad ejecutora.
    - Se guardan únicamente resultados pequeños:
        * data/processed/execution_<period>.csv   (agregado por entidad)
        * data/processed/execution_<period>_meta.json (metadatos del run)
        * data/snapshots/execution_<period>_sample.json (5-10 filas)

Período dinámico (regla CLI, sin fechas hardcodeadas):
    python src/data_pipeline.py --period 2025          # devengado anual
    python src/data_pipeline.py --period 2025-06       # devengado acumulado a junio
    python src/data_pipeline.py --period 2025-06 --max-rows 200000   # muestra rápida

La URL del CSV de cada año se resuelve dinámicamente desde el catálogo DKAN
(package_show), no se hardcodea.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils  # noqa: E402

log = utils.get_logger("mef_mcp.pipeline")

# Dataset del MEF en el portal (slug DKAN). Override por env var si cambiara.
DATASET_SLUG = "presupuesto-y-ejecución-de-gasto-–-devengado-mensual"

# Columnas de dimensión que conservamos (de las 73 del archivo original).
DIM_COLS = [
    "ANO_EJE",
    "NIVEL_GOBIERNO",
    "NIVEL_GOBIERNO_NOMBRE",
    "SECTOR_NOMBRE",
    "PLIEGO_NOMBRE",
    "EJECUTORA_NOMBRE",
    "DEPARTAMENTO_EJECUTORA_NOMBRE",
]

# Devengado acumulado por mes: columnas MONTO_DEVENGADO_<MES>.
MONTH_COLS = [
    "MONTO_DEVENGADO_ENERO",
    "MONTO_DEVENGADO_FEBRERO",
    "MONTO_DEVENGADO_MARZO",
    "MONTO_DEVENGADO_ABRIL",
    "MONTO_DEVENGADO_MAYO",
    "MONTO_DEVENGADO_JUNIO",
    "MONTO_DEVENGADO_JULIO",
    "MONTO_DEVENGADO_AGOSTO",
    "MONTO_DEVENGADO_SEPTIEMBRE",
    "MONTO_DEVENGADO_OCTUBRE",
    "MONTO_DEVENGADO_NOVIEMBRE",
    "MONTO_DEVENGADO_DICIEMBRE",
]

# Agrupación de salida: una fila por entidad ejecutora dentro de su contexto.
GROUP_COLS = [
    "NIVEL_GOBIERNO_NOMBRE",
    "DEPARTAMENTO_EJECUTORA_NOMBRE",
    "SECTOR_NOMBRE",
    "PLIEGO_NOMBRE",
    "EJECUTORA_NOMBRE",
]

# Niveles de gobierno por alcance (E=Nacional, R=Regional, M=Local).
SCOPE_LEVELS = {
    "subnational": {"R", "M"},
    "national": {"E"},
    "all": {"E", "R", "M"},
}


def parse_period(period: str) -> tuple[int, int | None]:
    """'2025' -> (2025, None); '2025-06' -> (2025, 6). Valida rangos."""
    parts = period.strip().split("-")
    year = int(parts[0])
    if len(parts) == 1:
        return year, None
    month = int(parts[1])
    if not 1 <= month <= 12:
        raise ValueError(f"Mes inválido en período '{period}' (debe ser 1-12).")
    return year, month


def resolve_resource_url(year: int) -> str:
    """
    Resuelve dinámicamente la URL del CSV del año pedido desde el catálogo DKAN
    (package_show). No hay URLs hardcodeadas: se busca el recurso cuyo nombre
    empieza por el año y corresponde al devengado mensual.
    """
    pkg = utils.package_show(DATASET_SLUG)
    resources = pkg.get("resources", [])
    year_s = str(year)
    candidates = [
        r
        for r in resources
        if (r.get("name") or "").startswith(year_s)
        and "Devengado" in (r.get("name") or "")
        and (r.get("url") or "").lower().endswith(".csv")
    ]
    if not candidates:
        raise RuntimeError(f"No se encontró recurso CSV para el año {year} en el portal.")
    # Preferir el archivo mensual sobre el diario.
    preferred = [r for r in candidates if "Mensual" in (r.get("name") or "")]
    chosen = (preferred or candidates)[0]
    log.info("Recurso %s -> %s", year, chosen.get("name"))
    return chosen["url"]


def _devengado_columns(month: int | None) -> list[str]:
    """Columnas de devengado a leer según el período (acumulado hasta el mes)."""
    if month is None:
        return MONTH_COLS  # año completo
    return MONTH_COLS[:month]


def process(
    period: str,
    scope: str = "subnational",
    max_rows: int | None = None,
    chunksize: int = 100_000,
) -> dict:
    """
    Descarga en streaming, filtra por alcance, agrega por entidad y persiste
    resultados pequeños. Devuelve un dict de metadatos del run.
    """
    if scope not in SCOPE_LEVELS:
        raise ValueError(f"scope inválido: {scope}. Use {list(SCOPE_LEVELS)}.")

    year, month = parse_period(period)
    url = resolve_resource_url(year)
    dev_cols = _devengado_columns(month)
    usecols = DIM_COLS + ["MONTO_PIM"] + dev_cols
    levels = SCOPE_LEVELS[scope]

    log.info(
        "Procesando período=%s scope=%s (niveles=%s) max_rows=%s",
        period, scope, sorted(levels), max_rows,
    )

    resp = requests.get(url, stream=True, headers=utils.DEFAULT_HEADERS, timeout=120)
    resp.raise_for_status()
    resp.raw.decode_content = True

    reader = pd.read_csv(
        resp.raw,
        usecols=usecols,
        dtype=str,
        chunksize=chunksize,
        encoding="latin-1",
    )

    partials: list[pd.DataFrame] = []
    rows_read = 0
    try:
        for chunk in reader:
            rows_read += len(chunk)

            # Filtro de alcance (subnacional por defecto).
            chunk = chunk[chunk["NIVEL_GOBIERNO"].isin(levels)]
            if chunk.empty:
                if max_rows and rows_read >= max_rows:
                    break
                continue

            # Montos a numérico (los vacíos -> 0).
            chunk["pim"] = pd.to_numeric(chunk["MONTO_PIM"], errors="coerce").fillna(0.0)
            dev = pd.to_numeric(chunk[dev_cols[0]], errors="coerce").fillna(0.0)
            for col in dev_cols[1:]:
                dev = dev + pd.to_numeric(chunk[col], errors="coerce").fillna(0.0)
            chunk["devengado"] = dev

            agg = chunk.groupby(GROUP_COLS, dropna=False)[["pim", "devengado"]].sum()
            partials.append(agg)

            if max_rows and rows_read >= max_rows:
                log.info("Alcanzado max_rows=%s (filas leídas=%s)", max_rows, rows_read)
                break
    finally:
        resp.close()

    if not partials:
        raise RuntimeError("No se agregó ninguna fila (¿filtro demasiado estricto?).")

    # Consolidación final (suma de los agregados parciales por entidad).
    result = (
        pd.concat(partials)
        .groupby(level=GROUP_COLS, dropna=False)[["pim", "devengado"]]
        .sum()
        .reset_index()
    )
    result.columns = [
        "nivel_gobierno", "departamento", "sector", "pliego", "ejecutora",
        "pim", "devengado",
    ]
    result["period"] = period
    result["scope"] = scope
    result = result.sort_values("pim", ascending=False).reset_index(drop=True)

    # --- Persistencia (solo resultados pequeños) ---
    utils.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    utils.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    out_csv = utils.PROCESSED_DIR / f"execution_{period}.csv"
    result.to_csv(out_csv, index=False, encoding="utf-8")

    meta = {
        "period": period,
        "scope": scope,
        "source_url": url,
        "rows_read": rows_read,
        "partial_sample": bool(max_rows),
        "entities": int(len(result)),
        "total_pim": float(result["pim"].sum()),
        "total_devengado": float(result["devengado"].sum()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "processed_csv": str(out_csv),
    }
    (utils.PROCESSED_DIR / f"execution_{period}_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    utils.save_snapshot(
        f"execution_{period}_sample",
        {"meta": meta, "sample_rows": result.head(utils.MAX_SNAPSHOT_ROWS).to_dict("records")},
    )

    log.info(
        "OK: %d entidades | PIM=%.0f | Devengado=%.0f -> %s",
        meta["entities"], meta["total_pim"], meta["total_devengado"], out_csv,
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta MEF devengado (anti-flooding).")
    parser.add_argument("--period", required=True, help="Año o año-mes, p.ej. 2025 o 2025-06.")
    parser.add_argument(
        "--scope", default="subnational", choices=list(SCOPE_LEVELS),
        help="Nivel de gobierno a analizar (subnacional por defecto).",
    )
    parser.add_argument(
        "--max-rows", type=int, default=None,
        help="Tope de filas leídas (modo muestra rápida; omitir = período completo).",
    )
    parser.add_argument("--chunksize", type=int, default=100_000, help="Filas por chunk.")
    args = parser.parse_args()

    meta = process(args.period, args.scope, args.max_rows, args.chunksize)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
