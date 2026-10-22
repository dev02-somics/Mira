set -eu
umask 077
touch /tmp/mira-maintenance
python3 -B - <<'PY'
import pathlib,json
macs={section:pathlib.Path('/sys/class/net/'+dev+'/address').read_text().strip()
      for section,dev in [('mira_sta','wlan0'),('mira_ap','wlan0-1')]}
pathlib.Path('/tmp/mira-fixed-macs.json').write_text(json.dumps(macs))
print('Preserving current interface MACs',macs)
PY
/etc/init.d/cron stop
/etc/init.d/mira_player stop
python3 -B - <<'PY'
import os,pathlib,signal,time
targets={b'/usr/bin/mgwp_app',b'/usr/bin/mgwp_monitor',b'/usr/bin/mgwp_mpd_monitor',
         b'/usr/lib/python3.9/site-packages/mgw3/mgwp.pyc'}
records={}
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  args=(p/'cmdline').read_bytes().split(b'\0')
  ppid=int((p/'stat').read_text().rsplit(')',1)[1].split()[1])
  records[int(p.name)]=(args,ppid)
 except (OSError,ValueError): pass
selected={pid for pid,(args,ppid) in records.items() if targets.intersection(args)}
for unused in range(8):
 selected |= {pid for pid,(args,ppid) in records.items() if ppid in selected}
for pid in sorted(selected):
 try: os.kill(pid,signal.SIGCONT);os.kill(pid,signal.SIGTERM)
 except ProcessLookupError: pass
time.sleep(5)
for pid in selected:
 try:
  if pathlib.Path('/proc/%d/cmdline'%pid).read_bytes().split(b'\0') == records[pid][0]:
   os.kill(pid,signal.SIGKILL)
 except (OSError,ProcessLookupError): pass
print('Stopped vendor application/monitor processes:',len(selected))
PY
/etc/init.d/mpd stop
python3 -B - <<'PY'
import pathlib,os,signal,time
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  args=(p/'cmdline').read_bytes().split(b'\0')
  if args[0] == b'/usr/bin/mpd': os.kill(int(p.name),signal.SIGTERM)
 except (OSError,ValueError): pass
time.sleep(3)
print('Remaining SD users:')
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  paths=list((p/'fd').glob('*'))+[p/'cwd',p/'exe']
  used=[str(x.resolve()) for x in paths if str(x.resolve()).startswith('/mnt/mmcblk0p1/')]
  if used: print(p.name,used[:3])
 except OSError: pass
PY
sync
du -sh /mnt/mmcblk0p1/*
