"""
ocr_engine.py — OCR del archivo ministerial histórico de 1964.

Documento fuente: "Cuenta General de la República" (1964), Contraloría General
del Perú, obtenido del portal Fuentes Históricas del Perú (Google Books).
Colócalo en data/raw_pdfs/cuenta_general_1964.pdf (ver README).

Flujo:
    1. Renderiza N páginas del PDF a imagen con PyMuPDF (sin poppler).
    2. Pasa cada imagen por PaddleOCR (>= 15 páginas, requisito de la tarea).
    3. Reconstruye texto/tablas y guarda solo resultados pequeños:
        - data/processed/ocr_1964/page_XXXX.txt   (texto por página)
        - data/processed/ocr_1964/ocr_1964.json    (líneas + confianza por página)
        - data/processed/ocr_1964_meta.json        (metadatos del run)
        - data/snapshots/ocr_1964_sample.json      (muestra de líneas)

Uso (páginas seleccionables, sin nada hardcodeado):
    python src/ocr_engine.py                       # 15 páginas desde la 1
    python src/ocr_engine.py --start 10 --count 20 # 20 páginas desde la 10
    python src/ocr_engine.py --pdf data/raw_pdfs/otro.pdf --dpi 250
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils  # noqa: E402

log = utils.get_logger("mef_mcp.ocr")


def _enable_gpu_dlls() -> None:
    """
    En Windows, registra los directorios de DLLs de los paquetes nvidia-*-cu12
    (cuDNN, cuBLAS, nvrtc) para que paddlepaddle-gpu encuentre cudnn64_8.dll.
    Es best-effort y no-op si no aplica (Linux o sin esos paquetes).
    """
    if os.name != "nt":
        return
    try:
        import nvidia  # paquete namespace instalado por nvidia-*-cu12

        base = Path(list(nvidia.__path__)[0])
        for sub in base.iterdir():
            bindir = sub / "bin"
            if bindir.is_dir():
                os.add_dll_directory(str(bindir))
                # paddle resuelve cudnn/cublas vía la variable PATH del proceso.
                os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
    except Exception as exc:  # noqa: BLE001
        log.debug("No se registraron DLLs nvidia: %s", exc)


def _gpu_available() -> bool:
    """True si paddle está compilado con CUDA y hay al menos una GPU visible."""
    try:
        import paddle

        return bool(paddle.is_compiled_with_cuda()) and paddle.device.cuda.device_count() > 0
    except Exception:  # noqa: BLE001
        return False

DEFAULT_PDF = utils.RAW_PDFS_DIR / "cuenta_general_1964.pdf"
OCR_OUT_DIR = utils.PROCESSED_DIR / "ocr_1964"
IMAGES_DIR = OCR_OUT_DIR / "images"
MIN_PAGES = 15  # requisito mínimo de la tarea


def render_pages(pdf_path: Path, start: int, count: int, dpi: int) -> list[tuple[int, Path]]:
    """
    Renderiza `count` páginas del PDF (a partir de `start`, base 1) a PNG con
    PyMuPDF. Devuelve [(numero_pagina, ruta_imagen), ...].
    """
    import fitz  # PyMuPDF

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    total = doc.page_count
    log.info("PDF '%s' con %d páginas.", pdf_path.name, total)

    start_idx = max(0, start - 1)
    end_idx = min(total, start_idx + count)
    rendered: list[tuple[int, Path]] = []
    for idx in range(start_idx, end_idx):
        page = doc[idx]
        pix = page.get_pixmap(dpi=dpi)
        out = IMAGES_DIR / f"page_{idx + 1:04d}.png"
        pix.save(out)
        rendered.append((idx + 1, out))
    doc.close()
    log.info("Renderizadas %d páginas (%d..%d) a %d DPI.", len(rendered), start, end_idx, dpi)
    return rendered


def _build_ocr(lang: str, use_gpu: bool):
    """Instancia PaddleOCR de forma compatible con distintas versiones."""
    from paddleocr import PaddleOCR

    try:
        return PaddleOCR(use_angle_cls=True, lang=lang, use_gpu=use_gpu, show_log=False)
    except TypeError:
        # Versiones nuevas eliminaron algunos kwargs.
        return PaddleOCR(lang=lang)


def _ocr_image(ocr, image_path: Path) -> list[dict]:
    """
    Ejecuta OCR sobre una imagen y normaliza la salida a una lista de
    {'text', 'confidence'} (descarta cajas, conserva orden de lectura).
    """
    raw = ocr.ocr(str(image_path))
    lines: list[dict] = []
    if not raw:
        return lines
    # PaddleOCR devuelve [ [ [bbox, (text, conf)], ... ] ] por imagen.
    page = raw[0] if isinstance(raw[0], list) else raw
    for item in page or []:
        try:
            text, conf = item[1][0], float(item[1][1])
        except (IndexError, TypeError, ValueError):
            continue
        if text and text.strip():
            lines.append({"text": text.strip(), "confidence": round(conf, 4)})
    return lines


def run(pdf: Path, start: int, count: int, dpi: int, lang: str, prefer_gpu: bool = True) -> dict:
    """Renderiza y aplica OCR a las páginas; persiste resultados pequeños."""
    pdf = Path(pdf)
    if not pdf.exists():
        raise FileNotFoundError(
            f"No se encontró el PDF en {pdf}. Descárgalo (ver README) y vuelve a intentar."
        )
    if count < MIN_PAGES:
        log.warning("count=%d < mínimo requerido (%d).", count, MIN_PAGES)

    OCR_OUT_DIR.mkdir(parents=True, exist_ok=True)
    pages = render_pages(pdf, start, count, dpi)

    if prefer_gpu:
        _enable_gpu_dlls()
    use_gpu = prefer_gpu and _gpu_available()
    log.info("Inicializando PaddleOCR (lang=%s, device=%s)…", lang, "GPU" if use_gpu else "CPU")
    ocr = _build_ocr(lang, use_gpu)

    per_page: list[dict] = []
    total_lines = 0
    for page_no, img in pages:
        lines = _ocr_image(ocr, img)
        total_lines += len(lines)
        # Texto plano por página.
        txt_path = OCR_OUT_DIR / f"page_{page_no:04d}.txt"
        txt_path.write_text("\n".join(l["text"] for l in lines), encoding="utf-8")
        per_page.append({"page": page_no, "n_lines": len(lines), "lines": lines})
        log.info("Página %d -> %d líneas OCR.", page_no, len(lines))

    # JSON consolidado (líneas + confianza por página).
    (OCR_OUT_DIR / "ocr_1964.json").write_text(
        json.dumps(per_page, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    meta = {
        "pdf": str(pdf),
        "pages_processed": len(pages),
        "page_range": [start, start + len(pages) - 1] if pages else [],
        "dpi": dpi,
        "lang": lang,
        "device": "GPU" if use_gpu else "CPU",
        "total_lines": total_lines,
        "meets_minimum": len(pages) >= MIN_PAGES,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(OCR_OUT_DIR),
    }
    (utils.PROCESSED_DIR / "ocr_1964_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Snapshot: primeras líneas de las primeras páginas.
    sample_lines = []
    for p in per_page:
        for l in p["lines"][:5]:
            sample_lines.append({"page": p["page"], **l})
        if len(sample_lines) >= utils.MAX_SNAPSHOT_ROWS:
            break
    utils.save_snapshot("ocr_1964_sample", {"meta": meta, "sample_lines": sample_lines[: utils.MAX_SNAPSHOT_ROWS]})

    log.info(
        "OCR OK: %d páginas, %d líneas. ¿Cumple mínimo %d? %s",
        len(pages), total_lines, MIN_PAGES, meta["meets_minimum"],
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR del archivo histórico 1964 (PaddleOCR).")
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="Ruta al PDF de 1964.")
    parser.add_argument("--start", type=int, default=1, help="Primera página (base 1).")
    parser.add_argument("--count", type=int, default=MIN_PAGES, help="Nº de páginas (>=15).")
    parser.add_argument("--dpi", type=int, default=200, help="Resolución de render.")
    parser.add_argument("--lang", default="es", help="Idioma de OCR (es por defecto).")
    parser.add_argument("--cpu", action="store_true", help="Forzar CPU (por defecto usa GPU si está disponible).")
    args = parser.parse_args()

    meta = run(Path(args.pdf), args.start, args.count, args.dpi, args.lang, prefer_gpu=not args.cpu)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
