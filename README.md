# mef_subnational_efficiency_mcp

Auditoría del gasto público peruano mediante sistemas multi-agente, Claude Code
Skills y un servidor **MCP local**. Procesa datos fiscales del MEF 2025 desde
`datosabiertos.gob.pe` y digitaliza vía OCR un archivo histórico de 1964,
exponiendo los resultados en un dashboard Streamlit de 4 pestañas.

> Tarea HW_05 — Issue d2cml-ai/Data-Science-Python#178

---

## Métricas clave

| Métrica | Fórmula |
|---|---|
| **Avance (%)** | `(Devengado / PIM) × 100` |
| **Presupuesto Paralizado** | `PIM − Devengado` |

- **PIM**: presupuesto asignado (lo que se podía gastar).
- **Devengado**: lo que realmente se gastó.

---

## Reglas de diseño

1. **Anti-context-flooding:** prohibido cargar CSV/JSON completos en el contexto
   del LLM. Python filtra los archivos grandes localmente y guarda solo snapshots
   de 5-10 filas (`data/snapshots/`) y agregados pequeños (`data/processed/`).
2. **Updates por CLI:** sin fechas hardcodeadas. El período se controla por
   argumento, p. ej. `claude "run executor_skill for period 2025-12"`.

---

## Estructura del proyecto

```
mef_subnational_efficiency_mcp/
├── app.py                     # Dashboard Streamlit (4 pestañas)
├── README.md
├── requirements.txt
├── .claude/skills/
│   ├── executor_skill.json    # Agente Executor (ingesta, OCR, draft UI)
│   └── evaluator_skill.json   # Agente Evaluator (QA, UI/UX, reporte)
├── src/
│   ├── mcp_server.py          # Servidor MCP local
│   ├── data_pipeline.py       # Ingesta MEF 2025 (anti-flooding)
│   ├── ocr_engine.py          # OCR PaddleOCR del archivo 1964
│   ├── analytical_engine.py   # Métricas y Hall of Shame
│   └── utils.py               # Helpers comunes
├── data/
│   ├── raw_pdfs/              # PDF fuente 1964 (no versionado)
│   ├── snapshots/            # Muestras de 5-10 filas
│   └── processed/            # Resultados agregados pequeños
└── video/link.txt            # Link del video de presentación
```

---

## Instalación

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Uso

```bash
streamlit run app.py
```

> Documentación detallada de cada módulo se completará conforme avancen las etapas
> del proyecto.
