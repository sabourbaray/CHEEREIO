# CHEEREIO on ECCC PBS Pro

This is a **locally tested deployment candidate**, not evidence of a completed ECCC run. Target CHEEREIO commit: `28ccdba77cb3803418a64009c817d29630492dba`. The tested template source is GCClassic **14.1.1**, commit `d1d71ef9f5f3b2bbb0bbda4843819bf57098f223`, with GEOS-Chem submodule `4722f288e90291ba904222f4bbe4fc216d17c34a`.

## Execution contract

Use a site-approved persistent interactive controller, as with the operational IMI workflow. It submits blocking PBS jobs; it is not itself a six-hour batch job. Each assimilation window uses:

1. A native array of independent GEOS-Chem forecasts, including control member 0 when enabled.
2. A native array of LETKF workers. Array entries select ensemble/core **column partitions**, not independent scientific inversions. Each process reads the full ensemble and its observations.
3. One job to combine columns, update every member's restart/scaling factors, and advance dates.

All forecast jobs have exited before analysis starts. All analysis jobs have exited before any combined update. The next forecast cannot start before the update job has finished. Compute allocations are released at each barrier. PBS column products are isolated by assimilation timestamp under scheduler/columns, so a previous window cannot contaminate the next update. Three small I/O hooks select this directory through CHEEREIO_COLUMN_ROOT; the default Slurm column paths stay unchanged. The science remains in `par_letkf.py`, `combine_columns_and_update.py`, and the existing date/HISTORY routines.

The controller never equates an empty queue, a restart filename, or the absence of KILL_ENS with success. It checks the blocking qsub result and every expected wrapper exit record. Forecast workers additionally check the endpoint NetCDF's CH4/time and continuous hourly concentration/pressure diagnostics. Multi-member arrays use explicit `-r y`; singleton stages use ordinary jobs. `PBSMaxConcurrent` can be smaller than the ensemble without the deadlock risk of holding members in allocation while waiting for others to start.

`Scheduler` defaults to the existing Slurm path when absent. Only `run_ens.sh` dispatches the new PBS workflow; legacy spinup, standalone control, backup/restore, snapshot shell, and other batch utilities have **not** all been ported. Use the PBS `plot` command below for snapshots.

The PBS validation supports GCClassic 14 methane, an optional control within the ensemble, and the regular analysis workflow. It rejects separate model/ensemble spinup and approximate Varon rerun. Existing ordinary run-in-place, non-approximate rerun, first-period scaling and burn-in flags are passed through to upstream logic, but only the supplied ordinary daily configuration has integrated validation here. Other species and advanced modes need their own acceptance runs and restart validators.

## Walltime and scientific scope

Neither an assimilation window nor an EnKF algorithm guarantees a three/six-hour walltime. The longest **individual** forecast, analysis worker, and combined update must each fit the applicable limit, including imports and I/O. The initial period is START_DATE to ASSIM_START_DATE and can be much longer than ASSIM_TIME. Production spinup can also be long. Run-in-place and rerun configurations increase forecast work.

This implementation does not checkpoint a partially executed forecast, LETKF worker, or combined update. An overrun stops the workflow. Do not shorten ASSIM_TIME automatically to fix it: that changes the inference problem. Measure runtimes first; increase appropriate resources, reduce a scientifically justified test domain, or add separately validated model/operator checkpointing if necessary.

The supplied configuration is a three-window **smoke test**, 2023-01-01 through 2023-01-04 end-exclusive: global 4x5, GEOS-FP, 47 levels, four perturbed members plus control, daily assimilation, and blended TROPOMI. It avoids introducing an unsupported arbitrary GTA domain and its boundary-condition preparation into the first scheduler test. It is not the upstream methane paper configuration and is not a production-recommended inversion design.

The smoke starts from an existing realistic atmospheric restart and allows only a day of perturbation evolution before its first update. That is deliberately insufficient evidence of mature methane ensemble covariance. A production experiment needs atmospheric **and ensemble** spinup, a substantially larger ensemble (the upstream guide discusses about 32), evaluation of ensemble spread, localization/inflation/error assumptions, and independent validation observations. Four members have anomaly rank at most three. A successful plot is not scientific validation.

## Site paths and prerequisites

The following two paths are copied from the operational IMI GTA configuration:

- ExtData: `/home/rsb001/geoschem/ExtData`
- 2023 blended TROPOMI: `/home/rsb001/site7/data/tropomi/blnd2023`

