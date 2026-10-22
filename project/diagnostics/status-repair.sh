python3 -B - <<'PY'
import json,pathlib,subprocess,time
for f in ['/tmp/mira-player-status.json','/tmp/mira-wifi-health.json']:
 try:
  data=json.loads(pathlib.Path(f).read_text());data['age_seconds']=round(time.monotonic()-data.get('monotonic',0),1)
  print(f,data)
 except Exception as e:print(f,type(e).__name__)
services=json.loads(subprocess.check_output(['ubus','call','service','list']))
for name in ['mira_player','mira_wifi','mpd']:
 print(name,[(v.get('pid'),v.get('running'),v.get('respawn')) for v in services.get(name,{}).get('instances',{}).values()])
PY
iw dev wlan0 link
ip -4 addr show wlan0
cat /sys/class/net/wlan0/address
mpc status
timeout 5 hwclock -w -u
echo RTC_WRITE_EXIT=$?
timeout 5 hwclock -r
echo RTC_READ_EXIT=$?
logread | tail -30
