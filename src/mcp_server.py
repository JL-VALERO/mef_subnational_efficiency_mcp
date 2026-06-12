"""
mcp_server.py — Servidor MCP local (sin autenticación privilegiada).

Expone "tools" para que Claude Code interactúe de forma segura con el portal
`datosabiertos.gob.pe`, delegando la descarga y el filtrado pesado a
`data_pipeline.py` (respetando la regla anti-context-flooding).

NOTA: scaffold inicial — la implementación se realiza en la Etapa 1
(feature/mcp-server-core).
"""

# TODO (Etapa 1): inicializar el servidor MCP y registrar las tools.