The new source/output paths `/home/rsb001/geoschem/CHEEREIO` and `/home/rsb001/geoschem/cheereio_output_dir` are proposed locations. Adjust them if needed.

**The RESTART_FILE in the test config is a destination to prepare, not a discovered file.** Supply an actual 2023-01-01, 47-level, global 4x5 GEOS-Chem methane restart, with compatible meteorology. IMI's `/ExtData/imi_bc/blnd/v2024-06/GEOSChem.BoundaryConditions.*` is not an interchangeable restart. Do not merely rename it or relabel its date: its variables, time averaging, resolution and initialization semantics differ. CHEEREIO's `prep_par.py` derives the assimilation grid from RESTART_FILE, even if GEOS-Chem could regrid an input. The preflight rejects a mismatched restart.

Prepare the restart through a compatible standalone GEOS-Chem run/regridding workflow and check scientific consistency. If you do not already have one, obtaining that input is an outstanding prerequisite; this patch does not invent the atmospheric state or regrid BCs into an instantaneous restart.

Create a separate Python environment; do not modify the working IMI environment:

```bash
source ~/.startmamba_imi
conda env create -f environments/cheereio-eccc.yml
```

Load your established ECCC GEOS-Chem compiler/NetCDF environment first. Optionally set `ECCC_GC_ENV` to an existing environment script that loads it. Then:

```bash
source environments/eccc.env
```

The script reuses the known `~/.startmamba_imi` initialization but activates `cheereio-eccc`. No Harvard modules are loaded. The animation helper uses explicit nearest shading and full-size frame arrays for matplotlib 3.8 compatibility. No ECCC compiler/module names, queue or account are guessed. The dependency recipe is a proposed portable conda-forge environment; its solve and GEOS-Chem shared-library compatibility must be verified on ECCC. Local scientific checks used Python 3.12 with NumPy 1.26.4, SciPy 1.13.1, pandas 2.2.2 and xarray 2024.5.0; the supplied environment requests Python 3.10. Keep NumPy below 2 for this tested stack. The three obsolete `np.float` dtype aliases were replaced with `float` without changing the calculation.

## Prepare source and config

From the patched CHEEREIO source root:

```bash
git clone --branch 14.1.1 https://github.com/geoschem/GCClassic.git GCClassic
git -C GCClassic submodule update --init --recursive
cp ens_config_CH4_ECCC_2023.json ens_config.json
```

Edit ASSIM_PATH, MY_PATH, RESTART_FILE, and optional PBSQueue/PBSAccount. Use a fresh RUN_NAME. Disable `settings_to_override.json` overrides. Paths without spaces are required by legacy setup shell scripts even though the new PBS wrapper quotes its own arguments.

Run the input check with the environment active:

```bash
(cd core && python eccc_preflight.py)
```

It verifies imports, external commands, exact restart grid/time/fields, and the filename convention and one blended observation file per day. It does not certify every orbit or all ExtData. TROPOMI data must retain the S5P filename convention used by CHEEREIO's date index. The BLENDED reader expects root-level variables, including methane_mixing_ratio_blended, dry-air subcolumns, prior profiles, averaging kernels and surface fields. It filters qa_value > 0.5 and land pixels. A directory of operational grouped Level-2 files is a different product even if it is also called XCH4.

## Setup, dry run, and launch

Use an approved setup/build allocation. The setup commands below run immediately in the current shell; they do not automatically submit their own batch jobs. Compilation is bounded by CHEEREIO_BUILD_CPUS (default two).

First create only the template:

```bash
SetupTemplateRundir=true CompileTemplateRundir=false \
SetupSpinupRun=false SetupControlRun=false SetupEnsembleRuns=false \
bash setup_ensemble.sh
```

Inspect the generated template HEMCO_Config.rc, especially CH4 source selection and scaling-factor linkage. Confirm DATA_PATH is correct; the PBS setup explicitly uses it instead of silently taking an unrelated ~/.geoschem/config. Preserve the `{DATE1}`, `{TIME1}`, `{DATE2}`, `{TIME2}` placeholders in the template geoschem_config.yml.

Then compile and create members:

```bash
CHEEREIO_BUILD_CPUS=8 SetupTemplateRundir=false CompileTemplateRundir=true \
SetupSpinupRun=false SetupControlRun=false SetupEnsembleRuns=true \
bash setup_ensemble.sh
```

