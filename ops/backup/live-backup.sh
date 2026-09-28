#!/bin/sh
set -eu

umask 077

: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"

backup_root=${BACKUP_ROOT:-/backups}
interval_seconds=${BACKUP_INTERVAL_SECONDS:-86400}
free_space_warn_kb=${FREE_SPACE_WARN_KB:-10485760}

mkdir -p \
  "$backup_root/database" \
  "$backup_root/attachments" \
  "$backup_root/manifests"

while true; do
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  dump_relative="database/postgres-$timestamp.dump"
  dump_path="$backup_root/$dump_relative"
  dump_partial="$dump_path.partial"

  pg_dump --format=custom --file="$dump_partial"
  mv "$dump_partial" "$dump_path"

  # Pliki zalacznikow sa niezmienne. Brak --delete oznacza, ze kopia nigdy
  # nie usuwa plikow, nawet gdyby zniknely ze zrodla.
  rsync -a --chmod=D700,F600 /data/reklamacje/ "$backup_root/attachments/"

  (
    cd "$backup_root"
    sha256sum "$dump_relative" > "manifests/postgres-$timestamp.sha256"
  )
  (
    cd "$backup_root/attachments"
    find . -type f -exec sha256sum {} \; | sort > "$backup_root/manifests/attachments-$timestamp.sha256"
  )

  printf '%s\n' "$timestamp" > "$backup_root/LAST_SUCCESS"

  available_kb=$(df -Pk "$backup_root" | awk 'NR == 2 { print $4 }')
  if [ "$available_kb" -lt "$free_space_warn_kb" ]; then
    printf '{"time":"%s","level":"warning","message":"Malo miejsca na backup","available_kb":%s}\n' \
      "$timestamp" "$available_kb"
    printf '%s available_kb=%s\n' "$timestamp" "$available_kb" > "$backup_root/LOW_DISK_SPACE"
  else
    printf '{"time":"%s","level":"info","message":"Backup live zakonczony","database":"%s","available_kb":%s}\n' \
      "$timestamp" "$dump_relative" "$available_kb"
  fi

  sleep "$interval_seconds"
done
