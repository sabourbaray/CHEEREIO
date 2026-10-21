#!/bin/bash

source ../CHEEREIO/core/scheduler.sh || exit 1
submit_job --array={START}-{END} -W run_ensemble_spinup_simulations.sh
exit $?