CHEEREIO copies its code/config into the run directory. All execution below uses **that deployed copy**. Changing source after setup does not update an existing ensemble.

In a compute allocation with the runtime loaded, perform a GEOS-Chem dry run in member 1 before normal forecasts:

```bash
cd /home/rsb001/geoschem/cheereio_output_dir/ECCC_CH4_20230101_20230104_smoke/ensemble_runs/ECCC_CH4_20230101_20230104_smoke_0001
ldd ./gcclassic
./gcclassic --dryrun > dryrun.log 2>&1
python download_data.py dryrun.log -skip-download
```

Review the unique-path list and `download_data.yml` mirrors. Use `python download_data.py dryrun.log MIRROR-NAME` with an actual configured mirror on a site-permitted data-transfer node. The data root is the same ExtData tree as IMI. Downloads are not triggered by applying the patch. Data coverage in a one-day dry run does not prove later dates are present; inspect all three days or run additional dry runs in an isolated copy with explicit dates.

From your persistent orchestration session, activate the environment and launch:

```bash
cd /home/rsb001/geoschem/cheereio_output_dir/ECCC_CH4_20230101_20230104_smoke/ensemble_runs
source ../CHEEREIO/environments/eccc.env
bash run_ens.sh
python ../CHEEREIO/core/pbs_workflow.py plot
```

The plot stage invokes upstream snapshot_pt1.py and snapshot_pt2.py in a PBS allocation, using the same environment with matplotlib, Basemap and ffmpeg. It produces upstream `SNAPSHOT_*` graphics and animation in the run's postprocess directory. Upstream BigY values are forecast-equivalent simulated observations at each update, not a separately rerun posterior verification. The control is present so the standard control-comparison plots are meaningful.

For a three-hour queue, change **all four** PBSResources walltime values to `03:00:00` before setup. Those fields govern this PBS driver; the legacy WallTime fields do not. Do not change a running experiment's config. Record real `resources_used.walltime`, peak memory, and stage throughput before scaling up. PBS array limits apply per subjob; queuing and throttling may make the whole controller run far longer.

## Failure handling and evidence

Evidence is outside ensemble_runs so upstream directory globbing cannot mistake scheduler directories for ensemble members:

- `scheduler/state.json`: config/code signature, current stage/window and start time.
- `scheduler/<stage>/job-*/`: exact qsub argv, task manifest, wrapper, submission output, separate payload logs, and per-task exit records.
- `scheduler/columns/<timestamp>/`: retained partition products for that exact window; storage grows with window count and state size.
- `ensemble_runs/<member>/GC.log`: model output.
- `postprocess/bigy/`: upstream observation/model comparison products.

A filesystem lock excludes concurrent controllers. A started-task marker rejects PBS rerunning a payload in the same attempt, even though arrays must be submitted as rerunnable. Job failure, missing tasks or lost controller connection leave a non-ready stage. Restarting the command refuses to automatically replay it. Do not clear that state to force a retry: a partially completed ensemble update may already have modified only some members.

Reconcile the submitted PBS jobs (including queued tasks), inspect logs, and restore a consistent **whole-run** checkpoint in a separate run tree before retrying. This patch does not implement transactional rollback or produce automatic whole-run backups. Keep snapshots before important stages in production. The controller signature protects config/core/operator edits; it does not hash all forcing data, executables or model inputs. Keep those immutable throughout a run.

## Acceptance criteria

1. Real PBS single and throttled-array probes succeed; a failure, deletion or walltime kill prevents subsequent stages.
2. The real executable links against the intended runtime and the complete test data are present.
3. Five forecasts complete for every window, four LETKF partitions complete, and one combined update completes before the next forecast.
4. Three BigY timestamps are 20230102_0000, 20230103_0000 and 20230104_0000, with nonempty filtered observations.
5. All four perturbed members contain finite scaling factors and synchronized timestamps; control member 0 is not assimilated. Inspect nonzero spread and actual updates rather than accepting only process exit codes.
6. Snapshots render and their units, observation coverage and forecast/control comparisons are plausible. Do not demand monotonic fit improvement as a scheduler acceptance condition.
7. Every individual stage fits the applicable three/six-hour limit with an observed margin. Save qstat/accounting evidence for both success and injected failure.

Only after these pass should a longer, scientifically spun-up experiment be attempted.
