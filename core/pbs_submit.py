#!/usr/bin/env python3

# Submit the native CHEEREIO job scripts with PBS Pro instead of Slurm.
# This wrapper handles resources and job completion, not assimilation steps.

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def walltime(value):
    # Slurm accepts days-hours:minutes as well as hours:minutes:seconds.
    days, sep, clock = value.partition('-')
    if not sep:
        days, clock = '0', days
    parts = [int(v) for v in clock.split(':')]
    if sep:
        hours, minutes, seconds = (parts + [0, 0])[:3]
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = 0, *parts
    else:
        hours, minutes, seconds = 0, parts[0], 0
    seconds += (int(days)*24 + hours)*3600 + minutes*60
    if seconds <= 0:
        raise ValueError('Walltime must be positive')
    return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'


def resources(script):
    # Read the same directives used by sbatch. Numeric Slurm memory is in MB.
    options = {}
    names = {'-J': 'name', '--job-name': 'name', '-c': 'cpus',
             '--cpus-per-task': 'cpus', '-N': 'nodes', '--nodes': 'nodes',
             '-n': 'tasks', '--ntasks': 'tasks', '-p': 'partition',
             '--partition': 'partition', '--mem': 'memory', '-t': 'walltime',
             '--time': 'walltime', '-o': 'output', '--output': 'output',
             '-e': 'error', '--error': 'error'}
    for line in script.read_text().splitlines():
        if not line.startswith('#SBATCH'):
            continue
        tokens = shlex.split(line[len('#SBATCH'):], comments=True)
        if not tokens:
            continue
        flag, eq, value = tokens[0].partition('=')
        if flag not in names:
            raise ValueError(f'Unsupported PBS translation of {flag} in {script}')
        if not eq:
            value = tokens[1] if len(tokens) == 2 else ''
        options[names[flag]] = value
    if int(options.get('nodes', 1)) != 1 or int(options.get('tasks', 1)) != 1:
        raise ValueError('GEOS-Chem Classic jobs require one node and one task')
    cpus = int(options.get('cpus', 1))
    memory = options['memory'].lower()
    match = re.fullmatch(r'([1-9][0-9]*)([kmgt]?)(?:b)?', memory)
    if cpus < 1 or not match:
        raise ValueError('Positive CPU count and memory are required')
    memory = match[1] + (match[2] or 'm') + 'b'
    return options, cpus, memory, walltime(options['walltime'])


def submit(script, indices, config):
    script = Path(script).resolve()
    options, cpus, memory, limit = resources(script)
    cwd = Path.cwd()
    logs = cwd / 'logs'
    logs.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='pbs-', dir=logs))
    job = directory / 'job.sh'
    array = indices is not None
    first, last = indices if array else (0, 0)
    # Keep per-task exit records because a missing/deleted array task must not
    # be treated as a completed cycle. These are job records, not model checks.
    job.write_text('\n'.join([
        '#!/bin/bash',
        'cd ' + shlex.quote(str(cwd)) + ' || exit 1',
        f'task=${{PBS_ARRAY_INDEX:-{first}}}',
        'directory=' + shlex.quote(str(directory)),
        'mkdir "$directory/$task.started" || exit 1',
        'exec > "$directory/task-$task.out" 2> "$directory/task-$task.err"',
        'finish() { rc=$?; trap - EXIT; printf "%s\\n" "$rc" > "$directory/$task.status"; exit "$rc"; }',
        'trap finish EXIT',
        *(['export PBS_ARRAY_INDEX=$task'] if array else []),
        'bash ' + shlex.quote(str(script)), 'exit $?', '']))
    args = ['qsub', '-S', '/bin/bash', '-V', '-W', 'block=true',
            '-N', options.get('name') or script.stem,
            '-l', f'select=1:ncpus={cpus}:mem={memory}',
            '-l', 'walltime=' + limit, '-j', 'oe', '-o', str(directory) + '/']
    for flag, key in [('-q', 'PBSQueue'), ('-A', 'PBSAccount')]:
        if config.get(key):
            args += [flag, str(config[key])]
    if array and first != last:
        args += ['-r', 'y', '-J', f'{first}-{last}']
    else:
        args += ['-r', 'n']
    args.append(str(job))
    print(f'Submitting {script.name}; job logs: {directory}', flush=True)
    with (directory / 'submission.log').open('w') as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
    failed = [i for i in range(first, last+1)
              if not (directory / f'{i}.status').is_file()
              or (directory / f'{i}.status').read_text().strip() != '0']
    if result.returncode or failed:
        raise RuntimeError(f'PBS job failed: qsub={result.returncode}, failed/missing tasks={failed}; {directory}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Submit a native CHEEREIO script with PBS Pro')
    parser.add_argument('-W', action='store_true', help='Wait for job completion')
    parser.add_argument('--array', help='Inclusive member range, e.g. 0-4')
    parser.add_argument('script')
    args = parser.parse_args()
    if not args.W:
        parser.error('This wrapper requires -W to wait for completion')
    indices = None
    if args.array:
        if not re.fullmatch(r'[0-9]+-[0-9]+', args.array):
            parser.error('Use a complete member range without a concurrency cap')
        indices = tuple(int(i) for i in args.array.split('-'))
        if indices[0] > indices[1]:
            parser.error('Array start must not exceed its end')
    with (Path(__file__).resolve().parent.parent / 'ens_config.json').open() as f:
        config = json.load(f)
    submit(args.script, indices, config)
