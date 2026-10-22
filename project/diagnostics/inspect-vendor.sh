python3 -B - <<'PY'
from pathlib import Path
import json,subprocess,re
p=Path('/usr/bin/mgwp_cfg')
for n,line in enumerate(p.read_text().splitlines(),1):
 if re.search('(?i)password|passwd|\.key|secret|token',line):line='[credential line omitted]'
 print('%d %s'%(n,line))
for name in ['/mnt/mmcblk0p1/mgwp_error.log','/mnt/mmcblk0p1/mgwp.log']:
 print(name)
 for line in Path(name).read_text(errors='replace').splitlines()[-25:]:
  if not re.search('(?i)password|passwd|authorization|token|secret',line):print(line)
wireless=json.loads(subprocess.check_output(['ubus','call','uci','get','{"config":"wireless"}']))
for name,settings in wireless['values'].items():
 print(name,{k:settings[k] for k in ['.type','mode','ssid','disabled','macaddr','network'] if k in settings})
PY
