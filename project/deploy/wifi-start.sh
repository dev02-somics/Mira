#!/bin/sh
cd /root/mira-wifi || exit 1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/root/mira-wifi
umask 077
exec /usr/bin/python3 -m mira_wifi.app
