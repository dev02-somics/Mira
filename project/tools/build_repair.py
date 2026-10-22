"""Build the board-specific maintenance package from verified private backups."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "diagnostics/private"


def main():
    old = {}
    for name in ("mira-before-repair-20261008.tar.gz", "mira-extra-backup.tar.gz"):
        with tarfile.open(PRIVATE / name) as archive:
            old.update({"/" + m.name: archive.extractfile(m).read() for m in archive if m.isfile()})
    files = {}
    for package, target in (("mira_player", "/root/mira-player"), ("mira_wifi", "/root/mira-wifi")):
        for p in (ROOT / "src" / package).glob("*.py"):
            files[target + "/" + package + "/" + p.name] = p.read_bytes().replace(b"\r\n", b"\n")
    for source, target in (("deploy/start.sh", "/root/mira-player/start.sh"),
                           ("deploy/mira_health.py", "/root/mira-health.py"),
                           ("deploy/90-mira-rtc", "/etc/hotplug.d/ntp/90-mira-rtc")):
        files[target] = (ROOT / source).read_bytes().replace(b"\r\n", b"\n")
    monitor = old["/usr/bin/mgwp_monitor"].decode()
    assert "curl -s" in monitor and "# Check if the microsd is mount as RO" in monitor
    monitor = monitor[:monitor.index("# Check if the microsd is mount as RO")]
    guards = '''[ -e /tmp/mira-maintenance ] && exit 0
grep -q ' /mnt/mmcblk0p1 .*rw[, ]' /proc/mounts || exit 0
timeout 12 python3 -B /root/mira-health.py --audio-owned && exit 0
'''
    monitor = monitor.replace("#!/bin/sh\n", "#!/bin/sh\n" + guards, 1)
    monitor = monitor.replace("curl -s", "curl --connect-timeout 3 --max-time 8 -s")
    monitor = monitor.replace("sleep 50", "sleep 5").replace("LINES_MAX=11000", "LINES_MAX=1000").replace("LINES_MIN=6000", "LINES_MIN=500")
    files["/usr/bin/mgwp_monitor"] = monitor.encode()
    managed_guard = '''# MIRA services own audio/network while enabled; avoid two controllers.
if [ -x /etc/init.d/mira_player ] && /etc/init.d/mira_player enabled; then
    exit 0
fi
'''
    for name in ("/usr/bin/mgwp_app", "/usr/bin/mgwp_monitor"):
        text = files.get(name, old[name]).decode()
        files[name] = text.replace("#!/bin/sh\n", "#!/bin/sh\n" + managed_guard, 1).encode()
    files["/usr/bin/mgwp_mpd_monitor"] = b"#!/bin/sh\nexec timeout 160 python3 -B /root/mira-health.py\n"
    mpd = old["/etc/init.d/mpd"].decode()
    mpd = mpd.replace("start_service() {", "start_service() {\n\tgrep -q ' /mnt/mmcblk0p1 .*rw[, ]' /proc/mounts || return 1")
    mpd = mpd.replace("\tprocd_set_param stderr 1", "\tprocd_set_param stderr 1\n\tprocd_set_param respawn 3600 5 0\n\tprocd_set_param term_timeout 15")
    files["/etc/init.d/mpd"] = mpd.encode()
    startup = old["/etc/init.d/mgw7688"].decode()
    assert "/usr/bin/mpd --no-daemon /etc/mpd.conf &" in startup
    startup = startup.replace("/usr/bin/mpd --no-daemon /etc/mpd.conf &", "/etc/init.d/mpd start")
    files["/etc/init.d/mgw7688"] = startup.encode()
    # Deploy only changed files and require the exact original hashes on the board.
    files = {k:v for k,v in files.items() if old.get(k) != v}
    manifest = {k:{"before": hashlib.sha256(old[k]).hexdigest() if k in old else None,
                   "after": hashlib.sha256(v).hexdigest()} for k,v in files.items()}
    with tarfile.open(PRIVATE / "mira-repair.tar.gz", "w:gz") as archive:
        for k,v in list(files.items()) + [("/MANIFEST.json", json.dumps(manifest).encode())]:
            info = tarfile.TarInfo(k.lstrip("/"))
            info.size = len(v)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(v))
    print("Repair package:", len(files), "files; original and replacement hashes recorded")


if __name__ == "__main__":
    main()
