"""Run with python -m unittest discover -s eccc -v (no real qsub)."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pbs', ROOT/'core/pbs_workflow.py')
pbs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbs)

MOCK = '''#!/usr/bin/env python3
import os,sys,json,subprocess
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f: f.write(json.dumps(a)+'\\n')
if os.environ.get('REJECT'): sys.exit(9)
indices=[0]
if '-J' in a:
 assert a[a.index('-r')+1]=='y'
 lo,hi=a[a.index('-J')+1].split('%')[0].split('-');indices=range(int(lo),int(hi)+1)
rc=0
for i in indices:
 if str(i)==os.environ.get('MISSING'): continue
 env=dict(os.environ,PBS_ARRAY_INDEX=str(i),PBS_JOBID=f'123[{i}].mock')
 code=subprocess.run(['bash',a[-1]],cwd='/',env=env).returncode
 rc=max(code,rc)
print('123[].mock')
sys.exit(rc)
'''

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.root=self.base/'run'; self.root.mkdir()
        self.core=self.root/'CHEEREIO/core';self.core.mkdir(parents=True)
        (self.core/'pbs_workflow.py').write_text('import sys,json\nfrom pathlib import Path\nt=json.loads(Path(sys.argv[2]).read_text())[int(sys.argv[3])]\nsys.exit(t.get("exit",0))\n')
        env=self.base/'env.sh';env.write_text(':\n')
        self.c=json.loads((ROOT/'ens_config_CH4_ECCC_2023.json').read_text())
        self.c['PBSEnvironment']=str(env)
        self.bin=self.base/'bin';self.bin.mkdir()
        (self.bin/'qsub').write_text(MOCK);(self.bin/'qsub').chmod(0o755)
        self.environ=patch.dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],CALLS=str(self.base/'calls'))
        self.environ.start()
    def tearDown(self):
        self.environ.stop();self.tmp.cleanup()
    def submit(self,tasks):
        pbs.submit(self.core,self.c,self.root,'analysis',tasks)
    def test_array_and_single_and_cwd(self):
        self.submit([{},{}]);self.submit([{}])
        calls=[json.loads(x) for x in (self.base/'calls').read_text().splitlines()]
        self.assertEqual(calls[0][calls[0].index('-J')+1],'0-1%4')
        self.assertNotIn('-J',calls[1]);self.assertNotIn('-q',calls[0])
        self.assertEqual(calls[0][calls[0].index('-W')+1],'block=true')
    def test_deleted_member_stops(self):
        with patch.dict(os.environ,MISSING='1'):
            with self.assertRaises(RuntimeError):self.submit([{},{}])
    def test_payload_failure_stops(self):
        with self.assertRaises(RuntimeError):self.submit([{},dict(exit=7)])
    def test_rejection_stops(self):
        with patch.dict(os.environ,REJECT='1'):
            with self.assertRaises(RuntimeError):self.submit([{}])
    def test_environment_failure_stops(self):
        Path(self.c['PBSEnvironment']).write_text('return 8\n')
        with self.assertRaises(RuntimeError):self.submit([{}])
    def test_no_automatic_task_reexecution(self):
        self.submit([{}])
        script=next((self.root/'scheduler').glob('*/job-*/job.sh'))
        self.assertNotEqual(subprocess.run(['bash',str(script)],capture_output=True).returncode,0)
    def test_config_gates(self):
        pbs.validate(self.c)
        for key,val in [('DO_ENS_SPINUP','true'),('APPROXIMATE_VARON_RERUN','True'),('nEnsemble','1')]:
            c=copy.deepcopy(self.c);c[key]=val
            with self.assertRaises(ValueError):pbs.validate(c)
        c=copy.deepcopy(self.c);c['PBSResources']['analysis']['memory']='32'
        with self.assertRaises(ValueError):pbs.validate(c)
    def test_three_window_order_and_completion(self):
        scratch=self.root/'scratch';scratch.mkdir()
        (scratch/'INPUT_GEOS_TEMP').write_text('20230101 000000\n20230102 000000\n')
        (scratch/'IS_FIRST').touch()
        self.c.update(MY_PATH=str(self.base),RUN_NAME='run')
        calls=[]
        def stage(core,c,root,kind,tasks):
            calls.append((kind,tasks))
            if kind=='update':
                start,end=pbs.window(root)
                import datetime as dt
                (scratch/'INPUT_GEOS_TEMP').write_text(f'{end:%Y%m%d %H%M%S}\n{end+dt.timedelta(days=1):%Y%m%d %H%M%S}\n')
                (scratch/'IS_FIRST').unlink(missing_ok=True)
                if end.strftime('%Y%m%d')=='20230104':(scratch/'ENSEMBLE_COMPLETE').touch()
        with patch.object(pbs,'context',return_value=(self.core,self.c,self.root)),patch.object(pbs,'provenance',return_value='test'),patch.object(pbs,'submit',side_effect=stage):
            pbs.control('run')
        self.assertEqual([k for k,t in calls],['forecast','analysis','update']*3)
        self.assertEqual([t[0]['first'] for k,t in calls if k=='analysis'],['true','false','false'])
        self.assertEqual(len(calls[0][1]),5)
        self.assertEqual(json.loads((self.root/'scheduler/state.json').read_text())['phase'],'complete')
    def test_interrupted_update_is_not_retried(self):
        d=self.root/'scheduler';d.mkdir()
        (d/'state.json').write_text(json.dumps(dict(signature='test',phase='update')))
        with patch.object(pbs,'context',return_value=(self.core,self.c,self.root)),patch.object(pbs,'provenance',return_value='test'),patch.object(pbs,'submit') as submit:
            with self.assertRaises(RuntimeError):pbs.control('run')
            submit.assert_not_called()
    def test_stale_columns_stop_before_submission(self):
        scratch=self.root/'scratch';scratch.mkdir()
        (scratch/'INPUT_GEOS_TEMP').write_text('20230101 000000\n20230102 000000\n')
        (scratch/'old.npy').touch()
        with patch.object(pbs,'context',return_value=(self.core,self.c,self.root)),patch.object(pbs,'provenance',return_value='test'),patch.object(pbs,'submit') as submit:
            with self.assertRaises(RuntimeError):pbs.control('run')
            submit.assert_not_called()


class NetCDFTests(unittest.TestCase):
    def test_restart_timestamp_and_contents(self):
        import numpy as np
        import xarray as xr
        import datetime as dt
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'restart.nc4'
            ds=xr.Dataset({'SpeciesRst_CH4':(('time','lev','lat','lon'),np.ones((1,1,1,1))*1.8e-6)},coords={'time':[np.datetime64('2023-01-02')],'lev':[1],'lat':[0],'lon':[0]})
            ds.to_netcdf(path)
            pbs.verify_restart(path,dt.datetime(2023,1,2))
            with self.assertRaises(ValueError):pbs.verify_restart(path,dt.datetime(2023,1,3))
            ds['SpeciesRst_CH4'][:]=np.nan;ds.to_netcdf(path)
            with self.assertRaises(ValueError):pbs.verify_restart(path,dt.datetime(2023,1,2))
    def test_diagnostic_holes_are_rejected(self):
        import numpy as np
        import xarray as xr
        import datetime as dt
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory);(run/'OutputDir').mkdir()
            for col,var in [('SpeciesConc','SpeciesConcVV_CH4'),('LevelEdgeDiags','Met_PEDGE')]:
                ds=xr.Dataset({var:(('time',),np.ones(24))},coords={'time':np.arange('2023-01-01','2023-01-02',dtype='datetime64[h]')})
                ds.to_netcdf(run/'OutputDir'/f'GEOSChem.{col}.20230101.nc4')
            pbs.verify_diagnostics(run,dt.datetime(2023,1,1),dt.datetime(2023,1,2))
            ds.isel(time=slice(0,23)).to_netcdf(run/'OutputDir'/f'GEOSChem.{col}.20230101.nc4')
            with self.assertRaises(ValueError):pbs.verify_diagnostics(run,dt.datetime(2023,1,1),dt.datetime(2023,1,2))
    def test_real_blended_reader(self):
        import numpy as np
        import xarray as xr
        sys.path.insert(0,str(ROOT/'core'))
        import tropomi_tools as tt
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'blended.nc'
            ds=xr.Dataset()
            for name,value in dict(qa_value=1.,surface_classification=0,pressure_interval=8000.,surface_pressure=100000.,surface_altitude_precision=10.,methane_mixing_ratio_blended=1900.,methane_mixing_ratio_precision=12.,longitude=0.,latitude=0.,surface_albedo_SWIR=.2,surface_albedo_NIR=.2).items():
                ds[name]=('obs',[value])
            ds['time_utc']=('obs',['2023-01-01T12:00:00'])
            for name in ['dry_air_subcolumns','methane_profile_apriori','column_averaging_kernel']:
                ds[name]=( ('obs','layer'),np.ones((1,12)))
            ds.to_netcdf(path)
            obs=tt.read_tropomi_gosat_corrected(path,'CH4',includeObsError=True)
            np.testing.assert_allclose(obs['CH4'],[1900.])
            np.testing.assert_allclose(obs['pressures'],[np.arange(1000.,39.,-80.)])

class ColumnIsolationTests(unittest.TestCase):
    def test_previous_window_columns_cannot_count_as_current(self):
        import runpy
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'run';scratch=root/'scratch';scratch.mkdir(parents=True)
            columns=root/'scheduler/columns/20230103_0000';columns.mkdir(parents=True)
            for i in range(10):(scratch/f'old-{i}.npy').touch()
            for i in range(9):(columns/f'current-{i}.npy').touch()
            si=SimpleNamespace(getSpeciesConfig=lambda:dict(MY_PATH=directory,RUN_NAME='run'),getLatLonVals=lambda c:([1,2,3],[1,2,3]))
            with patch.dict(sys.modules,settings_interface=si),patch.dict(os.environ,CHEEREIO_COLUMN_ROOT=str(columns)):
                runpy.run_path(str(ROOT/'core/check_for_all_columns.py'))
            self.assertTrue((scratch/'ALL_COLUMNS_FOUND').exists())
            (scratch/'ALL_COLUMNS_FOUND').unlink();(columns/'current-8.npy').unlink()
            with patch.dict(sys.modules,settings_interface=si),patch.dict(os.environ,CHEEREIO_COLUMN_ROOT=str(columns)):
                runpy.run_path(str(ROOT/'core/check_for_all_columns.py'))
            self.assertFalse((scratch/'ALL_COLUMNS_FOUND').exists())

if __name__=='__main__':unittest.main()
