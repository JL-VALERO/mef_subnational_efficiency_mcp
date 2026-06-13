"""
run_skill.py — Orquestador de los skills duales (Executor / Evaluator).

Mapea el comando dinámico de Claude Code, p. ej.:

    claude "run executor_skill for period 2025-12"

a una ejecución concreta y reproducible por período (sin fechas hardcodeadas):

    python src/run_skill.py executor_skill --period 2025-12
    python src/run_skill.py evaluator_skill --period 2025-06

- Executor: lee .claude/skills/executor_skill.json y ejecuta sus pasos
  (ingesta + análisis; OCR/dashboard quedan como pasos opcionales).
- Evaluator: lee .claude/skills/evaluator_skill.json, audita la consistencia de
  los resultados del período y genera un reporte de QA.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils  # noqa: E402

import analytical_engine  # noqa: E402

log = utils.get_logger("mef_mcp.skills")

SKILLS_DIR = utils.PROJECT_ROOT / ".claude" / "skills"
PERIOD_RE = re.compile(r"^\d{4}(-\d{2})?$")


def load_skill(name: str) -> dict:
    """Carga un skill JSON por nombre (con o sin sufijo .json)."""
    name = name if name.endswith(".json") else f"{name}.json"
    path = SKILLS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"No existe el skill {path}.")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_period(period: str) -> None:
    if not PERIOD_RE.match(period):
        raise ValueError(f"Período inválido '{period}'. Use YYYY o YYYY-MM (p.ej. 2025-12).")


def run_executor(skill: dict, period: str, max_rows: int | None, with_ocr: bool, dry_run: bool) -> dict:
    """Ejecuta los pasos auto-ejecutables del Executor para el período dado."""
    results = []
    for step in skill.get("steps", []):
        auto = step.get("auto_run", False) or (step.get("id") == "ocr_1964" and with_ocr)
        cmd = step["command"].format(python=sys.executable, period=period)
        if step.get("accepts_max_rows") and max_rows:
            cmd += f" --max-rows {max_rows}"

        if not auto:
            log.info("[skip] %s (auto_run=false): %s", step["id"], cmd)
            results.append({"step": step["id"], "status": "skipped", "command": cmd})
            continue

        log.info("[run ] %s: %s", step["id"], cmd)
        if dry_run:
            results.append({"step": step["id"], "status": "dry-run", "command": cmd})
            continue

        proc = subprocess.run(cmd, shell=True)
        status = "ok" if proc.returncode == 0 else "error"
        results.append({"step": step["id"], "status": status, "command": cmd, "returncode": proc.returncode})
        if proc.returncode != 0:
            log.error("Paso %s falló (returncode=%s). Se detiene el Executor.", step["id"], proc.returncode)
            break

    meta = {
        "skill": "executor_skill",
        "period": period,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    return meta


def run_evaluator(skill: dict, period: str) -> dict:
    """Audita la consistencia de los resultados del período y escribe el QA report."""
    df = analytical_engine.load_execution(period)  # incluye avance_pct y paralizado
    min_pim = analytical_engine.DEFAULT_MIN_PIM

    def n_viol(mask) -> int:
        return int(mask.sum())

    checks = [
        {"id": "pim_no_negativo", "violations": n_viol(df["pim"] < 0)},
        {"id": "devengado_no_negativo", "violations": n_viol(df["devengado"] < 0)},
        {"id": "devengado_no_excede_pim", "violations": n_viol(df["devengado"] > df["pim"] * 1.001)},
        {"id": "avance_en_rango", "violations": n_viol((df["avance_pct"] < 0) | (df["avance_pct"] > 100))},
        {"id": "paralizado_consistente", "violations": n_viol((df["paralizado"] - (df["pim"] - df["devengado"])).abs() >= 1)},
    ]
    shame = analytical_engine.hall_of_shame(df, min_pim=min_pim, top=10_000)
    checks.append({"id": "hall_of_shame_umbral", "violations": n_viol(shame["pim"] <= min_pim)})

    for c in checks:
        c["passed"] = c["violations"] == 0

    report = {
        "skill": "evaluator_skill",
        "period": period,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "entities_audited": int(len(df)),
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
        "ui_ux_recommendations": skill.get("ui_ux_recommendations", []),
        "performance_recommendations": skill.get("performance_recommendations", []),
    }

    utils.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = utils.PROCESSED_DIR / f"qa_report_{period}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    utils.save_snapshot(f"qa_report_{period}", report)
    log.info(
        "Evaluator %s: %d entidades, checks OK=%s -> %s",
        period, report["entities_audited"], report["all_passed"], out,
    )
    report["report_path"] = str(out)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Ejecuta un skill (executor/evaluator) por período.")
    parser.add_argument("skill", help="Nombre del skill: executor_skill | evaluator_skill")
    parser.add_argument("--period", required=True, help="Período YYYY o YYYY-MM (p.ej. 2025-12).")
    parser.add_argument("--max-rows", type=int, default=None, help="Tope de filas para la ingesta (modo muestra).")
    parser.add_argument("--with-ocr", action="store_true", help="Incluir el paso OCR del Executor.")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar los comandos sin ejecutarlos.")
    args = parser.parse_args()

    _validate_period(args.period)
    skill = load_skill(args.skill)
    role = skill.get("role")

    if role == "executor":
        out = run_executor(skill, args.period, args.max_rows, args.with_ocr, args.dry_run)
    elif role == "evaluator":
        out = run_evaluator(skill, args.period)
    else:
        raise ValueError(f"Rol de skill no soportado: {role!r}")

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
