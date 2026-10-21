#!/bin/bash

# Load the configured site environment. Paths are relative to CHEEREIO.
cheereio_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
envfile=$(jq -r '.EnvironmentFile // "environments/cheereio.env"' "$cheereio_dir/ens_config.json")
export CHEEREIO_ENV_NAME=$(jq -r '.CondaEnv' "$cheereio_dir/ens_config.json")
if [[ $envfile != /* ]]; then
  envfile="$cheereio_dir/$envfile"
fi
source "$envfile" || return 1
if [[ -z ${CHEEREIO_PYTHON:-} ]]; then
  eval "$(conda shell.bash hook)"
fi
