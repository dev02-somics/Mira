"""Package tested player/Wi-Fi code plus the saved state for this board repair."""
from pathlib import Path
import hashlib,io,json,tarfile

root=Path(__file__).resolve().parents[1]
payload={}
for package,location in [('mira_player','root/mira-player'),('mira_wifi','root/mira-wifi')]:
 for p in (root/'src'/package).glob('*.py'):
  payload[location+'/'+package+'/'+p.name]=p.read_bytes().replace(b'\r\n',b'\n')
with tarfile.open(root/'diagnostics/private/mira-sd-before-repair.tar.gz') as archive:
 state=archive.extractfile('./mira-player/state.json').read()
 assert json.loads(state)['version']==1
 payload['recovery-state.json']=state
payload['MANIFEST.json']=json.dumps({k:hashlib.sha256(v).hexdigest() for k,v in payload.items()}).encode()
with tarfile.open(root/'diagnostics/private/mira-seek-repair.tar.gz','w:gz') as archive:
 for name,data in payload.items():
  entry=tarfile.TarInfo(name);entry.mode=0o600;entry.size=len(data)
  archive.addfile(entry,io.BytesIO(data))
print('Packaged',len(payload),'files')
