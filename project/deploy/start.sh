#!/bin/sh
set -eu
export PYTHONPATH=/root/mira-player
export PYTHONDONTWRITEBYTECODE=1
while ! grep -q ' /mnt/mmcblk0p1 .*rw[, ]' /proc/mounts; do
    logger -t mira-player 'Waiting for writable SD'
    sleep 5
done
while ! /usr/bin/python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",6600),timeout=2); assert s.recv(128).startswith(b"OK MPD "); s.close()' >/dev/null 2>&1; do
    logger -t mira-player 'Waiting for MPD'
    sleep 5
done
exec /usr/bin/python3 -u -m mira_player.app --config /root/mira-player/config.json
