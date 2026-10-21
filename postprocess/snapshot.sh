#!/bin/bash

#Usage: bash snapshot.sh [--gossipgirl]
#Optional gossip girl flag changes print output.

source ../core/load_environment.sh || exit 1

bash ../core/run_python.sh $(jq -r ".CondaEnv" ../ens_config.json) snapshot_pt1.py $1
bash ../core/run_python.sh $(jq -r ".AnimationEnv" ../ens_config.json) snapshot_pt2.py $1
