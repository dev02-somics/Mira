uptime
ps w
cat /tmp/mira-audio-lease.json
ls -l /tmp/mira-maintenance
logread | grep -E 'network|netifd|mira|watchdog|kill|Out of memory' | tail -100
python3 -B - <<'PY'
from pathlib import Path
for root in ['/etc/init.d','/etc/hotplug.d','/usr/bin']:
 for p in Path(root).rglob('*'):
  if not p.is_file():continue
  try:
   if p.stat().st_size>150000:continue
   lines=p.read_text().splitlines()
  except (OSError,UnicodeError):continue
  for i,line in enumerate(lines):
   if '/etc/init.d/network' in line and any(word in line for word in ['restart','reload']):
    print('NETWORK_RESTART_SOURCE',p,i+1,line.strip())
PY
