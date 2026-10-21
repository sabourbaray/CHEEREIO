#!/usr/bin/env python3
"""PBS Pro stage orchestration; scientific operations remain in CHEEREIO core.

Run from the deployed CHEEREIO copy. A stage left in flight is deliberately
not retried: reconcile PBS and scientific files before restoring a checkpoint.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time


def write_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def truth(value):
    return str(value).lower() == 'true'


def validate(c):
    if c.get('Scheduler') != 'PBS':
        raise ValueError('This driver requires Scheduler= PBS')
    if c['sim_name'] != 'CH4' or not c['GC_VERSION'].startswith('14.'):
        raise ValueError('PBS restart validation currently supports GCClassic 14 CH4')
    for key in ('DO_SPINUP', 'DO_ENS_SPINUP'):
        if truth(c[key]):
            raise ValueError(key + ': prepare spun-up restart inputs separately; this driver runs assimilation windows')
    if truth(c['DO_CONTROL_RUN']) and not truth(c['DO_CONTROL_WITHIN_ENSEMBLE_RUNS']):
        raise ValueError('PBS control must run within ensemble_runs')
    # Overlapping forecasts and special updates need dedicated checkpoint logic.
    # Reject them rather than advertise untested upstream modes as PBS support.
    for key in ('DO_RUN_IN_PLACE', 'DO_VARON_RERUN', 'APPROXIMATE_VARON_RERUN',
                'DO_BURN_IN', 'SIMPLE_SCALE_FOR_FIRST_ASSIM_PERIOD',
                'AMPLIFY_ENSEMBLE_SPREAD_FOR_FIRST_ASSIM_PERIOD'):
        if truth(c.get(key, False)):
            raise ValueError(key + ': PBS currently supports ordinary nonoverlapping LETKF windows only')
    if int(c['nEnsemble']) < 2 or int(c['MaxPar']) < 1:
        raise ValueError('Need at least two members and MaxPar >= 1')
    for kind in ('forecast', 'analysis', 'update', 'plot'):
        r = c['PBSResources'][kind]
        if int(r['cpus']) < 1 or not re.fullmatch(r'[1-9][0-9]*(?:mb|gb)', r['memory']):
            raise ValueError('Positive CPUs and explicit mb/gb memory required')
        if not re.fullmatch(r'[0-9]+:[0-5][0-9]:[0-5][0-9]', r['walltime']):
            raise ValueError('PBS walltime must be HHH:MM:SS')
        if sum(int(v)*m for v,m in zip(r['walltime'].split(':'),(3600,60,1))) <= 0:
            raise ValueError('Walltime must be positive')
    if int(c['PBSResources']['analysis']['cpus']) < int(c['MaxPar']):
        raise ValueError('Analysis CPUs must cover MaxPar')
    if int(c.get('PBSMaxConcurrent', 0)) < 0:
        raise ValueError('PBSMaxConcurrent is zero (uncapped) or positive')
    start, first, end = [dt.datetime.strptime(c[k], '%Y%m%d') for k in
                         ('START_DATE', 'ASSIM_START_DATE', 'END_DATE')]
    if not start < first <= end or int(c['ASSIM_TIME']) <= 0:
        raise ValueError('Require START_DATE < ASSIM_START_DATE <= END_DATE and positive ASSIM_TIME')
    if not truth(c['DO_RUN_IN_PLACE']) and not truth(c['DO_VARON_RERUN']):
        if (end-first).total_seconds() % (3600*int(c['ASSIM_TIME'])):
            raise ValueError('END_DATE must align with complete assimilation windows')


def context():
    core = Path(__file__).resolve().parent
    os.chdir(core)
    import settings_interface as si
    c = si.getSpeciesConfig()
    validate(c)
    root = Path(c['MY_PATH']) / c['RUN_NAME']
    if core.parent != (root / 'CHEEREIO').resolve():
        raise ValueError('Use the deployed run/CHEEREIO/core/pbs_workflow.py')
    return core, c, root


def checked(command, cwd=None, **kwargs):
    subprocess.run([str(v) for v in command], cwd=cwd, check=True, **kwargs)


def submit(core, c, root, kind, tasks):
    """Blocking PBS plus all expected completion records, including deleted tasks."""
    directory = root / 'scheduler' / kind
    directory.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix='job-', dir=directory))
    manifest = attempt / 'tasks.json'
    write_json(manifest, tasks)
    r = c['PBSResources'][kind]
    script = attempt / 'job.sh'
    envfile = Path(c['PBSEnvironment']).expanduser()
    if not envfile.is_absolute():
        envfile = core.parent / envfile
    script.write_text('\n'.join([
        '#!/bin/bash', 'set -eE -o pipefail',
        'cd ' + shlex.quote(str(attempt)),
        'task=${PBS_ARRAY_INDEX:-0}',
        '[[ $task =~ ^[0-9]+$ ]] || exit 2',
        'mkdir "${task}.started" || exit 2',
        'exec > "task-${task}.log" 2>&1',
        'finish() { rc=$?; trap - EXIT; printf "%s\\n" "$rc" > "${task}.status.tmp"; mv "${task}.status.tmp" "${task}.status"; exit "$rc"; }',
        'trap finish EXIT',
        'export CHEEREIO_ENV_NAME=' + shlex.quote(c['CondaEnv']),
        'source ' + shlex.quote(str(envfile)),
        'export OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1',
        'export OMP_NUM_THREADS=' + str(r['cpus'] if kind == 'forecast' else 1),
        'export MPLBACKEND=Agg CHEEREIO_PBS_STAGE=true',
        'python ' + shlex.quote(str(core / 'pbs_workflow.py')) +
        ' worker ' + shlex.quote(str(manifest)) + ' "$task"', '']))
    args = ['qsub', '-S', '/bin/bash', '-V', '-W', 'block=true', '-N', 'ch_' + kind,
            '-l', f"select=1:ncpus={r['cpus']}:mem={r['memory']}",
            '-l', 'walltime=' + r['walltime'], '-j', 'oe', '-o', str(attempt) + '/']
    for flag, key in (('-q', 'PBSQueue'), ('-A', 'PBSAccount')):
        if c.get(key):
            args += [flag, c[key]]
    if len(tasks) > 1:
        cap = int(c.get('PBSMaxConcurrent', 0))
        indices = f'0-{len(tasks)-1}' + (f'%{cap}' if cap else '')
        args += ['-r', 'y', '-J', indices]
    else:
        args += ['-r', 'n']
    args.append(str(script))
    write_json(attempt / 'request.json', args)
    print(f'{kind}: {len(tasks)} task(s); evidence: {attempt}', flush=True)
    with (attempt / 'submission.log').open('w') as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
    failures = [i for i in range(len(tasks)) if not (attempt / f'{i}.status').is_file()
                or (attempt / f'{i}.status').read_text().strip() != '0']
    if result.returncode or failures:
        raise RuntimeError(f'PBS {kind} failed: qsub={result.returncode}, incomplete/failed tasks={failures}; {attempt}')


def window(root):
    lines = (root / 'scratch/INPUT_GEOS_TEMP').read_text().splitlines()
    if len(lines) != 2:
        raise ValueError('Expected two window timestamps')
    return [dt.datetime.strptime(s, '%Y%m%d %H%M%S') for s in lines]


def members(c, root):
    first = 0 if truth(c['DO_CONTROL_RUN']) and truth(c['DO_CONTROL_WITHIN_ENSEMBLE_RUNS']) else 1
    return [root / 'ensemble_runs' / f"{c['RUN_NAME']}_{i:04d}"
            for i in range(first, int(c['nEnsemble'])+1)]


def verify_restart(path, timestamp):
    import numpy as np
    import xarray as xr
    with xr.open_dataset(path) as ds:
        if 'SpeciesRst_CH4' not in ds or 'time' not in ds:
            raise ValueError(f'Missing CH4 or time in {path}')
        if ds.sizes['time'] != 1 or np.datetime64(ds.time.values[0], 's') != np.datetime64(timestamp, 's'):
            raise ValueError(f'Restart time mismatch: {path}')
        values = ds.SpeciesRst_CH4.values
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError(f'Invalid CH4 restart values: {path}')


def verify_diagnostics(run, start, end):
    """Require every hourly record used by the CH4 satellite operator."""
    import numpy as np
    import xarray as xr
    for collection, variable in (('SpeciesConc', 'SpeciesConcVV_CH4'),
                                 ('LevelEdgeDiags', 'Met_PEDGE')):
        times = []
        for path in sorted((run / 'OutputDir').glob(f'GEOSChem.{collection}.*.nc4')):
            with xr.open_dataset(path) as ds:
                if variable not in ds:
                    raise ValueError(f'Missing {variable}: {path}')
                selected = ds.sel(time=slice(np.datetime64(start), np.datetime64(end)-np.timedelta64(1,'s')))
                if selected.sizes.get('time', 0):
                    if not np.isfinite(selected[variable].values).all():
                        raise ValueError(f'Nonfinite diagnostic: {path}')
                    times.extend(selected.time.values.astype('datetime64[s]'))
        times = np.sort(np.asarray(times, dtype='datetime64[s]'))
        expected = int((end-start).total_seconds()/3600)
        if len(times) != expected or (len(times)>1 and not np.all(np.diff(times)==np.timedelta64(1,'h'))):
            raise ValueError(f'Missing/duplicate/nonhourly {collection} records in {run}')


def worker(manifest, index):
    core, c, root = context()
    task = json.loads(Path(manifest).read_text())[int(index)]
    kind = task['kind']
    scratch = root / 'scratch'
    if kind in ('analysis', 'update'):
        os.environ['CHEEREIO_COLUMN_ROOT'] = str(root / 'scheduler/columns' / task['stamp'])
    if (scratch / 'KILL_ENS').exists():
        raise RuntimeError('KILL_ENS exists')
    if kind == 'forecast':
        run = Path(task['run'])
        end = window(root)[1]
        restart = run / f"GEOSChem.Restart.{end:%Y%m%d_%H%M}z.nc4"
        # A fresh stage must not mistake an old endpoint for new success.
        if restart.exists():
            raise RuntimeError(f'Endpoint already exists before forecast: {restart}')
        with (run / 'GC.log').open('a') as log:
            checked(['./gcclassic'], cwd=run, stdout=log, stderr=subprocess.STDOUT)
        verify_restart(restart, end)
        verify_diagnostics(run, *window(root))
    elif kind == 'analysis':
        # Each upstream (member,core) assignment covers disjoint columns but
        # reads the whole ensemble. No member is updated until all tasks exit.
        processes = []
        for slot in range(1, int(c['MaxPar'])+1):
            args = [sys.executable, '-u', 'par_letkf.py', task['stamp'],
                    str(task['member']), str(slot), task['first'], task['simple'], task['amplify']]
            processes.append(subprocess.Popen(args, cwd=core))
        codes = [p.wait() for p in processes]
        if any(codes):
            raise RuntimeError(f'LETKF process failures: {codes}')
    elif kind == 'update':
        if task['simple'] == 'false':
            checked([sys.executable, 'check_for_all_columns.py'], cwd=core)
            if not (scratch / 'ALL_COLUMNS_FOUND').is_file():
                raise RuntimeError('Missing expected LETKF columns')
            checked([sys.executable, '-u', 'combine_columns_and_update.py', task['stamp']], cwd=core)
        elif not (scratch / 'ASSIMILATION_COMPLETE').is_file():
            raise RuntimeError('Simple scaling did not finish')
        # Invoke the upstream window and HISTORY logic in its original order.
        if task['first'] == 'true':
            checked(['bash', '-e', 'change_histrst_durfreq.sh',
                     'BURNIN_DURFREQ' if truth(c['DO_BURN_IN']) else 'UPDATEDURFREQ'], cwd=core)
        if task['burn']:
            checked(['bash', '-e', 'change_histrst_durfreq.sh', 'UPDATEDURFREQ'], cwd=core)
        tag = 'POSTFIRST' if task['first'] == 'true' else ('POSTBURN' if task['burn'] else 'ASSIM')
        checked(['bash', '-e', 'update_input_geos.sh', tag], cwd=core)
        if (scratch / 'KILL_ENS').exists():
            raise RuntimeError('Window advance failed')
        for name in ('ALL_RUNS_COMPLETE', 'ALL_COLUMNS_FOUND', 'ASSIMILATION_COMPLETE', 'IS_FIRST'):
            (scratch / name).unlink(missing_ok=True)
        for path in list(scratch.glob('**/*.npy')):
            path.unlink()
        if list(scratch.glob('**/*.npy')):
            raise RuntimeError('Column cleanup incomplete')
    elif kind == 'plot':
        checked([sys.executable, 'snapshot_pt1.py'], cwd=core.parent / 'postprocess')
        checked([sys.executable, 'snapshot_pt2.py'], cwd=core.parent / 'postprocess')
    else:
        raise ValueError('Unknown worker kind')


def provenance(core):
    digest = hashlib.sha256()
    paths = [core.parent / 'ens_config.json', core.parent / 'settings_to_override.json',
             core.parent / 'operators.json']
    paths += sorted(core.glob('*.py')) + sorted(core.glob('*.sh'))
    paths += sorted((core.parent / 'extensions').glob('*.json'))
    paths += sorted((core.parent / 'postprocess').glob('*.py'))
    paths += sorted((core.parent / 'environments').glob('*.env'))
    for path in paths:
        digest.update(str(path.relative_to(core.parent)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def control(action):
    core, c, root = context()
    scheduler = root / 'scheduler'
    scheduler.mkdir(exist_ok=True)
    with (scheduler / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        statepath = scheduler / 'state.json'
        signature = provenance(core)
        state = json.loads(statepath.read_text()) if statepath.exists() else dict(phase='ready', signature=signature)
        if state['signature'] != signature:
            raise ValueError('Code/config changed since workflow started')
        if state['phase'] not in ('ready', 'complete'):
            raise RuntimeError('Unfinished stage: reconcile PBS and restore a consistent run checkpoint; no automatic retry')
        if action == 'plot':
            if state['phase'] != 'complete':
                raise RuntimeError('Complete the assimilation before plotting')
            submit(core, c, root, 'plot', [dict(kind='plot')])
            return
        scratch = root / 'scratch'
        while not (scratch / 'ENSEMBLE_COMPLETE').exists():
            if (scratch / 'KILL_ENS').exists():
                raise RuntimeError('KILL_ENS exists')
            start, end = window(root)
            if end > dt.datetime.strptime(c['END_DATE'], '%Y%m%d') or end <= start:
                raise ValueError('Invalid or overshooting assimilation window')
            for flag in ('ALL_COLUMNS_FOUND', 'ASSIMILATION_COMPLETE', 'ALL_RUNS_COMPLETE'):
                if (scratch / flag).exists():
                    raise RuntimeError(f'Stale flag at window start: {flag}')
            if list(scratch.glob('**/*.npy')):
                raise RuntimeError('Stale column products at window start')
            first = (scratch / 'IS_FIRST').exists()
            burn = truth(c['DO_BURN_IN']) and (scratch / 'BURN_IN_PERIOD_PROCESSED').exists() and not (scratch / 'BURN_IN_SCALING_COMPLETE').exists()
            simple = (first and truth(c['SIMPLE_SCALE_FOR_FIRST_ASSIM_PERIOD'])) or (burn and truth(c['SIMPLE_SCALE_AT_END_OF_BURN_IN_PERIOD']))
            common = dict(stamp=end.strftime('%Y%m%d_%H%M'), first=str(first).lower(),
                          simple=str(simple).lower(), burn=burn,
                          amplify=str(first and truth(c['AMPLIFY_ENSEMBLE_SPREAD_FOR_FIRST_ASSIM_PERIOD'])).lower())
            stages = [('forecast', [dict(kind='forecast', run=str(p)) for p in members(c, root)]),
                      ('analysis', [dict(common, kind='analysis', member=i) for i in range(1, int(c['nEnsemble'])+1)]),
                      ('update', [dict(common, kind='update')])]
            for kind, tasks in stages:
                if kind == 'analysis' and (root / 'scheduler/columns' / common['stamp']).exists():
                    raise RuntimeError('Analysis column directory already exists for this timestamp')
                state.update(phase=kind, window=[start.isoformat(), end.isoformat()], started=time.time())
                write_json(statepath, state)
                submit(core, c, root, kind, tasks)
            state.update(phase='ready')
            write_json(statepath, state)
        state.update(phase='complete')
        write_json(statepath, state)
        print('All assimilation windows complete.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'plot', 'worker'])
    parser.add_argument('worker_args', nargs='*')
    args = parser.parse_args()
    if args.action == 'worker':
        worker(*args.worker_args)
    else:
        control(args.action)
