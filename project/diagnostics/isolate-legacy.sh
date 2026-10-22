set -eu
test -f /tmp/mira-maintenance
umask 077
tar -czf /tmp/mira-legacy-backup.tar.gz /usr/bin/mgwp_app /usr/bin/mgwp_monitor
python3 -B - <<'PY'
from pathlib import Path
import os,re,json,subprocess
# The legacy app is a second device controller. Leave its boot GPIO/ALSA
# initialisation intact, but run only the new player/network control plane.
for name in ['/usr/bin/mgwp_app','/usr/bin/mgwp_monitor']:
 p=Path(name);text=p.read_text()
 guard='''# MIRA services own audio/network while enabled; avoid two controllers.
if [ -x /etc/init.d/mira_player ] && /etc/init.d/mira_player enabled; then
    exit 0
fi
'''
 assert text.startswith('#!/bin/sh\n')
 p.write_text(text.replace('#!/bin/sh\n','#!/bin/sh\n'+guard,1))
 subprocess.run(['/bin/sh','-n',name],check=True)
p=Path('/etc/config/wireless')
text=p.read_text();macs=json.loads(Path('/tmp/mira-fixed-macs.json').read_text())
for section,mac in macs.items():
 pattern=r"(?m)^config wifi-iface '"+section+r"'\n(?:(?!^config ).*\n?)*"
 match=re.search(pattern,text)
 assert match,section
 block=re.sub(r'(?m)^\s*option macaddr .*\n?', '',match.group())
 block=block.rstrip()+"\n\toption macaddr '"+mac+"'\n\n"
 text=text[:match.start()]+block+text[match.end():]
tmp=Path('/etc/config/wireless.repair-new')
tmp.write_text(text);tmp.chmod(0o600);tmp.replace(p)
print('Written persistent interface MACs:',macs)
PY
/etc/init.d/mira_wifi restart
sync
echo 'Legacy controller disabled while mira_player is enabled'
