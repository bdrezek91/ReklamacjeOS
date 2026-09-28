#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

printf 'Wpisz haslo skrzynki e-mail i nacisnij Enter: '
IFS= read -r -s smtp_secret
printf '\n'

if [[ -z "$smtp_secret" ]]; then
  printf 'Haslo jest puste. Niczego nie zmieniono.\n' >&2
  exit 1
fi

mkdir -p .secrets
chmod 700 .secrets
umask 077
secret_tmp=$(mktemp .secrets/smtp_password.XXXXXX)
trap 'rm -f "$secret_tmp"' EXIT
printf '%s' "$smtp_secret" > "$secret_tmp"
chmod 600 "$secret_tmp"
mv "$secret_tmp" .secrets/smtp_password
trap - EXIT
unset smtp_secret

if grep -q '^SMTP_ENABLED=' .env; then
  sed -i 's/^SMTP_ENABLED=.*/SMTP_ENABLED=true/' .env
else
  printf '\nSMTP_ENABLED=true\n' >> .env
fi
chmod 600 .env

printf 'Haslo zapisane bez wyswietlania. SMTP zostalo oznaczone jako gotowe do uruchomienia.\n'
