set -eu
test -f /tmp/mira-maintenance
if grep -q ' /mnt/mmcblk0p1 ' /proc/mounts; then
    echo 'Refusing fsck on mounted SD'
    exit 1
fi
set +e
timeout 90 fsck.fat -a -V /dev/mmcblk0p1 >/tmp/mira-fsck-repair.txt 2>&1
result=$?
set -e
cat /tmp/mira-fsck-repair.txt
[ "$result" -le 1 ] || exit "$result"
timeout 90 fsck.fat -n /dev/mmcblk0p1 >/tmp/mira-fsck-after.txt 2>&1
cat /tmp/mira-fsck-after.txt
uci set fstab.@global[0].check_fs='1'
uci set fstab.@mount[0].options='rw,noatime,errors=remount-ro'
uci commit fstab
mount -t vfat -o rw,noatime,errors=remount-ro /dev/mmcblk0p1 /mnt/mmcblk0p1
swapon /mnt/mmcblk0p1/swapfile
/etc/init.d/mpd start
sleep 3
PYTHONPATH=/root/mira-player python3 -B - <<'PY'
from pathlib import Path
from mira_player.legacy_audio import MPD
client=MPD()
status=client.command('status')
current=client.command('currentsong')
if current.get('file') and status.get('state') != 'play':
    path=Path('/mnt/mmcblk0p1/mgwp/audio')/current['file']
    if not path.exists() and current.get('Id'):
        client.command('deleteid '+current['Id'])
        print('Removed only the missing current playlist entry')
client.command('clearerror')
print('MPD after recovery:',client.command('status'))
client.close()
PY
/etc/init.d/mira_wifi restart
/etc/init.d/mira_player start
start-stop-daemon -S -b -x /usr/bin/mgwp_app
/etc/init.d/sysntpd restart
sync
rm /tmp/mira-maintenance
/etc/init.d/cron start
df -h /overlay
