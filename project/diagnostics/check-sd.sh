set -eu
test -f /tmp/mira-maintenance
swapoff /mnt/mmcblk0p1/swapfile
sync
umount /mnt/mmcblk0p1
if grep -q ' /mnt/mmcblk0p1 ' /proc/mounts; then
    echo 'Still mounted; refusing filesystem check'
    exit 1
fi
set +e
timeout 90 fsck.fat -n -v /dev/mmcblk0p1 >/tmp/mira-fsck-before.txt 2>&1
result=$?
cat /tmp/mira-fsck-before.txt
echo "FSCK_READONLY_EXIT=$result"
exit 0
