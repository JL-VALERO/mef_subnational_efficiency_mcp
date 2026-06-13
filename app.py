"""
app.py — Dashboard Streamlit (4 pestañas) del auditor de gasto público.

Pestañas:
    1. KPIs 2025 + análisis histórico 1964 (independiente, 2+ gráficos).
    2. Distribución territorial 2025 (heatmap, treemap, ranking por departamento).
    3. Hall of Shame 2025 (entidades > 10M PEN con bajo avance).
    4. Reporte de auditoría multi-agente (QA) + playground interactivo.

Reutiliza las funciones del motor analítico y lee solo los agregados pequeños
de data/processed/ (regla anti-flooding). Cachea las cargas (st.cache_data).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import analytical_engine as ae  # noqa: E402
import utils  # noqa: E402

st.set_page_config(page_title="MEF Subnational Efficiency", layout="wide", page_icon="📊")


# --------------------------------------------------------------------------- #
# Carga de datos (cacheada)
# --------------------------------------------------------------------------- #
def available_periods() -> list[str]:
    """Períodos con datos procesados (data/processed/execution_<period>.csv)."""
    periods = []
    for p in utils.PROCESSED_DIR.glob("execution_*.csv"):
        periods.append(p.stem.replace("execution_", ""))
    return sorted(periods, reverse=True)


@st.cache_data(show_spinner=False)
def load_period(period: str) -> pd.DataFrame:
    return ae.load_execution(period)


@st.cache_data(show_spinner=False)
def load_qa(period: str) -> dict | None:
    path = utils.PROCESSED_DIR / f"qa_report_{period}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@st.cache_data(show_spinner=False)
def load_ocr_1964() -> dict | None:
    path = utils.PROCESSED_DIR / "ocr_1964" / "ocr_1964.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def fmt_m(x: float) -> str:
    """Formatea un monto en millones de PEN."""
    return f"S/ {x / 1e6:,.1f} M"


# Patrón de montos al estilo peruano: 2'833,333.34 / 177,357.15 / 166.66
_AMOUNT_RE = re.compile(r"^\d{1,3}([',]\d{3})*(\.\d+)?$")


def parse_amount(token: str) -> float | None:
    if not _AMOUNT_RE.match(token):
        return None
    try:
        return float(token.replace("'", "").replace(",", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
st.sidebar.title("📊 Auditoría MEF")
st.sidebar.caption("Eficiencia del gasto subnacional")

periods = available_periods()
if not periods:
    st.title("Auditoría del Gasto Público — MEF")
    st.warning(
        "No hay datos procesados todavía. Genera un período con el Executor:\n\n"
        "```\npython src/run_skill.py executor_skill --period 2025-06 --max-rows 150000\n```"
    )
    st.stop()

period = st.sidebar.selectbox("Período", periods)
df = load_period(period)
summary = ae.kpis(df)
st.sidebar.metric("Entidades", summary["n_entities"])
st.sidebar.metric("Avance global", f"{summary['avance_global_pct']:.1f}%")
st.sidebar.caption(f"CLI dinámico:\n\n`run executor_skill for period {period}`")


st.title("Auditoría del Gasto Público — MEF")
tab1, tab2, tab3, tab4 = st.tabs(
    ["① KPIs 2025 + 1964", "② Distribución territorial", "③ Hall of Shame", "④ Auditoría multi-agente"]
)

# --------------------------------------------------------------------------- #
# Pestaña 1 — KPIs 2025 + análisis histórico 1964
# --------------------------------------------------------------------------- #
with tab1:
    st.subheader(f"KPIs de ejecución — {period}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("PIM total", fmt_m(summary["total_pim"]))
    c2.metric("Devengado", fmt_m(summary["total_devengado"]))
    c3.metric("Avance global", f"{summary['avance_global_pct']:.1f}%")
    c4.metric("Paralizado", fmt_m(summary["paralizado_total"]))

    nivel = ae.by_dimension(df, "nivel_gobierno")
    fig = px.bar(
        nivel, x="nivel_gobierno", y="avance_pct", color="avance_pct",
        color_continuous_scale="RdYlGn", range_color=[0, 100],
        title="Avance % por nivel de gobierno", text_auto=".1f",
    )
    st.plotly_chart(fig, width="stretch")

    st.divider()
    st.subheader("🏛️ Análisis histórico independiente — Cuenta General 1964")
    ocr = load_ocr_1964()
    if not ocr:
        st.info(
            "Aún no hay datos OCR de 1964. Ejecuta:\n\n"
            "```\npython src/ocr_engine.py --start 60 --count 15\n```"
        )
    else:
        pages = pd.DataFrame([{"page": p["page"], "n_lines": p["n_lines"]} for p in ocr])
        total_lines = int(pages["n_lines"].sum())
        m1, m2 = st.columns(2)
        m1.metric("Páginas OCR procesadas", len(pages))
        m2.metric("Líneas extraídas", f"{total_lines:,}")

        g1, g2 = st.columns(2)
        with g1:
            fig_lines = px.bar(
                pages, x="page", y="n_lines",
                title="Líneas reconocidas por página (1964)", labels={"page": "Página", "n_lines": "Líneas"},
            )
            st.plotly_chart(fig_lines, width="stretch")

        # Extrae montos del texto OCR para una distribución histórica.
        amounts = []
        for p in ocr:
            for line in p["lines"]:
                val = parse_amount(line["text"])
                if val is not None and val >= 1000:  # foco en cifras presupuestarias
                    amounts.append(val)
        with g2:
            if amounts:
                amt_df = pd.DataFrame({"monto": amounts})
                fig_amt = px.histogram(
                    amt_df, x="monto", nbins=40, log_y=True,
                    title="Distribución de montos detectados (1964)", labels={"monto": "Monto (soles de 1964)"},
                )
                st.plotly_chart(fig_amt, width="stretch")
            else:
                st.info("No se detectaron montos numéricos en las páginas OCR.")

        if amounts:
            top_amt = pd.DataFrame({"monto": sorted(amounts, reverse=True)[:10]})
            st.caption("Top 10 mayores montos detectados en el documento de 1964 (soles de la época):")
            st.dataframe(top_amt.style.format({"monto": "{:,.2f}"}), width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
# Pestaña 2 — Distribución territorial 2025
# --------------------------------------------------------------------------- #
with tab2:
    st.subheader(f"Distribución territorial — {period}")

    dep = ae.by_dimension(df, "departamento").head(20)
    fig_dep = px.bar(
        dep.sort_values("paralizado"), x="paralizado", y="departamento", orientation="h",
        color="avance_pct", color_continuous_scale="RdYlGn", range_color=[0, 100],
        title="Presupuesto paralizado por departamento (color = avance %)",
        labels={"paralizado": "Paralizado (PEN)", "departamento": ""},
    )
    fig_dep.update_layout(height=600)
    st.plotly_chart(fig_dep, width="stretch")

    col_a, col_b = st.columns(2)
    with col_a:
        # Heatmap departamento x nivel de gobierno (avance %).
        pivot = df.pivot_table(
            index="departamento", columns="nivel_gobierno", values="avance_pct", aggfunc="mean"
        )
        fig_hm = px.imshow(
            pivot, color_continuous_scale="RdYlGn", zmin=0, zmax=100, aspect="auto",
            title="Mapa de calor: avance % (departamento × nivel)",
        )
        fig_hm.update_layout(height=600)
        st.plotly_chart(fig_hm, width="stretch")
    with col_b:
        # Treemap jerárquico (tamaño = PIM, color = avance).
        fig_tm = px.treemap(
            df, path=[px.Constant("Perú"), "nivel_gobierno", "departamento"],
            values="pim", color="avance_pct", color_continuous_scale="RdYlGn", range_color=[0, 100],
            title="Mapa del presupuesto (tamaño = PIM, color = avance %)",
        )
        fig_tm.update_layout(height=600)
        st.plotly_chart(fig_tm, width="stretch")

# --------------------------------------------------------------------------- #
# Pestaña 3 — Hall of Shame
# --------------------------------------------------------------------------- #
with tab3:
    st.subheader(f"🚨 Hall of Shame — {period}")
    min_pim = st.slider(
        "Umbral de PIM (entidades grandes)", 1_000_000, 100_000_000, int(ae.DEFAULT_MIN_PIM), 1_000_000,
        format="S/ %d",
    )
    top = st.slider("Cantidad a mostrar", 5, 30, 15)
    shame = ae.hall_of_shame(df, min_pim=float(min_pim), top=top)

    if shame.empty:
        st.info("No hay entidades por encima del umbral seleccionado.")
    else:
        st.caption(f"Entidades con PIM > {fmt_m(min_pim)} y peor avance de ejecución.")
        fig_shame = px.bar(
            shame.sort_values("avance_pct", ascending=False),
            x="avance_pct", y="ejecutora", orientation="h",
            color="avance_pct", color_continuous_scale="RdYlGn", range_color=[0, 100],
            title="Peores avances de ejecución (entidades grandes)",
            labels={"avance_pct": "Avance %", "ejecutora": ""},
        )
        fig_shame.update_layout(height=500)
        st.plotly_chart(fig_shame, width="stretch")

        show = shame.copy()
        for col in ["pim", "devengado", "paralizado"]:
            show[col] = show[col].map(lambda v: f"{v:,.0f}")
        show["avance_pct"] = show["avance_pct"].map(lambda v: f"{v:.1f}%")
        st.dataframe(show, width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
# Pestaña 4 — Auditoría multi-agente + playground
# --------------------------------------------------------------------------- #
with tab4:
    st.subheader("🤖 Reporte de auditoría multi-agente (Evaluator)")
    qa = load_qa(period)
    if not qa:
        st.info(
            "Aún no hay reporte de QA para este período. Genera con el Evaluator:\n\n"
            f"```\npython src/run_skill.py evaluator_skill --period {period}\n```"
        )
    else:
        if qa["all_passed"]:
            st.success(f"✅ Todos los checks de consistencia pasaron ({qa['entities_audited']} entidades auditadas).")
        else:
            st.error("❌ Hay checks de consistencia con violaciones.")
        checks = pd.DataFrame(qa["checks"])
        checks["estado"] = checks["passed"].map({True: "✅ OK", False: "❌ Falla"})
        st.dataframe(
            checks[["id", "violations", "estado"]], width="stretch", hide_index=True
        )
        with st.expander("Recomendaciones del Evaluator (UI/UX y performance)"):
            st.markdown("**UI/UX**")
            for r in qa.get("ui_ux_recommendations", []):
                st.markdown(f"- {r}")
            st.markdown("**Performance**")
            for r in qa.get("performance_recommendations", []):
                st.markdown(f"- {r}")

    st.divider()
    st.subheader("🎛️ Playground interactivo")
    st.caption("Filtra entidades dinámicamente y observa el presupuesto en riesgo.")
    cc1, cc2 = st.columns(2)
    pim_min = cc1.number_input("PIM mínimo (PEN)", 0, 500_000_000, 10_000_000, 1_000_000)
    av_max = cc2.slider("Avance máximo (%)", 0, 100, 40)
    flt = df[(df["pim"] >= pim_min) & (df["avance_pct"] <= av_max)]
    k1, k2 = st.columns(2)
    k1.metric("Entidades que cumplen el filtro", len(flt))
    k2.metric("Presupuesto en riesgo (paralizado)", fmt_m(float(flt["paralizado"].sum())))
    st.dataframe(
        flt[["ejecutora", "departamento", "pim", "devengado", "paralizado", "avance_pct"]]
        .sort_values("paralizado", ascending=False)
        .head(50),
        width="stretch", hide_index=True,
    )
