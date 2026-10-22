"""Read an archive directly over SSH; never buffer the SD backup in board RAM."""
from pathlib import Path
import tarfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from board_ssh import remote

result = remote("printf '%s\\n' './swapfile' > /tmp/mira-sd-exclude; tar -X /tmp/mira-sd-exclude -czf - -C /mnt/mmcblk0p1 .", timeout=240)
if result.returncode:
    raise RuntimeError(result.stderr.decode(errors="replace"))
target = Path("diagnostics/private/mira-sd-before-repair.tar.gz")
target.write_bytes(result.stdout)
with tarfile.open(target) as archive:
    entries = archive.getmembers()
    assert not any(m.name == "./swapfile" for m in entries)
    print("Verified SD backup:",len(entries),"entries;",target.stat().st_size,"bytes")
