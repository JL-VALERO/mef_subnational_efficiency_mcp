"""
app.py — Dashboard Streamlit (4 pestañas) del auditor de gasto público.

Pestañas:
    1. KPIs 2025 + análisis histórico 1964 (2+ gráficos)
    2. Distribución territorial 2025 (mapas / heatmaps)
    3. Hall of Shame 2025 (unidades > 10M PEN con bajo avance)
    4. Reporte de auditoría multi-agente + playground interactivo

NOTA: scaffold inicial — la lógica se implementa en la Etapa 6.
"""

import streamlit as st


def main() -> None:
    st.set_page_config(
        page_title="MEF Subnational Efficiency",
        layout="wide",
    )
    st.title("Auditoría del Gasto Público — MEF")

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "KPIs 2025 + 1964",
            "Distribución territorial",
            "Hall of Shame",
            "Auditoría multi-agente",
        ]
    )

    with tab1:
        st.info("TODO (Etapa 6): KPIs 2025 y análisis histórico 1964.")
    with tab2:
        st.info("TODO (Etapa 6): mapas / heatmaps de distribución territorial 2025.")
    with tab3:
        st.info("TODO (Etapa 6): unidades > 10M PEN con bajo avance.")
    with tab4:
        st.info("TODO (Etapa 6): reporte de auditoría y playground.")


if __name__ == "__main__":
    main()
