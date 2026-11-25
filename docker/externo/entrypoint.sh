#!/bin/sh
# Servidor "de Internet" del laboratorio: simula un host publico al que los
# clientes internos llegan a traves del NAT del router.
set -eu

echo "[externo] Servidor web publico en 0.0.0.0:8000 (IP 10.99.0.2)"
exec python3 -m http.server 8000 --directory /srv
