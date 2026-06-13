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
PERIOD_RE = re.compile(r"^\d{4}(-(\d{2}|Q[1-4]))?$", re.IGNORECASE)


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


def _cross_verify_via_mcp(period: str) -> dict:
    """
    Re-muestrea independientemente el CSV de origen usando la tool MCP preview_csv
    y verifica que las columnas clave existen (detección de 'extraction drift').
    """
    try:
        import data_pipeline as dp
        import mcp_server

        year, _ = dp.parse_period(period)
        url = dp.resolve_resource_url(year)
        sample = mcp_server.preview_csv(url, rows=3)
        header = sample.get("header", [])
        missing = [c for c in ["MONTO_PIM"] if c not in header]
        if not any(h.startswith("MONTO_DEVENGADO_") for h in header):
            missing.append("MONTO_DEVENGADO_*")
        return {"source_url": url, "columns_checked": len(header), "missing": missing, "drift": bool(missing)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200], "drift": None}


def _apply_optimizations() -> list[dict]:
    """
    Aplica/asegura optimizaciones reales sobre el código del dashboard
    (no solo recomendaciones): crea .streamlit/config.toml y verifica
    estáticamente cache y guarda de división por cero en app.py.
    """
    actions: list[dict] = []

    cfg_dir = utils.PROJECT_ROOT / ".streamlit"
    cfg_dir.mkdir(exist_ok=True)
    cfg = cfg_dir / "config.toml"
    desired = (
        "[server]\nheadless = true\n\n"
        "[runner]\nfastReruns = true\n\n"
        '[theme]\nbase = "dark"\nprimaryColor = "#e74c3c"\n'
        'backgroundColor = "#0e1117"\nsecondaryBackgroundColor = "#1c2333"\n'
        'textColor = "#fafafa"\n'
    )
    if not cfg.exists() or cfg.read_text(encoding="utf-8") != desired:
        cfg.write_text(desired, encoding="utf-8")
        actions.append({"change": "config", "detail": "Generado/actualizado .streamlit/config.toml (performance + tema)."})
    else:
        actions.append({"change": "config", "detail": ".streamlit/config.toml ya óptimo (sin cambios)."})

    app_text = (utils.PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    actions.append({
        "change": "cache_check",
        "detail": "@st.cache_data presente en app.py." if "@st.cache_data" in app_text
        else "FALTA @st.cache_data en app.py (rendimiento degradado).",
        "ok": "@st.cache_data" in app_text,
    })

    # La guarda de división por cero del avance vive en el motor analítico.
    ae_text = (utils.PROJECT_ROOT / "src" / "analytical_engine.py").read_text(encoding="utf-8")
    has_guard = '["pim"] > 0' in ae_text
    actions.append({
        "change": "div_zero_check",
        "detail": "Guarda de división por cero presente en analytical_engine (pim > 0)." if has_guard
        else "Revisar manejo de división por cero en cálculos de avance.",
        "ok": has_guard,
    })
    return actions


def _write_markdown_report(report: dict, crosscheck: dict, actions: list[dict]) -> Path:
    """Escribe el reporte de QA en markdown (mostrado en el Tab 4 del dashboard)."""
    period = report["period"]
    lines = [
        f"# Reporte de Auditoría — Evaluator ({period})",
        "",
        f"_Generado: {report['evaluated_at']} · Entidades auditadas: {report['entities_audited']}_",
        "",
        f"**Veredicto global:** {'✅ Todos los checks pasaron' if report['all_passed'] else '❌ Hay violaciones'}",
        "",
        "## 1. Checks de consistencia de datos",
        "",
        "| Check | Violaciones | Estado |",
        "|---|---|---|",
    ]
    for c in report["checks"]:
        lines.append(f"| {c['id']} | {c['violations']} | {'✅' if c['passed'] else '❌'} |")
    lines += [
        "",
        "## 2. Cross-verificación independiente (vía MCP)",
        "",
        f"- Fuente re-muestreada: `{crosscheck.get('source_url', 'n/d')}`",
        f"- Columnas verificadas: {crosscheck.get('columns_checked', 'n/d')}",
        f"- Extraction drift: {'❌ ' + ', '.join(crosscheck['missing']) if crosscheck.get('drift') else '✅ sin drift'}",
        "",
        "## 3. Optimizaciones de performance/UX aplicadas",
        "",
    ]
    for a in actions:
        lines.append(f"- **{a['change']}**: {a['detail']}")
    lines += [
        "",
        "## 4. Recomendaciones (Executor draft → Evaluator polish)",
        "",
        "**UI/UX**",
    ]
    lines += [f"- {r}" for r in report.get("ui_ux_recommendations", [])]
    lines += ["", "**Performance**"]
    lines += [f"- {r}" for r in report.get("performance_recommendations", [])]
    lines.append("")

    path = utils.PROCESSED_DIR / f"qa_report_{period}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


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

    crosscheck = _cross_verify_via_mcp(period)
    actions = _apply_optimizations()

    report = {
        "skill": "evaluator_skill",
        "period": period,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "entities_audited": int(len(df)),
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
        "cross_verification": crosscheck,
        "optimizations_applied": actions,
        "ui_ux_recommendations": skill.get("ui_ux_recommendations", []),
        "performance_recommendations": skill.get("performance_recommendations", []),
    }

    utils.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = utils.PROCESSED_DIR / f"qa_report_{period}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = _write_markdown_report(report, crosscheck, actions)
    utils.save_snapshot(f"qa_report_{period}", report)
    log.info(
        "Evaluator %s: %d entidades, checks OK=%s, drift=%s -> %s",
        period, report["entities_audited"], report["all_passed"], crosscheck.get("drift"), md_path,
    )
    report["report_path"] = str(out)
    report["report_markdown"] = str(md_path)
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
