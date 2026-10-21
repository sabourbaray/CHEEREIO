#!/bin/bash

# Keep scheduler commands separate from the ensemble workflow.
cheereio_core=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
Scheduler=$(jq -r '.Scheduler // "SLURM"' "$cheereio_core/../ens_config.json")
case "$Scheduler" in
  SLURM|PBS) ;;
  *) echo "Unsupported Scheduler: $Scheduler" >&2; return 1 ;;
esac

submit_job() {
  if [[ $Scheduler == PBS ]]; then
    source "$cheereio_core/load_environment.sh" || return 1
    python "$cheereio_core/pbs_submit.py" "$@"
  else
    sbatch "$@"
  fi
}

run_geoschem() {
  if [[ $Scheduler == PBS ]]; then
    time -p ./gcclassic
  else
    srun -c "$OMP_NUM_THREADS" time -p ./gcclassic
  fi
}
