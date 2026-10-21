#!/bin/bash
# Shared setup/update launcher. Preserve upstream named Conda execution by default.
set -e
env_name=${1:?Expected CondaEnv followed by Python arguments}
shift
core_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
scheduler=$(jq -er '.Scheduler // "SLURM"' "$core_dir/../ens_config.json")
case "$scheduler" in
    PBS)
        if [[ ! -x ${CHEEREIO_PYTHON:-} || ${CHEEREIO_ENV_NAME:-} != "$env_name" ]]; then
            echo "PBS Python is not initialized for $env_name; source PBSEnvironment first." >&2
            exit 1
        fi
        exec "$CHEEREIO_PYTHON" "$@"
        ;;
    SLURM) exec conda run -n "$env_name" python "$@" ;;
    *) echo "Unsupported Scheduler: $scheduler" >&2; exit 1 ;;
esac
