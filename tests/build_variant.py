"""Compile the alternate current-control/manual-table configuration; never flash."""
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'programmer'))
from backend import Programmer
from config import defaults

values = defaults()
values.update(CURRENT_CONTROL=True, SYNCHRONOUS_SWITCHING=True,
              IDENTIFY_HALLS_ON_BOOT=False, HALL_TABLE=[255, 2, 0, 1, 4, 3, 5, 255])
p = Programmer()
p.start(values)
while p.status()['status'] == 'running': time.sleep(0.2)
result = p.status()
print(result['stage'])
print(result['artifact'])
if result['status'] != 'complete':
    print(result['log'])
    sys.exit(1)
