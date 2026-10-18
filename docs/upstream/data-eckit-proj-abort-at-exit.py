# /// script
# dependencies = ["earthkit-data==1.3.0", "pyproj==3.8.0"]
# ///
"""With earthkit-data 1.3.0 the process aborts at interpreter exit once pyproj is imported after a
GRIB field has been read: eckitlib and pyproj each bundle their own libproj, sqlite3 and curl, and
glibc stops on a bad free() while the process shuts down. The work and its output are complete.

The work runs in a child process so that this script can report the child's exit status."""

import subprocess
import sys

if len(sys.argv) > 1:  # child: read a field, then import pyproj (as cartopy does)
    import earthkit.data as ekd

    field = ekd.from_source("sample", "test.grib").to_fieldlist()[0]
    print("values:", field.to_numpy().shape, flush=True)

    import pyproj

    print("crs:", pyproj.CRS("EPSG:4326").name, flush=True)
    sys.exit(0)

run = subprocess.run([sys.executable, __file__, "child"], capture_output=True, text=True)
print(run.stdout, end="")
status = run.returncode if run.returncode >= 0 else 128 - run.returncode  # shell's 134 for SIGABRT
finished = "crs: WGS 84" in run.stdout  # all the work was done before the abort
if finished and status != 0:
    message = [line for line in run.stderr.splitlines() if line.strip()][-1:]
    print("REPRODUCED: exit status", status, *message)
else:
    print("NOT REPRODUCED: exit status", status)
