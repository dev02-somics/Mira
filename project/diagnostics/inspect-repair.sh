set -eu
grep -E 'state_file|restore_paused|music_directory|log_file' /etc/mpd.conf
cat /etc/init.d/mgw7688 /etc/init.d/nwcfg /etc/init.d/swap_gen /etc/init.d/sysfixtime
cat /etc/hotplug.d/ntp/*
cat /etc/config/fstab
iw phy phy0 info | tail -35
python3 -B - <<'PY'
import hashlib,pathlib,json
for base in ['/root/mira-player/mira_player','/root/mira-wifi/mira_wifi']:
 print(base)
 print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path(base).glob('*.py')}))
PY
