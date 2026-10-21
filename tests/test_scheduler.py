"""Scheduler tests use local stand-ins for qsub, sbatch and GEOS-Chem."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pbs_submit', ROOT/'core/pbs_submit.py')
pbs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbs)

QSUB = '''#!/usr/bin/env python3
import json,os,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if os.environ.get('REJECT'):sys.exit(9)
indices=[None]
if '-J' in args:
 lo,hi=map(int,args[args.index('-J')+1].split('-'));indices=list(range(lo,hi+1))
children=[]
for i in indices:
 if str(i)==os.environ.get('MISSING'):continue
 env=dict(os.environ,PBS_JOBID=Path(args[-1]).parent.name+'.mock')
 if i is not None:env['PBS_ARRAY_INDEX']=str(i)
 children.append(subprocess.Popen(['bash',args[-1]],cwd='/',env=env))
# Some PBS installations return success even when individual tasks fail.
for child in children:child.wait()
sys.exit(0)
'''

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.bin=self.base/'bin';self.bin.mkdir()
        self.calls=self.base/'calls'
        self.executable('qsub',QSUB)
        self.env=patch.dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],CALLS=str(self.calls))
        self.env.start()
        self.oldcwd=Path.cwd();os.chdir(self.base)
    def tearDown(self):
        os.chdir(self.oldcwd);self.env.stop();self.tmp.cleanup()
    def executable(self,name,contents):
        p=self.bin/name;p.write_text(contents);p.chmod(0o755)
    def payload(self,body='exit 0\n'):
        p=self.base/'job.run'
        p.write_text('#!/bin/bash\n#SBATCH -c 8\n#SBATCH -N 1\n#SBATCH --mem=32000\n#SBATCH -t 06:00:00\n'+body)
        return p
    def test_pbs_resources_queue_and_full_array(self):
        pbs.submit(self.payload(),(0,4),dict(PBSQueue='workq',PBSAccount='project'))
        args=json.loads(self.calls.read_text())
        self.assertIn('select=1:ncpus=8:mem=32000mb',args)
        self.assertEqual(args[args.index('-J')+1],'0-4')
        self.assertIn('block=true',args);self.assertIn('workq',args);self.assertIn('project',args)
    def test_single_member_index_and_submission_directory(self):
        pbs.submit(self.payload('test "$PBS_ARRAY_INDEX" = 3 && test "$PWD" = '+str(self.base)+'\n'),(3,3),{})
        self.assertNotIn('-J',json.loads(self.calls.read_text()))
    def test_missing_task_stops(self):
        with patch.dict(os.environ,MISSING='1'):
            with self.assertRaises(RuntimeError):pbs.submit(self.payload(),(0,1),{})
    def test_task_failure_stops_even_when_qsub_returns_zero(self):
        with self.assertRaises(RuntimeError):pbs.submit(self.payload('exit 7\n'),None,{})
    def test_submission_rejection_stops(self):
        with patch.dict(os.environ,REJECT='1'):
            with self.assertRaises(RuntimeError):pbs.submit(self.payload(),None,{})
    def test_native_resource_formats(self):
        self.assertEqual(pbs.walltime('0-12:00'),'12:00:00')
        self.assertEqual(pbs.walltime('2-03:04:05'),'51:04:05')
        self.assertEqual(pbs.walltime('90'),'01:30:00')
        self.assertEqual(pbs.walltime('10:30'),'00:10:30')
        p=self.payload();p.write_text(p.read_text().replace('32000','32G'))
        self.assertEqual(pbs.resources(p)[2],'32gb')
    def install(self,config):
        root=self.base/'run';core=root/'CHEEREIO/core';core.mkdir(parents=True)
        (root/'ensemble_runs/logs').mkdir(parents=True)
        (root/'scratch').mkdir()
        for p in (ROOT/'core').glob('*.sh'):shutil.copy(p,core/p.name)
        shutil.copy(ROOT/'core/pbs_submit.py',core/'pbs_submit.py')
        config.update(MY_PATH=str(self.base),RUN_NAME='run',EnvironmentFile='site.env',CondaEnv='test')
        (core.parent/'ens_config.json').write_text(json.dumps(config))
        (core.parent/'site.env').write_text('export CHEEREIO_PYTHON="'+shutil.which('python')+'"\n')
        return root,core
    def test_absent_scheduler_uses_native_sbatch_and_propagates_failure(self):
        root,core=self.install({})
        self.executable('sbatch','#!/bin/bash\nprintf "%s\\n" "$@" > "$CALLS"\nexit 7\n')
        p=root/'ensemble_runs/run_ens.sh'
        p.write_text((ROOT/'templates/run_ens.sh').read_text().replace('{START}','1').replace('{END}','2'))
        result=subprocess.run(['bash',str(p)],cwd=p.parent,capture_output=True)
        self.assertEqual(result.returncode,7)
        self.assertEqual(self.calls.read_text().splitlines(),['--array=1-2','-W','run_ensemble_simulations.sh'])
    def run_native(self,species,cycles,scheduler='PBS',first=0):
        c=dict(Scheduler=scheduler,sim_name=species,AssimilationCyclesPerJob=str(cycles),
               nEnsemble='2',DO_CONTROL_RUN='true' if first==0 else 'false',
               DO_CONTROL_WITHIN_ENSEMBLE_RUNS='true' if first==0 else 'false',
               DO_RUN_IN_PLACE='False',SIMPLE_SCALE_FOR_FIRST_ASSIM_PERIOD='false',
               DO_BURN_IN='false',SIMPLE_SCALE_AT_END_OF_BURN_IN_PERIOD='false',
               AMPLIFY_ENSEMBLE_SPREAD_FOR_FIRST_ASSIM_PERIOD='false',
               APPROXIMATE_VARON_RERUN='False',DO_VARON_RERUN='False')
        root,core=self.install(c);scratch=root/'scratch'
        (scratch/'IS_FIRST').touch()
        (scratch/'INPUT_GEOS_TEMP').write_text('20190102 000000\n20190103 000000\n')
        # Keep native shell ordering, restart checks, combination and cleanup.
        # Replace only the expensive model/analysis/date calculations.
        (core/'par_letkf.py').write_text('''import sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
(root/'scratch'/('member-'+sys.argv[2]+'.npy')).touch()
with (root/'analysis_calls').open('a') as f:f.write(' '.join(sys.argv[1:])+'\\n')
''')
        (core/'check_for_all_columns.py').write_text('''from pathlib import Path
s=Path(__file__).resolve().parents[2]/'scratch'
if len(list(s.glob('member-*.npy')))==2:(s/'ALL_COLUMNS_FOUND').touch()
''')
        (core/'combine_columns_and_update.py').write_text('''import sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
assert len(list((root/'scratch').glob('member-*.npy')))==2
with (root/'updates').open('a') as f:f.write(sys.argv[1]+'\\n')
''')
        (core/'change_histrst_durfreq.sh').write_text('exit 0\n')
        (core/'update_input_geos.sh').write_text('python advance_mock.py "$1"\n')
        (core/'advance_mock.py').write_text('''from pathlib import Path
import datetime as dt,sys
root=Path(__file__).resolve().parents[2];s=root/'scratch'
lines=(s/'INPUT_GEOS_TEMP').read_text().splitlines()
end=dt.datetime.strptime(lines[1],'%Y%m%d %H%M%S')
with (root/'tags').open('a') as f:f.write(sys.argv[1]+'\\n')
if end.day==5:(s/'ENSEMBLE_COMPLETE').touch()
(s/'INPUT_GEOS_TEMP').write_text(f'{end:%Y%m%d %H%M%S}\\n{end+dt.timedelta(days=1):%Y%m%d %H%M%S}\\n')
''')
        for i in range(first,3):
            member=root/'ensemble_runs'/f'run_{i:04d}';member.mkdir()
            gc=member/'gcclassic'
            gc.write_text('''#!/usr/bin/env python3
from pathlib import Path
import time
s=Path('../../scratch')
end=(s/'INPUT_GEOS_TEMP').read_text().splitlines()[1]
# Allow the native one-second file barriers to settle between mock forecasts.
time.sleep(5)
Path('GEOSChem.Restart.'+end[:-2].replace(' ','_')+'z.nc4').touch()
print('**************   E N D')
''');gc.chmod(0o755)
        for name in ['run_ens.sh','run_ensemble_simulations.sh']:
            s=(ROOT/'templates'/name).read_text()
            for key,value in dict(START=first,END=2,RunName='run',NumCores=1,Partition='',Memory=100,WallTime='00:05:00',MaxPar=1,ASSIM=core.parent).items():
                s=s.replace('{'+key+'}',str(value))
            (root/'ensemble_runs'/name).write_text(s)
        if scheduler=='SLURM':
            self.executable('srun','#!/bin/bash\nshift 2\nif [[ $1 == time ]]; then shift 2; fi\nexec "$@"\n')
            self.executable('sbatch',QSUB.replace("if '-J' in args:","if args[0].startswith('--array='):").replace("args[args.index('-J')+1]","args[0].split('=')[1]").replace("PBS_JOBID=Path(args[-1]).parent.name+'.mock'","SLURM_JOB_ID=str(len(Path(os.environ['CALLS']).read_text().splitlines())),SLURM_CPUS_PER_TASK='1'").replace("env['PBS_ARRAY_INDEX']","env['SLURM_ARRAY_TASK_ID']").replace("cwd='/'","cwd=os.getcwd()"))
        result=subprocess.run(['bash','run_ens.sh'],cwd=root/'ensemble_runs',capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((root/'updates').read_text().splitlines(),['20190103_0000','20190104_0000','20190105_0000'])
        self.assertEqual((root/'tags').read_text().splitlines(),['POSTFIRST','ASSIM','ASSIM'])
        self.assertEqual(len((root/'analysis_calls').read_text().splitlines()),6)
        self.assertEqual(len(self.calls.read_text().splitlines()),(3+cycles-1)//cycles if cycles else 1)
        self.assertTrue((scratch/'ENSEMBLE_COMPLETE').exists())
        self.assertFalse(list(scratch.glob('*.npy')))
    def test_co_native_cycles(self):self.run_native('CO',1)
    def test_two_cycles_per_job(self):self.run_native('CO',2)
    def test_nox_native_continuous(self):self.run_native('fullchem',0,first=1)
    def test_slurm_native_cycles(self):self.run_native('CH4',1,'SLURM')

if __name__=='__main__':unittest.main()
