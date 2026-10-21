#!/bin/bash

if [[ $(jq -r '.Scheduler // "SLURM"' ../CHEEREIO/ens_config.json) == PBS ]]; then
    exec python ../CHEEREIO/core/pbs_workflow.py run
fi

sbatch --array={START}-{END} -W run_ensemble_simulations.sh

exit 0