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
├── .mcp.json                  # Registro del servidor MCP para Claude Code
├── .streamlit/config.toml     # Config de performance/tema (la genera el Evaluator)
├── .claude/skills/
│   ├── executor_skill.json    # Agente Executor (ingesta, OCR, draft UI)
│   └── evaluator_skill.json   # Agente Evaluator (QA, UI/UX, reporte)
├── src/
│   ├── mcp_server.py          # Servidor MCP local (8 tools)
│   ├── data_pipeline.py       # Ingesta MEF (anti-flooding)
│   ├── ocr_engine.py          # OCR PaddleOCR del archivo 1964
│   ├── analytical_engine.py   # Métricas y Hall of Shame
│   ├── run_skill.py           # Orquestador CLI de los skills (executor/evaluator)
│   └── utils.py               # Helpers comunes
├── data/
│   ├── raw_pdfs/              # PDF fuente 1964 (no versionado)
│   ├── snapshots/            # Muestras de 5-10 filas
│   └── processed/            # Resultados agregados pequeños
└── video/link.txt            # Link del video de presentación
```

> **Archivos añadidos a la estructura base:** `src/run_skill.py` (orquestador que
> mapea `claude "run <skill> for period <p>"` a la ejecución real), `.mcp.json`
> (registro del servidor en Claude Code) y `.streamlit/config.toml` (introducido
> por el Evaluator). El resto coincide con la estructura exacta del issue.

---

## Instalación

> ⚠️ **Usa Python 3.10–3.12.** PaddlePaddle/PaddleOCR aún no publican wheels para
> Python 3.13+; en 3.14 la instalación falla. En este proyecto se usó un entorno
> conda `geo` (Python 3.10).

```bash
conda create -n geo python=3.10 -y && conda activate geo   # recomendado
# o un venv con Python 3.10-3.12:  python3.10 -m venv .venv

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
| `preview_resource(resource_id, rows)` | Muestra filas de un recurso del datastore (≈ `consultar_datastore_filtrado`, máx 10). |
| `preview_csv(url, rows)` | Primeras filas de un CSV remoto sin descargarlo (≈ `inspeccionar_esquema_csv`). |
| `descargar_documento_1964(filename)` | Descarga el PDF histórico de 1964 a `data/raw_pdfs/`. |
| `procesar_ocr_paginas_1964(start, count)` | Dispara PaddleOCR sobre ≥15 páginas del 1964. |
| `descargar_y_analizar_estadisticas(period)` | Resumen pequeño (KPIs + top Hall of Shame) del período. |

**Criterio de las 8 tools** (no más): se separó por *responsabilidad* —
descubrimiento (`search`/`info`), inspección sin flooding (`preview_*`), e
ingesta/cómputo del flujo histórico (`descargar_1964`/`ocr`/`estadísticas`).
Se omitieron tools redundantes para mantener el contexto liviano.

> **Nota CKAN→DKAN:** el issue asume CKAN (`/api/3/action/package_search`), pero
> el portal real es **DKAN** y ese endpoint da 404. La búsqueda se implementó con
> `package_list` + filtro y `package_show` (que devuelve el paquete envuelto en
> lista). Por eso `search_datasets` no usa `package_search`.

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

- **executor_skill** — refresca el análisis de un período: ingesta MEF (`--scope
  all`) → métricas → (opcional) OCR 1964 → dashboard.
- **evaluator_skill** — audita consistencia, **cross-verifica vía MCP** (re-muestrea
  el origen para detectar *extraction drift*), **modifica código** (genera
  `.streamlit/config.toml`, verifica cache/guardas) y emite un reporte
  **markdown** (`qa_report_<period>.md`) además del JSON.

El período es **dinámico** (sin fechas hardcodeadas), admite año, año-mes y
**trimestre** (`2025`, `2025-06`, `2025-Q4`). El comando de Claude Code:

```text
claude "run executor_skill for period 2025-Q4"
```

equivale a:

```bash
python src/run_skill.py executor_skill --period 2025-Q4        # ingesta + análisis
python src/run_skill.py executor_skill --period 2025-Q4 --with-ocr   # incluye OCR
python src/run_skill.py executor_skill --period 2025-Q4 --dry-run    # solo muestra los comandos
python src/run_skill.py evaluator_skill --period 2025-Q4       # auditoría QA + markdown
```

## Dashboard

Dashboard de 4 pestañas que reutiliza el motor analítico y lee solo los
agregados de `data/processed/` (cacheado con `st.cache_data`):

1. **KPIs 2025 + 1964** — métricas de ejecución, **narrativa AI Advisor**, y
   análisis histórico **independiente** del 1964 (líneas/página, montos
   detectados, categorías estructurales y **conclusiones de texto**).
2. **Distribución territorial** — paralizado por departamento, heatmap
   departamento × nivel y treemap del presupuesto (tamaño = PIM, color = avance).
3. **Hall of Shame** — entidades > 10M PEN con peor avance (umbral ajustable) y
   **desglose del gasto bloqueado por genérica** (infraestructura, bienes…).
4. **Auditoría multi-agente** — reporte **markdown** del Evaluator (checks,
   cross-verificación, optimizaciones) + playground interactivo.

```bash
streamlit run app.py
```

> El selector de período toma los datos ya procesados; genera uno con
> `python src/run_skill.py executor_skill --period 2025-Q2 --max-rows 150000`.
