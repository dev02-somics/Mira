python3 -B -u - <<'PY'
import json,pathlib,subprocess,time,hashlib
def run(args):return subprocess.check_output(args,timeout=15,text=True,stderr=subprocess.STDOUT)
result={'uptime':float(pathlib.Path('/proc/uptime').read_text().split()[0]),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
for name in ['mira-player-status','mira-wifi-health']:
 try:
  data=json.loads(pathlib.Path('/tmp/'+name+'.json').read_text());data['age_seconds']=round(time.monotonic()-data.get('monotonic',0),2);result[name]=data
 except OSError:result[name]=None
services=json.loads(run(['ubus','call','service','list']))
result['services']={name:[{k:v.get(k) for k in ['pid','running','respawn']} for v in services.get(name,{}).get('instances',{}).values()] for name in ['mira_player','mira_wifi','mpd','cron']}
wireless=json.loads(run(['ubus','call','network.wireless','status']))
result['wireless']=[]
for item in wireless.get('radio0',{}).get('interfaces',[]):
 dev=item.get('ifname')
 entry={'section':item.get('section'),'device':dev,'ssid':item.get('config',{}).get('ssid')}
 if dev:
  try:
   entry['mac']=pathlib.Path('/sys/class/net/'+dev+'/address').read_text().strip()
   entry['addresses']=run(['ip','-4','addr','show',dev])
   if item.get('section')=='mira_sta':entry['link']=run(['iw','dev',dev,'link'])
  except Exception as e:entry['error']=type(e).__name__
 result['wireless'].append(entry)
result['mpd']=run(['mpc','status'])
result['sd_mount']=[l for l in pathlib.Path('/proc/mounts').read_text().splitlines() if '/mnt/mmcblk0p1 ' in l]
result['kernel_issues']=[l for l in run(['dmesg']).splitlines() if any(x in l.lower() for x in ['fat-fs','oom','out of memory','low voltage'])]
result['memory']=pathlib.Path('/proc/meminfo').read_text()
result['legacy_processes']=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  args=(p/'cmdline').read_bytes().split(b'\0')
  if b'/usr/bin/mgwp_app' in args or b'/usr/lib/python3.9/site-packages/mgw3/mgwp.pyc' in args:result['legacy_processes'].append(int(p.name))
 except OSError:pass
print(json.dumps(result,indent=2))
PY
