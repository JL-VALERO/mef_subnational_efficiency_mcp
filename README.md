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

## Servidor MCP local

El portal `datosabiertos.gob.pe` corre sobre **DKAN** (API estilo CKAN, sin
autenticación). El servidor MCP expone tools seguras que devuelven únicamente
catálogos reducidos y snapshots de pocas filas (regla anti-flooding):

| Tool | Qué hace |
|---|---|
| `health_check` | Verifica que el servidor está vivo. |
| `search_datasets(query, limit)` | Busca datasets por palabra clave (tolerante a acentos). |
| `get_dataset_info(dataset_id)` | Metadatos + recursos (id, formato, URL, datastore). |
| `preview_resource(resource_id, rows)` | Muestra filas de un recurso del datastore (máx 10). |
| `preview_csv(url, rows)` | Muestra las primeras filas de un CSV remoto sin descargarlo entero. |

Ejecutar el servidor (stdio):

```bash
python src/mcp_server.py
```

Registrarlo en Claude Code: ver `.mcp.json` en la raíz (ya configurado).

## Pipeline de datos MEF (anti-flooding)

Fuente: dataset del MEF *Presupuesto y Ejecución de Gasto – Devengado Mensual*
(un CSV por año; el de 2025 pesa ~2.8 GB). El pipeline **nunca** lo descarga
completo: lo transmite por chunks, lee solo las columnas necesarias, filtra por
nivel de gobierno, agrega por entidad ejecutora y guarda solo resultados
pequeños en `data/processed/` y `data/snapshots/`.

Período **dinámico** por CLI (sin fechas hardcodeadas):

```bash
# Devengado anual 2025, gobiernos subnacionales (regional + local)
python src/data_pipeline.py --period 2025

# Devengado acumulado a junio 2025
python src/data_pipeline.py --period 2025-06

# Muestra rápida (tope de filas) para pruebas/demo
python src/data_pipeline.py --period 2025-06 --max-rows 150000
```

Columnas clave del MEF: `MONTO_PIM` (PIM), `MONTO_DEVENGADO_<MES>`/`_ANUAL`
(Devengado), `NIVEL_GOBIERNO` (E/R/M = Nacional/Regional/Local),
`DEPARTAMENTO_EJECUTORA_NOMBRE`, `PLIEGO_NOMBRE`, `EJECUTORA_NOMBRE`.

## OCR del archivo histórico 1964

Documento: **"Cuenta General de la República" (1964)**, Contraloría General del
Perú (Fuentes Históricas del Perú / Google Books, id `9YkbAQAAMAAJ`).

1. Descarga el PDF (botón *Descargar PDF* en
   https://books.google.com.pe/books?id=9YkbAQAAMAAJ ) y guárdalo como
   `data/raw_pdfs/cuenta_general_1964.pdf` (no se versiona).
2. Ejecuta el OCR (PaddleOCR) sobre ≥15 páginas:

```bash
# 15 páginas con tablas presupuestarias (desde la 60), GPU si está disponible
python src/ocr_engine.py --start 60 --count 15

python src/ocr_engine.py --start 60 --count 15 --cpu   # forzar CPU
```

Salida (solo resultados pequeños): `data/processed/ocr_1964/page_XXXX.txt`,
`ocr_1964.json` y un snapshot en `data/snapshots/`.

> **GPU (opcional, recomendado):** requiere Python 3.10–3.12 (paddle no tiene
> wheels para 3.14). Combo verificado en una RTX 3050 Ti (CUDA 12):
> ```bash
> pip install paddlepaddle-gpu==2.6.1.post120 -f https://www.paddlepaddle.org.cn/whl/windows/mkl/avx/stable.html
> pip install nvidia-cudnn-cu12==8.9.7.29 "numpy<2"
> ```
> El motor registra automáticamente las DLLs de cuDNN/cuBLAS y cae a CPU si no
> hay GPU.

## Motor analítico (métricas + Hall of Shame)

Consume `data/processed/execution_<period>.csv` (salida del pipeline) y calcula:

- **Avance (%)** y **Presupuesto Paralizado** por entidad.
- KPIs globales (PIM, devengado, avance global, paralizado total).
- Agregados por nivel de gobierno, departamento y sector.
- **Hall of Shame**: entidades con PIM > 10M PEN y peor avance.

```bash
python src/analytical_engine.py --period 2025-06 --top 15
```

Salida en `data/processed/analytics_<period>/` (`kpis.json`, `by_*.csv`,
`hall_of_shame.csv`) + snapshot. Sus funciones (`kpis`, `by_dimension`,
`hall_of_shame`) las reutiliza el dashboard.

## Skills duales y CLI dinámico por período

Dos skills en `.claude/skills/` orquestados por `src/run_skill.py`:

- **executor_skill** — refresca el análisis de un período: ingesta MEF →
  métricas → (opcional) OCR 1964 → dashboard.
- **evaluator_skill** — audita la consistencia de los resultados y genera un
  reporte de QA (`data/processed/qa_report_<period>.json`).

El período es **dinámico** (sin fechas hardcodeadas). El comando de Claude Code:

```text
claude "run executor_skill for period 2025-12"
```

equivale a:

```bash
python src/run_skill.py executor_skill --period 2025-12        # ingesta + análisis
python src/run_skill.py executor_skill --period 2025-12 --with-ocr   # incluye OCR
python src/run_skill.py executor_skill --period 2025-12 --dry-run    # solo muestra los comandos
python src/run_skill.py evaluator_skill --period 2025-12       # auditoría QA
```

## Dashboard

Dashboard de 4 pestañas que reutiliza el motor analítico y lee solo los
agregados de `data/processed/` (cacheado con `st.cache_data`):

1. **KPIs 2025 + 1964** — métricas de ejecución del período y análisis histórico
   independiente del documento de 1964 (líneas por página + distribución de
   montos detectados por OCR).
2. **Distribución territorial** — paralizado por departamento, heatmap
   departamento × nivel y treemap del presupuesto (tamaño = PIM, color = avance).
3. **Hall of Shame** — entidades > 10M PEN con peor avance (umbral ajustable).
4. **Auditoría multi-agente** — reporte de QA del Evaluator + playground
   interactivo para filtrar el presupuesto en riesgo.

```bash
streamlit run app.py
```

> El selector de período toma los datos ya procesados; genera uno con
> `python src/run_skill.py executor_skill --period 2025-06 --max-rows 150000`.

> Documentación detallada de cada módulo se completará conforme avancen las etapas
> del proyecto.
