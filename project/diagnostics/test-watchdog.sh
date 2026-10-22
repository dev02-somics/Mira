set -eu
python3 -B -u - <<'PY'
import json,os,pathlib,signal,subprocess,time
path=pathlib.Path('/tmp/mira-player-status.json')
deadline=time.monotonic()+210
while time.monotonic()<deadline:
 try:
  probe=json.loads(path.read_text());pid=probe['pid']
  ticks=pathlib.Path('/proc/%d/stat'%pid).read_text().rsplit(')',1)[1].split()[19]
  if probe['mqtt_ready'] and time.monotonic()-int(ticks)/os.sysconf('SC_CLK_TCK')>=185:
   break
 except (OSError,ValueError,KeyError):pass
 time.sleep(3)
else:raise RuntimeError('Service not ready after startup grace')
status=json.loads(path.read_text());old=status['pid']
assert status['state']=='idle' and status['mqtt_ready']
assert time.monotonic()>240
assert b'mira_player.app' in pathlib.Path('/proc/%d/cmdline'%old).read_bytes().split(b'\0')
start=time.monotonic()
try:
 os.kill(old,signal.SIGSTOP)
 # Age the heartbeat to exercise the production timeout path immediately.
 # Without this injection the normal stale-heartbeat interval is 180 seconds.
 status['monotonic']=time.monotonic()-190
 tmp=path.with_suffix('.test');tmp.write_text(json.dumps(status));tmp.replace(path)
 subprocess.run(['python3','-B','/root/mira-health.py'],check=True,timeout=120)
 deadline=time.monotonic()+140
 while time.monotonic()<deadline:
  time.sleep(2)
  try:
   new=json.loads(path.read_text())
   if new['pid']!=old and new['mqtt_ready'] and time.monotonic()-new['monotonic']<15:
    print('FROZEN_PLAYER_RECOVERY_PASS',old,new['pid'],round(time.monotonic()-start,1),flush=True)
    break
  except (OSError,ValueError):pass
 else:raise RuntimeError('Frozen service did not recover')
finally:
 try:
  if b'mira_player.app' in pathlib.Path('/proc/%d/cmdline'%old).read_bytes().split(b'\0'):
   os.kill(old,signal.SIGCONT)
 except OSError:pass
 subprocess.run(['/etc/init.d/mira_player','start'],timeout=15)
PY
