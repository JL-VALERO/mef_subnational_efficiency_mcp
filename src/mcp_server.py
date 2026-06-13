"""
mcp_server.py — Servidor MCP local (sin autenticación privilegiada).

Expone "tools" para que Claude Code interactúe de forma segura con el portal
de datos abiertos peruano (`datosabiertos.gob.pe`, DKAN con API estilo CKAN).
El servidor NO
ingiere datasets completos: solo devuelve catálogos reducidos y snapshots de
pocas filas (regla anti-context-flooding). El filtrado pesado de períodos vive
en `data_pipeline.py` (Etapa 2).

Ejecutar como servidor MCP (stdio):
    python src/mcp_server.py

Registrar en Claude Code (.mcp.json / claude mcp add):
    command: python   args: ["src/mcp_server.py"]
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permite `import utils` tanto si se ejecuta el archivo directamente
# (python src/mcp_server.py) como vía el runner MCP.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import utils  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

log = utils.get_logger("mef_mcp.server")
mcp = FastMCP("mef-subnational-efficiency")


@mcp.tool()
def health_check() -> dict:
    """Verifica que el servidor MCP está vivo y muestra el portal configurado."""
    return {
        "status": "ok",
        "server": "mef-subnational-efficiency",
        "portal": utils.PORTAL_BASE_URL,
        "max_snapshot_rows": utils.MAX_SNAPSHOT_ROWS,
    }


@mcp.tool()
def search_datasets(query: str, limit: int = 5) -> dict:
    """
    Busca datasets por palabra clave sobre el catálogo del portal.

    El portal es DKAN y no expone búsqueda full-text por API, así que se filtra
    la lista de identificadores (package_list) por los tokens de `query` y se
    enriquecen solo los primeros `limit` resultados con su título y nº de
    recursos. Pensado para localizar datasets de ejecución de gasto del MEF
    (ej.: "ejecucion gastos 2016"). `limit` se acota a 20.
    """
    n = max(1, min(int(limit), 20))
    names = utils.ckan_action("package_list") or []
    tokens = [utils.fold(t) for t in query.split() if t]
    matched = [name for name in names if all(tok in utils.fold(name) for tok in tokens)]

    returned = []
    for slug in matched[:n]:
        try:
            pkg = utils.package_show(slug)
            returned.append(
                {
                    "id": pkg.get("id"),
                    "name": slug,
                    "title": pkg.get("title"),
                    "num_resources": len(pkg.get("resources", [])),
                }
            )
        except Exception as exc:  # noqa: BLE001
            returned.append({"name": slug, "error": str(exc)[:120]})

    log.info("search_datasets q=%r -> %d coincidencias", query, len(matched))
    return {"total_matches": len(matched), "returned": returned}


@mcp.tool()
def get_dataset_info(dataset_id: str) -> dict:
    """
    Devuelve metadatos reducidos de un dataset y la lista de sus recursos
    (package_show). Incluye por recurso el id, formato, URL de descarga y si
    está activo en el datastore. Útil para decidir cómo previsualizarlo.
    """
    result = utils.package_show(dataset_id)
    resources = [
        {
            "id": res.get("id"),
            "name": res.get("name") or res.get("title"),
            "format": (res.get("format") or "").lstrip("."),
            "url": res.get("url"),
            "datastore_active": res.get("datastore_active", False),
        }
        for res in result.get("resources", [])
    ]
    return {
        "id": result.get("id"),
        "name": result.get("name"),
        "title": result.get("title"),
        "num_resources": len(resources),
        "resources": resources,
    }


@mcp.tool()
def preview_resource(resource_id: str, rows: int = 5) -> dict:
    """
    Previsualiza un recurso cargado en el datastore (datastore_search),
    devolviendo unas pocas filas. `rows` se acota al tope anti-flooding (máx 10).

    Si el recurso NO está en el datastore (muchos CSV del portal no lo están),
    devuelve un aviso sugiriendo usar `preview_csv` con la URL del recurso
    (visible en get_dataset_info).
    """
    n = utils.cap_rows(rows)
    try:
        result = utils.ckan_action(
            "datastore_search", {"resource_id": resource_id, "limit": n}
        )
    except Exception as exc:  # noqa: BLE001
        log.info("preview_resource %s sin datastore: %s", resource_id, exc)
        return {
            "resource_id": resource_id,
            "datastore_active": False,
            "hint": "El recurso no está en el datastore. Usa preview_csv(url, rows) "
            "con la URL del recurso (mira get_dataset_info).",
        }

    fields = [f.get("id") for f in result.get("fields", [])]
    snapshot = {
        "resource_id": resource_id,
        "datastore_active": True,
        "total_records": result.get("total"),
        "fields": fields,
        "sample_rows": result.get("records", []),
    }
    path = utils.save_snapshot(f"resource_{resource_id}", snapshot)
    snapshot["snapshot_path"] = str(path)
    return snapshot


@mcp.tool()
def preview_csv(url: str, rows: int = 5) -> dict:
    """
    Previsualiza un CSV remoto leyendo solo sus primeras filas (sin descargar
    el archivo completo). Plan B cuando el recurso no está en el datastore.
    `rows` se acota al tope anti-flooding (máx 10). Guarda un snapshot.
    """
    head = utils.stream_csv_head(url, rows)
    snapshot = {
        "source_url": url,
        "delimiter": head["delimiter"],
        "header": head["header"],
        "sample_rows": head["sample_rows"],
    }
    safe_name = "".join(c if c.isalnum() else "_" for c in url)[-60:]
    path = utils.save_snapshot(f"csv_{safe_name}", snapshot)
    snapshot["snapshot_path"] = str(path)
    log.info("preview_csv %s -> %d filas", url[:60], len(head["sample_rows"]))
    return snapshot


# URL de descarga del documento histórico 1964 (Google Books, dominio público).
GOOGLE_BOOKS_1964_PDF = (
    "https://books.google.com/books/download/Cuenta_general.pdf?id=9YkbAQAAMAAJ&output=pdf"
)


@mcp.tool()
def descargar_documento_1964(filename: str = "cuenta_general_1964.pdf") -> dict:
    """
    Descarga el PDF histórico de 1964 ("Cuenta General de la República") a
    data/raw_pdfs/. Best-effort: Google Books puede exigir captcha; en ese caso
    devuelve un aviso para descarga manual (ver README).
    """
    import requests

    utils.RAW_PDFS_DIR.mkdir(parents=True, exist_ok=True)
    dest = utils.RAW_PDFS_DIR / filename
    try:
        r = requests.get(
            GOOGLE_BOOKS_1964_PDF,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            stream=True,
            timeout=120,
        )
        if "pdf" not in r.headers.get("content-type", ""):
            r.close()
            return {
                "status": "captcha",
                "dest": str(dest),
                "message": "Google Books exigió captcha. Descárgalo manualmente "
                "(https://books.google.com.pe/books?id=9YkbAQAAMAAJ) y guárdalo aquí.",
            }
        n = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                n += len(chunk)
        r.close()
        log.info("descargar_documento_1964 -> %s (%d bytes)", dest, n)
        return {"status": "ok", "dest": str(dest), "bytes": n}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)[:200]}


@mcp.tool()
def procesar_ocr_paginas_1964(start: int = 60, count: int = 15, dpi: int = 200, lang: str = "es") -> dict:
    """
    Dispara PaddleOCR sobre >=15 páginas del documento 1964 (envuelve
    ocr_engine.run). Requiere el PDF en data/raw_pdfs/ y un entorno con paddle
    (usa GPU si está disponible). Devuelve los metadatos del run.
    """
    import ocr_engine

    return ocr_engine.run(ocr_engine.DEFAULT_PDF, start, count, dpi, lang)


@mcp.tool()
def descargar_y_analizar_estadisticas(period: str) -> dict:
    """
    Resumen estadístico pequeño de un período (KPIs + top Hall of Shame) a
    partir del agregado en data/processed/. Lectura ligera (anti-flooding); si
    el período no existe, indica cómo generarlo con el Executor.
    """
    import analytical_engine as ae

    if not ae.execution_path(period).exists():
        return {
            "status": "missing",
            "hint": f"Genera primero: python src/run_skill.py executor_skill --period {period}",
        }
    df = ae.load_execution(period)
    shame = ae.hall_of_shame(df, top=5)
    return {
        "status": "ok",
        "period": period,
        "kpis": ae.kpis(df),
        "hall_of_shame_top5": shame.to_dict("records"),
    }


if __name__ == "__main__":
    log.info("Iniciando servidor MCP 'mef-subnational-efficiency' (stdio)…")
    mcp.run()
