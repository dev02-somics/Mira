python3 -B - <<'PY'
from pathlib import Path
import marshal,types,hashlib
for name in ['/etc/config/wireless','/tmp/.uci/wireless']:
 print(name)
 p=Path(name)
 if p.exists():
  for line in p.read_text().splitlines():
   if any(x in line for x in ['macaddr','config wifi','disabled','ifname','channel']):print(line)
def walk(code,path):
 for v in code.co_consts:
  if isinstance(v,types.CodeType):walk(v,path+':'+v.co_name)
  elif isinstance(v,str) and any(s in v for s in ['network restart','network reload','mgwp_cfg','wireless.','killall python']):
   if not any(s in v.lower() for s in ['password','passwd','.key','secret','token']):
    print('CODE',path,repr(v[:300]))
for p in Path('/usr/lib/python3.9/site-packages/mgw3').rglob('*.pyc'):
 try:walk(marshal.loads(p.read_bytes()[16:]),str(p))
 except Exception:pass
PY
ps w | tail -18
