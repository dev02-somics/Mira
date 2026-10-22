#!/usr/bin/python3
"""MP3 -> MPD/ALSA -> WM8960 analog output -> amplifier on MIRA.

No direct I2C register or I2S pin programming: the kernel codec driver does it.
Defaults use firmware GPIO 34 (enable=1) and 17 (unmute=0).
These GPIOs must match the actual amplifier wiring. Run --check first.
"""
import argparse
try:
    import fcntl
except ImportError:
    fcntl = None  # Desktop supports offline tests only
import os
import signal
import socket
import subprocess
import time
import re

SONG = 'music-test.mp3'
MUSIC_FILE = '/mnt/mmcblk0p1/mgwp/audio/' + SONG
APP_FILE = '/usr/lib/python3.9/site-packages/mgw3/mgwp.pyc'
GPIO_HANDLES = {}


class MPD:
    def __init__(self):
        self.sock = socket.create_connection(('127.0.0.1', 6600), timeout=10)
        self.stream = self.sock.makefile('rwb')
        if not self.stream.readline().startswith(b'OK MPD '):
            raise RuntimeError('MPD handshake failed')

    def command(self, command):
        self.stream.write((command + '\n').encode())
        self.stream.flush()
        result = {}
        while True:
            line = self.stream.readline().decode().rstrip('\n')
            if line == 'OK':
                return result
            if not line or line.startswith('ACK '):
                raise RuntimeError(line or 'MPD disconnected')
            if ': ' in line:
                key, value = line.split(': ', 1)
                result[key] = value

    def close(self):
        self.stream.close()
        self.sock.close()


def interrupted(signum, frame):
    raise KeyboardInterrupt


