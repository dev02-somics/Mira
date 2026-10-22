set -eu
umask 077
touch /tmp/mira-maintenance
/etc/init.d/mira_player stop
/etc/init.d/mira_wifi stop
tar -czf /tmp/mira-before-seek.tar.gz /root/mira-player /root/mira-wifi /mnt/mmcblk0p1/mira-player/state.json /mnt/mmcblk0p1/mira-player/state.json.bak
python3 -B -u - <<'PY'
import ast,hashlib,json,os,pathlib,tarfile,time
with tarfile.open('/tmp/mira-seek-repair.tar.gz') as archive:
 manifest=json.load(archive.extractfile('MANIFEST.json'));payload={}
 for name,digest in manifest.items():
  data=archive.extractfile(name).read();assert hashlib.sha256(data).hexdigest()==digest
  if name!='recovery-state.json':
   assert name.startswith(('root/mira-player/mira_player/','root/mira-wifi/mira_wifi/')) and '..' not in pathlib.Path(name).parts
   ast.parse(data,filename=name)
  payload[name]=data
 for name,data in payload.items():
  if name=='recovery-state.json':continue
  target=pathlib.Path('/'+name);tmp=target.with_suffix('.repair-new')
  with tmp.open('wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
  tmp.chmod(0o644);tmp.replace(target)
 state=pathlib.Path('/mnt/mmcblk0p1/mira-player/state.json')
 # Do not overwrite a journal that another session has already recovered.
 try:json.loads(state.read_text());needs_recovery=False
 except (OSError,ValueError):needs_recovery=True
 if needs_recovery:
  restored=json.loads(payload['recovery-state.json'])
  for key,record in restored['media'].items():
   if record['status']!='ready':continue
   event=record['event']
   p=pathlib.Path('/mnt/mmcblk0p1/mgwp/audio/mira-cache')/(hashlib.sha256(key.encode()).hexdigest()+pathlib.Path(event['fileName']).suffix.lower())
   if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=event['sha256']:
    record.update(status='failed',error='cache_unavailable_after_recovery')
  for old in [state,pathlib.Path(str(state)+'.bak')]:
   if old.exists():old.rename(old.with_name(old.name+'.damaged-'+str(time.time_ns())))
  import sys
  sys.path.insert(0,'/root/mira-player')
  from mira_player.protocol import atomic_json
  atomic_json(state,restored)
  print('Recovered saved state; valid cached tracks:',sum(v['status']=='ready' for v in restored['media'].values()))
print('Installed player and Wi-Fi modules')
PY
sync
/etc/init.d/mira_wifi start
/etc/init.d/mira_player start
rm /tmp/mira-maintenance
