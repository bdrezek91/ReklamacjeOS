#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

printf 'Wpisz oficjalny klucz TypeSafe API i nacisnij Enter: '
IFS= read -r -s typesafe_secret
printf '\n'

if [[ -z "$typesafe_secret" ]]; then
  printf 'Klucz jest pusty. Niczego nie zmieniono.\n' >&2
  exit 1
fi

mkdir -p .secrets
chmod 700 .secrets
umask 077
secret_tmp=$(mktemp .secrets/typesafe_api_key.XXXXXX)
trap 'rm -f "$secret_tmp"' EXIT
printf '%s' "$typesafe_secret" > "$secret_tmp"
chmod 640 "$secret_tmp"
mv "$secret_tmp" .secrets/typesafe_api_key
trap - EXIT
unset typesafe_secret

if grep -q '^TYPESAFE_ENABLED=' .env; then
  sed -i 's/^TYPESAFE_ENABLED=.*/TYPESAFE_ENABLED=false/' .env
else
  printf '\nTYPESAFE_ENABLED=false\n' >> .env
fi
typesafe_secret_gid=$(id -g)
if grep -q '^TYPESAFE_SECRET_GID=' .env; then
  sed -i "s/^TYPESAFE_SECRET_GID=.*/TYPESAFE_SECRET_GID=$typesafe_secret_gid/" .env
else
  printf 'TYPESAFE_SECRET_GID=%s\n' "$typesafe_secret_gid" >> .env
fi
chmod 600 .env

printf 'Klucz zapisany bez wyswietlania. Jev pozostaje wylaczony; wlacz go po wdrozeniu.\n'
