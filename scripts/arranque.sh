#!/bin/sh
# Arranque en despliegue.
#   scripts/arranque.sh web      → migraciones + estudio (si la BD está vacía) + API
#   scripts/arranque.sh worker   → cola + cron (no migra, para no competir con la web)
set -e
case "$1" in
  web)
    alembic upgrade head
    if [ "${DEMO:-false}" = "true" ]; then
      python -m app.db.seed --si-vacia --demo-clientes 30 --demo-reservas
    else
      python -m app.db.seed --si-vacia
    fi
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" ;;
  worker)
    exec arq app.worker.WorkerSettings ;;
  *)
    echo "Uso: $0 web|worker" >&2; exit 1 ;;
esac
