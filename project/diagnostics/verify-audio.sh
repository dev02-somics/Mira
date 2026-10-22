set -eu
touch /tmp/mira-maintenance
trap 'rm -f /tmp/mira-maintenance; /etc/init.d/mira_player start' EXIT
/etc/init.d/mira_player stop
PYTHONPATH=/root/mira-player python3 -B - <<'PY'
import fcntl,json,pathlib,time,subprocess
from mira_player.audio import HardwareAudio
from mira_player.config import load
lock=open('/tmp/mira-player.lock','w')
deadline=time.monotonic()+100
while True:
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
 except BlockingIOError:
  if time.monotonic()>deadline:raise
  time.sleep(.5)
audio_lock=open('/tmp/main-run-mp3.lock','w');fcntl.flock(audio_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
cfg=load('/root/mira-player/config.json')
state=json.loads(pathlib.Path('/mnt/mmcblk0p1/mira-player/state.json').read_text())
old_volume=state['volume']
file=min(pathlib.Path(cfg['cache_dir']).glob('*.mp3'),key=lambda p:p.stat().st_size)
audio=HardwareAudio(cfg)
try:
 audio.start(file,10)
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  status=audio.poll()
  if status['state']=='playing':break
  time.sleep(.3)
 else:raise RuntimeError('playback did not start')
 time.sleep(2)
 status=audio.poll();assert status['state']=='playing' and status['elapsed']>0
 print('PLAY',status)
 audio.pause();assert audio.poll()['state']=='paused'
 target=min(10.0,audio.poll()['duration']/2)
 actual=audio.seek(target)
 assert abs(actual-target)<.6 and audio.poll()['state']=='paused'
 print('SEEK_WHILE_PAUSED',actual,'duration',audio.poll()['duration'])
 print('PAUSE',audio.poll())
 suspended=list(audio.record['suspended'])
 for pid,identity in suspended:
  assert audio.identity(pid)==identity
  assert 'T' in pathlib.Path('/proc/%d/status'%pid).read_text().split('State:',1)[1].splitlines()[0]
 audio.resume();time.sleep(1)
 assert audio.poll()['state']=='playing'
 print('RESUME',audio.poll())
 actual=audio.seek(3)
 assert abs(actual-3)<.6 and audio.poll()['state']=='playing'
 print('SEEK_BACKWARD_WHILE_PLAYING',actual)
finally:
 audio.stop();audio.set_volume(old_volume)
print('STOP: lease removed =',not audio.lease.exists())
for pid,identity in suspended:
 assert audio.identity(pid)==identity
 assert 'T' not in pathlib.Path('/proc/%d/status'%pid).read_text().split('State:',1)[1].splitlines()[0]
print('Original volume restored; resumed legacy processes:',len(suspended))
PY