def find_mgwp():
    found = []
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            with open('/proc/' + name + '/cmdline', 'rb') as f:
                args = f.read().split(b'\0')
            if APP_FILE.encode() in args:
                found.append(int(name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    return found


def run_tool(args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True, timeout=10)


def gpio(pin, level):
    # MRAA pin numbers are board mappings, not Linux gpioXX numbers.
    # Keep objects alive: constructing a separate "get" tool may change direction.
    import mraa
    if pin not in GPIO_HANDLES:
        handle=mraa.Gpio(pin)
        if handle.dir(mraa.DIR_OUT)!=mraa.SUCCESS:
            raise RuntimeError('Cannot set GPIO output: '+str(pin))
        GPIO_HANDLES[pin]=handle
    if GPIO_HANDLES[pin].write(level)!=mraa.SUCCESS:
        raise RuntimeError('Cannot write GPIO: '+str(pin))
    if GPIO_HANDLES[pin].read()!=level:
        raise RuntimeError('GPIO level verification failed: '+str(pin))


def mixer(control, value):
    subprocess.run(['/usr/bin/amixer', '-c', '0', 'sset', control, '-M', value],
                   check=True, stdout=subprocess.DEVNULL, timeout=10)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true', help='Read hardware/status only; do not play or change GPIO')
    p.add_argument('--volume', type=int, default=100, help='Mapped ALSA volume 1..100 percent; default 100')
    p.add_argument('--file', default=MUSIC_FILE, help='Audio file inside the MPD music directory')
    p.add_argument('--amp-gpio', type=int, default=34, help='MRAA pin used by firmware for amplifier enable')
    p.add_argument('--amp-active', type=int, choices=[0,1], default=1)
    p.add_argument('--mute-gpio', type=int, default=17, help='Firmware audio mute pin')
    p.add_argument('--unmute-level', type=int, choices=[0,1], default=0)
    p.add_argument('--no-gpio', action='store_true', help='Leave GPIO alone, for an amplifier already enabled')
    args=p.parse_args()
    if not 1 <= args.volume <= 100:
        p.error('--volume must be 1..100')
    if args.amp_gpio < 0 or args.mute_gpio < 0 or args.amp_gpio == args.mute_gpio:
        p.error('GPIO pins must be nonnegative and different')
    return args


def main():
    global MUSIC_FILE, SONG
    args=parse_args()
    music_root=os.path.realpath('/mnt/mmcblk0p1/mgwp/audio')
    MUSIC_FILE=os.path.realpath(args.file)
    if not MUSIC_FILE.startswith(music_root+os.sep):
        raise RuntimeError('File must be inside '+music_root)
    SONG=os.path.relpath(MUSIC_FILE,music_root)
    if any(ord(c)<32 for c in SONG):
        raise RuntimeError('Invalid audio file path')
    song_arg='"'+SONG.replace('\\','\\\\').replace('"','\\"')+'"'
    with open('/proc/asound/cards') as f:
        cards=f.read()
    print(cards, flush=True)
    if 'WM8960' not in cards.upper():
        raise RuntimeError('Card WM8960 khong duoc nhan; khong thay doi phan cung.')
    control_text=run_tool(['/usr/bin/amixer','-c','0','scontrols'])
    controls=set(re.findall(r"Simple mixer control '(.+)',\d+",control_text))
    selected='Headphone'
    if args.check:
        print(control_text,flush=True)
        print('File:',MUSIC_FILE,'exists:',os.path.isfile(MUSIC_FILE),flush=True)
        print('Output:',selected,'GPIO enable:',args.amp_gpio,'GPIO mute:',args.mute_gpio,flush=True)
        print(run_tool(['/usr/bin/mpc','status']),flush=True)
        return
    if selected not in controls:
        raise RuntimeError('Khong co ALSA control: '+selected)
    lock = open('/tmp/main-run-mp3.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Chuong trinh phat nhac dang chay.')
    if not os.path.isfile(MUSIC_FILE):
        raise RuntimeError('Khong tim thay file: ' + MUSIC_FILE)
    with open('/proc/mounts') as f:
        if not any(line.split()[1] == '/mnt/mmcblk0p1' for line in f):
            raise RuntimeError('The nho chua mount tai /mnt/mmcblk0p1.')

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    mpd = MPD()
    suspended = []
    song_id = None
    amp_touched = False
    mixer_touched = False
    old = mpd.command('status')
    if old.get('state') == 'play':
        mpd.close()
        raise RuntimeError('MPD dang phat bai khac. Hay dung bai do truoc.')
    try:
        for pid in find_mgwp():
            with open('/proc/%d/status' % pid) as f:
                already_stopped = any(line.startswith('State:') and 'T' in line.split()[1] for line in f)
            if not already_stopped:
                with open('/proc/%d/stat' % pid) as f:
                    identity = f.read().rsplit(')', 1)[1].split()[19]
                suspended.append((pid, identity))
                os.kill(pid, signal.SIGSTOP)
        mpd.command('update ' + song_arg)
        deadline = time.monotonic() + 90
        while 'updating_db' in mpd.command('status'):
            if time.monotonic() > deadline:
                raise RuntimeError('MPD cap nhat thu vien qua lau.')
            time.sleep(1)
        song_id = mpd.command('addid ' + song_arg)['Id']
        for key, value in [('repeat', '0'), ('random', '0'), ('consume', '0'), ('single', '1')]:
            mpd.command(key + ' ' + value)
        if not args.no_gpio:
            amp_touched=True
            gpio(args.mute_gpio,1-args.unmute_level)
            gpio(args.amp_gpio,1-args.amp_active)
        mixer_touched=True
        # Only touch controls actually advertised by this driver.
        for control in ('Headphone','Speaker'):
            if control in controls:
                mixer(control,'0%')
        for control in ('Left Output Mixer PCM','Right Output Mixer PCM'):
            if control in controls:
                mixer(control,'on')
        if 'DAC' in controls:
            mixer('DAC','90%')
        elif 'Playback' in controls:
            mixer('Playback','100%')
        mixer(selected,str(args.volume)+'%')
        if not args.no_gpio:
            gpio(args.amp_gpio,args.amp_active)
            time.sleep(0.1)
            gpio(args.mute_gpio,args.unmute_level)
        mpd.command('playid ' + song_id)
        print('Dang phat: ' + MUSIC_FILE, flush=True)
        print('WM8960 analog output:',selected,'volume:',str(args.volume)+'%',flush=True)
        if not args.no_gpio:
            print('GPIO enable:',args.amp_gpio,'=',GPIO_HANDLES[args.amp_gpio].read(),
                  'unmute:',args.mute_gpio,'=',GPIO_HANDLES[args.mute_gpio].read(),flush=True)
        print('Nhan Ctrl+C de dung; MGWP se tu chay lai.', flush=True)
        deadline = time.monotonic() + 3600
        while True:
            time.sleep(1)
            status = mpd.command('status')
            if status.get('state') != 'play' or status.get('songid') != song_id:
                if status.get('error'):
                    raise RuntimeError(status['error'])
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Da vuot qua thoi gian phat toi da 1 gio.')
    finally:
        # Do not allow a second Ctrl+C to prevent restoring MGWP.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            if song_id is not None:
                status = mpd.command('status')
                if status.get('songid') == song_id:
                    mpd.command('stop')
                mpd.command('deleteid ' + song_id)
            for key in ('repeat', 'random', 'consume', 'single'):
                if key in old:
                    mpd.command(key + ' ' + old[key])
            if old.get('volume','-1')!='-1':
                mpd.command('setvol '+old['volume'])
        except Exception as e:
            print('Khoi phuc MPD: ' + str(e), flush=True)
        finally:
            try:
                time.sleep(0.3)
                if amp_touched:
                    try:
                        gpio(args.mute_gpio,1-args.unmute_level)
                    finally:
                        gpio(args.amp_gpio,1-args.amp_active)
                if mixer_touched:
                    mixer(selected,'0%')
            except Exception as e:
                print('Loi tat output/amplifier: '+str(e),flush=True)
            for pid, identity in suspended:
                try:
                    with open('/proc/%d/stat' % pid) as f:
                        current = f.read().rsplit(')', 1)[1].split()[19]
                    if current == identity:
                        os.kill(pid, signal.SIGCONT)
                except (FileNotFoundError, ProcessLookupError):
                    pass
            mpd.close()
            print('Da ket thuc va khoi phuc MGWP.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Da dung nhac.')
    except Exception as e:
        print('Loi: ' + str(e), flush=True)
        raise SystemExit(1)
