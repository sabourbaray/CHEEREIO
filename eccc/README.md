# CHEEREIO on ECCC PBS Pro

PBS uses the native CHEEREIO ensemble scripts, including their restart checks,
LETKF calls, cleanup and date updates. The submission wrapper translates the
resource directives in those scripts to PBS Pro. It does not inspect species,
restart contents or diagnostic collections. The previous `pbs_workflow.py`
stage controller and `eccc_preflight.py` are removed.

## Configuration

The optional settings in `ens_config.json` are:

- `Scheduler`: `SLURM` (default when absent) or `PBS`.
- `EnvironmentFile`: site environment, relative to the deployed CHEEREIO
  directory or an absolute path. Runtime default: `environments/cheereio.env`.
- `PBSQueue`, `PBSAccount`: optional PBS queue and project account.
- `AssimilationCyclesPerJob`: number of complete forecast/assimilation/cleanup
  iterations per allocation. `0` (default) retains the continuous native loop.
  A positive value makes `run_ens.sh` wait for the whole array, then submit the
  next array until CHEEREIO creates `ENSEMBLE_COMPLETE`. This works with both
  schedulers. It does not change the assimilation window or scientific settings.

Resources come from the usual `NumCores`, `Memory` and `WallTime` settings
through the generated job scripts. The same allocation runs GEOS-Chem and its
assigned analysis work; member 1 also combines the results. Memory must therefore
cover the analysis and combination, not just the forecast. Numeric `Memory` is
in MB, as in Slurm. PBS uses one node per member.

All members, including control, must be able to run concurrently, as in native
CHEEREIO. Do not limit a PBS array to fewer running jobs than the ensemble size;
a queue/user limit below that size will also prevent the native barriers from
completing. The previous `PBSMaxConcurrent` and per-stage `PBSResources` settings
are no longer used.

Job cycling releases allocations at clean native cycle boundaries. An entire
cycle must fit within `WallTime`. A failed, deleted or walltime-killed task stops
submission; no automatic restart of a partially completed cycle is attempted.
PBS job output and exit records are under `ensemble_runs/logs/pbs-*`, alongside
the native LETKF logs. GEOS-Chem logs remain in the member directories.

## Repeat the short experiment

Use a fresh deployment; do not patch the CHEEREIO copy of an existing run.
From the source checkout, select the supplied config:

```bash
cp ens_config_CH4_ECCC_native.json ens_config.json
```

This repeats January 2–5, 2019 at 4x5 with four perturbed members and control,
using the existing restart, observations and ExtData paths. Its new run name is
`ECCC_CH4_20190102_20190105_native01`. Choose another name if that directory
already exists. It requests 8 CPUs and 32000 MB per member, six hours per job,
and one complete assimilation cycle per job. Adjust queue/account/walltime to
site requirements before setup. This is a workflow test, not a production
inversion configuration.

Follow the native two-pass setup procedure, editing the switches near the top
of `setup_ensemble.sh` (no exported setup variables are needed):

1. Set `SetupTemplateRundir=true` and all four other switches to `false`.
   Run `bash setup_ensemble.sh`. Inspect the generated template, especially
   HEMCO emissions/scaling-factor links, as instructed by CHEEREIO's documentation.
2. Set `SetupTemplateRundir=false`, `CompileTemplateRundir=true`,
   `SetupSpinupRun=false`, `SetupControlRun=false`, `SetupEnsembleRuns=true`.
   Run `bash setup_ensemble.sh` again in an allocation with at least 8 CPUs.
3. Change to the new run's `ensemble_runs` directory and launch the native
   `nohup bash run_ens.sh > run_ens.log 2>&1 &` from a site-approved persistent
   session. Environment loading is handled by the configured site file.
4. After successful completion, change to the deployed `CHEEREIO/postprocess`
   directory and run `bash snapshot.sh` in an appropriate compute allocation.
   This is the native snapshot/image/movie workflow; results go to the run's
   `postprocess` directory. There is no separate PBS plotting controller.

The same `run_ensspin.sh` entry point submits separate ensemble spinup when
configured. For a standalone global spinup/control `.run` file or the native
`postprocess.batch`, PBS users can run the script with `bash` from its normal
working directory to submit it (or execute it inside an existing allocation).
Slurm users continue submitting those files with `sbatch`. Auxiliary backup and
landmask batch utilities retain their original Slurm headers.

The wrapper does not reject run-in-place, burn-in, concentration updates or other
species. These follow the native code paths; removing a scheduler restriction
is not scientific validation of those configurations. GNU parallel is still
required for `MaxPar > 1`, as in upstream CHEEREIO.
