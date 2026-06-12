"""
analytical_engine.py — Motor analítico de auditoría del gasto.

Consume el agregado por entidad que produce data_pipeline.py
(data/processed/execution_<period>.csv) y calcula las métricas de la tarea:

    Avance (%)             = (Devengado / PIM) * 100
    Presupuesto Paralizado = PIM - Devengado
    Hall of Shame          = entidades con PIM > umbral (10M PEN) y bajo avance

Las funciones son importables por el dashboard (app.py). Como salida persiste
solo tablas pequeñas en data/processed/analytics_<period>/ y un snapshot.

Uso:
    python src/analytical_engine.py --period 2025-06
    python src/analytical_engine.py --period 2025-06 --min-pim 10000000 --top 15
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils  # noqa: E402

log = utils.get_logger("mef_mcp.analytics")

# Umbral de "unidad grande" para el Hall of Shame (PEN).
DEFAULT_MIN_PIM = 10_000_000.0
DIMENSIONS = ["nivel_gobierno", "departamento", "sector"]


def execution_path(period: str) -> Path:
    """Ruta del CSV agregado por entidad para un período (salida de data_pipeline)."""
    return utils.PROCESSED_DIR / f"execution_{period}.csv"


def load_execution(period: str) -> pd.DataFrame:
    """Carga el agregado por entidad y añade las métricas derivadas."""
    path = execution_path(period)
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Genera primero los datos: "
            f"python src/data_pipeline.py --period {period}"
        )
    df = pd.read_csv(path)
    return add_metrics(df)


def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columnas avance_pct y paralizado (sin dividir por cero)."""
    df = df.copy()
    df["pim"] = pd.to_numeric(df["pim"], errors="coerce").fillna(0.0)
    df["devengado"] = pd.to_numeric(df["devengado"], errors="coerce").fillna(0.0)
    df["paralizado"] = df["pim"] - df["devengado"]
    df["avance_pct"] = 0.0
    mask = df["pim"] > 0
    df.loc[mask, "avance_pct"] = (df.loc[mask, "devengado"] / df.loc[mask, "pim"]) * 100
    df["avance_pct"] = df["avance_pct"].round(2)
    return df


def kpis(df: pd.DataFrame) -> dict:
    """KPIs globales del período/alcance."""
    total_pim = float(df["pim"].sum())
    total_dev = float(df["devengado"].sum())
    avance = round((total_dev / total_pim * 100), 2) if total_pim else 0.0
    return {
        "n_entities": int(len(df)),
        "total_pim": total_pim,
        "total_devengado": total_dev,
        "paralizado_total": round(total_pim - total_dev, 2),
        "avance_global_pct": avance,
    }


def by_dimension(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Agrega PIM/Devengado por una dimensión y recalcula avance/paralizado."""
    grouped = (
        df.groupby(col, dropna=False)[["pim", "devengado"]]
        .sum()
        .reset_index()
    )
    grouped["paralizado"] = grouped["pim"] - grouped["devengado"]
    grouped["avance_pct"] = 0.0
    mask = grouped["pim"] > 0
    grouped.loc[mask, "avance_pct"] = (
        grouped.loc[mask, "devengado"] / grouped.loc[mask, "pim"] * 100
    ).round(2)
    return grouped.sort_values("pim", ascending=False).reset_index(drop=True)


def hall_of_shame(
    df: pd.DataFrame, min_pim: float = DEFAULT_MIN_PIM, top: int = 15
) -> pd.DataFrame:
    """
    Entidades 'vergonzosas': PIM por encima del umbral (entidades grandes) y
    peor avance. Se ordena por avance ascendente (las que menos ejecutaron) y,
    a igualdad, por mayor presupuesto paralizado.
    """
    big = df[df["pim"] > min_pim].copy()
    ranked = big.sort_values(["avance_pct", "paralizado"], ascending=[True, False])
    cols = [
        "ejecutora", "pliego", "departamento", "nivel_gobierno",
        "pim", "devengado", "paralizado", "avance_pct",
    ]
    cols = [c for c in cols if c in ranked.columns]
    return ranked[cols].head(top).reset_index(drop=True)


def analyze(period: str, min_pim: float = DEFAULT_MIN_PIM, top: int = 15) -> dict:
    """Calcula todo y persiste tablas pequeñas + snapshot. Devuelve metadatos."""
    df = load_execution(period)
    summary = kpis(df)

    out_dir = utils.PROCESSED_DIR / f"analytics_{period}"
    out_dir.mkdir(parents=True, exist_ok=True)

    for dim in DIMENSIONS:
        if dim in df.columns:
            by_dimension(df, dim).to_csv(out_dir / f"by_{dim}.csv", index=False, encoding="utf-8")

    shame = hall_of_shame(df, min_pim=min_pim, top=top)
    shame.to_csv(out_dir / "hall_of_shame.csv", index=False, encoding="utf-8")
    (out_dir / "kpis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    meta = {
        "period": period,
        "min_pim": min_pim,
        "top": top,
        "kpis": summary,
        "hall_of_shame_count": int(len(shame)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(out_dir),
    }
    (out_dir / "analytics_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    utils.save_snapshot(
        f"analytics_{period}_sample",
        {"meta": meta, "hall_of_shame_top": shame.head(utils.MAX_SNAPSHOT_ROWS).to_dict("records")},
    )

    log.info(
        "Analytics %s: %d entidades | avance global %.2f%% | Hall of Shame: %d",
        period, summary["n_entities"], summary["avance_global_pct"], len(shame),
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Motor analítico (métricas + Hall of Shame).")
    parser.add_argument("--period", required=True, help="Período, p.ej. 2025 o 2025-06.")
    parser.add_argument("--min-pim", type=float, default=DEFAULT_MIN_PIM, help="Umbral PIM del Hall of Shame.")
    parser.add_argument("--top", type=int, default=15, help="Nº de entidades en el Hall of Shame.")
    args = parser.parse_args()

    meta = analyze(args.period, args.min_pim, args.top)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
