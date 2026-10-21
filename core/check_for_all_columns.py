import os
from glob import glob
import settings_interface as si 

data = si.getSpeciesConfig()
latgrid,longrid = si.getLatLonVals(data)

path_to_scratch = f"{data['MY_PATH']}/{data['RUN_NAME']}/scratch"
column_root = os.environ.get('CHEEREIO_COLUMN_ROOT', path_to_scratch)
columns = glob(f'{column_root}/**/*.npy',recursive=True)
numcols = len(columns)
num_cells = len(latgrid)*len(longrid)

if numcols==num_cells:
	with open(f"{path_to_scratch}/ALL_COLUMNS_FOUND", "w") as f:
		f.write("All columns found.\n") #If so, save flag file to ensemble folder
