#!/bin/bash

source ../CHEEREIO/core/scheduler.sh || exit 1

maxcycles=$(jq -r '.AssimilationCyclesPerJob // 0' ../CHEEREIO/ens_config.json)
if [[ ! $maxcycles =~ ^[0-9]+$ ]]; then
  echo "AssimilationCyclesPerJob must be a nonnegative integer." >&2
  exit 1
fi

while true; do
  submit_job --array={START}-{END} -W run_ensemble_simulations.sh || exit $?
  if [ -f ../scratch/KILL_ENS ]; then
    exit 1
  fi
  if [ "$maxcycles" -eq 0 ] || [ -f ../scratch/ENSEMBLE_COMPLETE ]; then
    break
  fi
done

exit 0
