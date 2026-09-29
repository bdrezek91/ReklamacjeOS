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
umask 077
secret_tmp=$(mktemp .secrets/typesafe_api_key.XXXXXX)
trap 'rm -f "$secret_tmp"' EXIT
printf '%s' "$typesafe_secret" > "$secret_tmp"
chmod 640 "$secret_tmp"
mv "$secret_tmp" .secrets/typesafe_api_key
trap - EXIT
unset typesafe_secret

typesafe_secret_gid=$(id -g)
printf 'Klucz zapisany bez wyswietlania (GID %s). Jev pozostaje wylaczony; wlacz go po wdrozeniu.\n' "$typesafe_secret_gid"
