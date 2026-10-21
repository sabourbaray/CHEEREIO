#!/bin/bash

# A site environment may supply its active Python; otherwise use named Conda.
env_name=${1:?Expected CondaEnv followed by Python arguments}
shift
if [[ -n ${CHEEREIO_PYTHON:-} && ${CHEEREIO_ENV_NAME:-} == "$env_name" ]]; then
  exec "$CHEEREIO_PYTHON" "$@"
else
  exec conda run -n "$env_name" python "$@"
fi
