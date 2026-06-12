"""
data_pipeline.py — Ingesta de datos fiscales del MEF 2025.

Regla anti-context-flooding: descarga y filtra los CSV/JSON grandes localmente,
guardando únicamente:
    - snapshots de 5-10 filas  -> data/snapshots/
    - agregados pequeños        -> data/processed/

El período es dinámico (controlado por CLI), nunca hardcodeado.

NOTA: scaffold inicial — la implementación se realiza en la Etapa 2
(feature/data-pipeline-2025).
"""

# TODO (Etapa 2): descarga + filtrado local + generación de snapshots.
