set -eu
test -f /tmp/mira-maintenance
python3 -B - <<'PY'
import ast,hashlib,json,os,pathlib,subprocess,tarfile
with tarfile.open('/tmp/mira-repair.tar.gz') as archive:
    manifest=json.load(archive.extractfile('MANIFEST.json'))
    payload={}
    for name,entry in manifest.items():
        p=pathlib.Path(name)
        assert str(p) == name and '..' not in p.parts
        before=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        assert before == entry['before'], 'Original changed: '+name
        data=archive.extractfile(name.lstrip('/')).read()
        assert hashlib.sha256(data).hexdigest() == entry['after']
        if name.endswith('.py'): ast.parse(data,filename=name)
        elif name.startswith(('/etc/init.d/', '/usr/bin/', '/etc/hotplug.d/')) or name.endswith('.sh'):
            subprocess.run(['/bin/sh','-n'],input=data,check=True)
        payload[name]=data
    for name,data in payload.items():
        p=pathlib.Path(name)
        mode=p.stat().st_mode & 0o777 if p.exists() else 0o755
        tmp=p.with_name(p.name+'.repair-new')
        with tmp.open('wb') as out:
            out.write(data);out.flush();os.fsync(out.fileno())
        tmp.chmod(mode)
        tmp.replace(p)
    print('Installed and verified',len(payload),'files')
# Modify only persistent MAC options, preserving transient UCI credentials.
import shutil,re
p=pathlib.Path('/etc/config/wireless');text=p.read_text()
macs=json.loads(pathlib.Path('/tmp/mira-fixed-macs.json').read_text())
for section,mac in macs.items():
    pattern=r"(?m)^config wifi-iface '"+section+r"'\n(?:(?!^config ).*\n?)*"
    match=re.search(pattern,text);assert match,section
    block=re.sub(r'(?m)^\s*option macaddr .*\n?', '',match.group())
    block=block.rstrip()+"\n\toption macaddr '"+mac+"'\n\n"
    text=text[:match.start()]+block+text[match.end():]
tmp=pathlib.Path('/etc/config/wireless.repair-new')
tmp.write_text(text);tmp.chmod(0o600);tmp.replace(p)
# Seed a credential backup now, rather than waiting for the next connection.
shutil.copy2('/etc/mira-wifi/profiles.json','/etc/mira-wifi/profiles.json.bak')
os.chmod('/etc/mira-wifi/profiles.json.bak',0o600)
print('Persistent interface MACs:',macs)
PY
uci set system.@system[0].log_size='256'
uci commit system
/etc/init.d/mpd enable
/etc/init.d/mira_player enable
/etc/init.d/mira_wifi enable
/etc/init.d/sysntpd enable
sync
