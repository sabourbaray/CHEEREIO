#!/bin/bash

case "$(jq -er '.Scheduler // "SLURM"' ../CHEEREIO/ens_config.json)" in
    PBS)
        export CHEEREIO_ENV_NAME=$(jq -er '.CondaEnv' ../CHEEREIO/ens_config.json)
        envfile=$(jq -er '.PBSEnvironment' ../CHEEREIO/ens_config.json) || exit 1
        [[ $envfile == /* ]] || envfile="../CHEEREIO/$envfile"
        source "$envfile" || exit 1
        exec "$CHEEREIO_PYTHON" ../CHEEREIO/core/pbs_workflow.py run
        ;;
    SLURM) ;;
    *) echo "Unsupported or unreadable Scheduler" >&2; exit 1 ;;
esac

sbatch --array={START}-{END} -W run_ensemble_simulations.sh

exit 0
