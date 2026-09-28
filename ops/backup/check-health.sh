#!/bin/sh
set -eu

backup_root=${BACKUP_ROOT:-/backups}
max_age_seconds=${BACKUP_MAX_AGE_SECONDS:-108000}
free_space_warn_kb=${FREE_SPACE_WARN_KB:-10485760}
last_success="$backup_root/LAST_SUCCESS"

[ -f "$last_success" ]

now=$(date +%s)
last_modified=$(stat -c %Y "$last_success")
age=$((now - last_modified))
[ "$age" -le "$max_age_seconds" ]

available_kb=$(df -Pk "$backup_root" | awk 'NR == 2 { print $4 }')
[ "$available_kb" -ge "$free_space_warn_kb" ]
