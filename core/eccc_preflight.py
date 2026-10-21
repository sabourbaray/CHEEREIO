#!/usr/bin/env python3
"""Validate ECCC smoke-test inputs before setup; execute from CHEEREIO/core."""
import datetime as dt
import importlib
import json
from pathlib import Path
import shutil
import numpy as np
import xarray as xr
import settings_interface as si
from pbs_workflow import validate, verify_restart

c = si.getSpeciesConfig()
validate(c)
if c['GC_VERSION'] != '14.1.1' or c['RES'] != '4.0x5.0' or c['NEST'] != 'F' or c['sim_name'] != 'CH4':
    raise ValueError('This preflight certifies the supplied 14.1.1 global 4x5 CH4 smoke configuration only')
if json.loads(Path('../settings_to_override.json').read_text())['override'] != 'False':
    raise ValueError('Disable testing overrides before deployment')
for name in ('Assimilator','tropomi_tools','netCDF4','geopy','scipy','matplotlib','mpl_toolkits.basemap'):
    importlib.import_module(name)
si.importObsTranslators()
for name in ('jq','qsub','ffmpeg','cmake','make'):
    if not shutil.which(name):
        raise ValueError('Missing executable: '+name)
if not Path(c['DATA_PATH']).is_dir():
    raise ValueError('Missing ExtData directory')
restart = Path(c['RESTART_FILE'])
verify_restart(restart, dt.datetime.strptime(c['START_DATE'], '%Y%m%d'))
with xr.open_dataset(restart) as ds:
    expected_lat = np.r_[-89., np.arange(-86., 90., 4.), 89.]
    if ds.sizes.get('lev') != 47 or not np.array_equal(ds.lat, expected_lat) or not np.array_equal(ds.lon, np.arange(-180.,180.,5.)):
        raise ValueError('Restart must already be on the 47-level global 4x5 GEOS-Chem grid; prep_par uses this grid directly')
    for v in ('Met_PS1DRY','Met_TMPU1','Met_BXHEIGHT','Met_TropLev'):
        if v not in ds:
            raise ValueError('Missing restart field: '+v)
files = sorted(Path(c['TROPOMI_dirs']['CH4']).rglob('S5P_*.nc'))
# Match the production operator's orbit selection, including its strict end bound.
orbits = [(p, dt.datetime.strptime(p.name.split('_')[-6], '%Y%m%dT%H%M%S'),
           dt.datetime.strptime(p.name.split('_')[-5], '%Y%m%dT%H%M%S')) for p in files]
from tropomi_tools import read_tropomi_gosat_corrected
for day in range((dt.datetime.strptime(c['END_DATE'],'%Y%m%d')-dt.datetime.strptime(c['START_DATE'],'%Y%m%d')).days):
    date = dt.datetime.strptime(c['START_DATE'],'%Y%m%d')+dt.timedelta(days=day)
    candidates = [p for p, start, end in orbits if start >= date and end < date+dt.timedelta(days=1)]
    if not candidates:
        raise ValueError('No selectable TROPOMI orbits for '+str(date.date()))
    # An ocean-only first orbit is normal; look for a usable orbit for this day.
    for p in candidates:
        obs = read_tropomi_gosat_corrected(str(p), 'CH4', filterinfo={
            'MAIN': [float(c['filter_obs_poleward_of_n_degrees']['CH4_TROPOMI'])]})
        if np.isfinite(obs['CH4']).any():
            break
    else:
        raise ValueError('No finite land CH4 in selectable orbits for '+str(date.date()))
print('Imports, input restart grid/time/fields, and sample blended TROPOMI files passed.')
print('GEOS-Chem dry run and measured PBS timings are still required.')
