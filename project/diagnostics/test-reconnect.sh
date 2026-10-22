set -eu
python3 -B -u - <<'PY'
import json,os,pathlib,signal,subprocess,time
def services():return json.loads(subprocess.check_output(['ubus','call','service','list'],timeout=10))
def pid(name):
 return next(v['pid'] for v in services()[name]['instances'].values() if v.get('running'))
status=json.loads(pathlib.Path('/tmp/mira-player-status.json').read_text())
assert status['state']=='idle'
old=pid('mpd');os.kill(old,signal.SIGKILL)
deadline=time.monotonic()+30
while time.monotonic()<deadline:
 time.sleep(1)
 try:
  new=pid('mpd')
  if new!=old:
   subprocess.run(['mpc','status'],check=True,stdout=subprocess.DEVNULL,timeout=3)
   break
 except Exception:pass
else:raise RuntimeError('MPD did not respawn')
print('MPD_RESPAWN_PASS',old,new,flush=True)
start=time.monotonic()
subprocess.run(['wifi','down','radio0'],check=True,timeout=30)
print('RADIO_DOWN_INJECTED',flush=True)
deadline=time.monotonic()+140
while time.monotonic()<deadline:
 time.sleep(3)
 try:
  health=json.loads(pathlib.Path('/tmp/mira-wifi-health.json').read_text())
  interface=json.loads(subprocess.check_output(['ubus','call','network.interface.mira_uplink','status'],timeout=8))
  player=json.loads(pathlib.Path('/tmp/mira-player-status.json').read_text())
  if health['state']=='connected' and interface.get('up') and interface.get('ipv4-address') and player['mqtt_ready']:
   print('WIFI_MQTT_RECONNECT_PASS',round(time.monotonic()-start,1),interface['ipv4-address'],flush=True)
   break
 except (OSError,ValueError,subprocess.SubprocessError):pass
else:raise RuntimeError('Wi-Fi/MQTT did not reconnect within limit')
PY
