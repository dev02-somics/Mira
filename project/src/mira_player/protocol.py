"""File events: validation, verified HTTP download and atomic journal writes."""
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

MAX_FILE = 20 * 1024 * 1024
MAX_PAYLOAD = 16384


def validate_event(payload, allowed_hosts, allowed_ports=(80, 443)):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError('MQTT payload exceeds 16 KiB')
    event = json.loads(payload.decode('utf-8'))
    if not isinstance(event, dict):
        raise ValueError('Payload must be a JSON object')
    for field in ('id', 'fileName', 'mimeType', 'sha256', 'downloadUrl'):
        if not isinstance(event.get(field), str) or not event[field].strip():
            raise ValueError('Missing/invalid field: ' + field)
    if len(event['id']) > 256 or len(event['fileName']) > 512:
        raise ValueError('id/fileName too long')
    if event.get('type') != 'file' or event.get('delivery') != 'http':
        raise ValueError('Expected type=file and delivery=http')
    if type(event.get('size')) is not int or not 0 < event['size'] <= MAX_FILE:
        raise ValueError('size must be integer bytes between 1 and 20 MiB')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', event['sha256']):
        raise ValueError('sha256 must contain 64 hexadecimal digits')
    extension = Path(event['fileName']).suffix.lower()
    types = {'.mp3': {'audio/mpeg', 'audio/mp3'},
             '.wav': {'audio/wav', 'audio/x-wav', 'audio/wave'}}
    if extension not in types or event['mimeType'].lower() not in types[extension]:
        raise ValueError('Only MP3/WAV with matching mimeType are supported')
    validate_url(event['downloadUrl'], allowed_hosts, allowed_ports)
    return event


def validate_url(url, allowed_hosts, allowed_ports=(80, 443)):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or parsed.hostname not in allowed_hosts or parsed.fragment
            or parsed.port not in (None,) + tuple(allowed_ports)):
        raise ValueError('downloadUrl is outside the configured HTTP origin')
    if not parsed.path.startswith('/api/files/'):
        raise ValueError('downloadUrl must use /api/files/')


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts, allowed_ports=(80, 443)):
        self.allowed_hosts = allowed_hosts
        self.allowed_ports = allowed_ports

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.allowed_hosts, self.allowed_ports)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sync_directory(path):
    if os.name != 'posix':
        return
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        try:
            os.fsync(fd)
        except OSError as error:
            if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
            os.sync()
    finally:
        os.close(fd)


def atomic_bytes(path, content):
    temp = path.with_suffix('.tmp')
    with temp.open('wb') as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.replace(str(temp), str(path))
    sync_directory(path.parent)


def atomic_json(path, data):
    content = json.dumps(data, ensure_ascii=False).encode('utf-8')
    atomic_bytes(path, content)
    # A second snapshot lets startup recover a damaged primary journal.
    atomic_bytes(Path(str(path) + '.bak'), content)


def require_card(music_dir):
    # A firmware-created symlink into /tmp must not be mistaken for an SD card.
    root = '/mnt/mmcblk0p1'
    with open('/proc/mounts') as source:
        mounted = any(line.split()[1] == root for line in source)
    if not mounted or not str(music_dir.resolve()).startswith(root + '/'):
        raise RuntimeError('SD card is not mounted at ' + root)


def download(event, destination, allowed_hosts, stop, allowed_ports=(80, 443), token=None):
    temp = destination.with_suffix(destination.suffix + '.part')
    opener = urllib.request.build_opener(CheckedRedirect(allowed_hosts, allowed_ports))
    started = time.monotonic()
    digest = hashlib.sha256()
    total = 0
    headers = {'Accept-Encoding': 'identity'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        req = urllib.request.Request(event['downloadUrl'], headers=headers)
        with opener.open(req, timeout=15) as response, temp.open('wb') as out:
            validate_url(response.geturl(), allowed_hosts, allowed_ports)
            length = response.headers.get('Content-Length')
            if length is not None and int(length) != event['size']:
                raise ValueError('HTTP Content-Length does not match MQTT size')
            while True:
                if stop.is_set():
                    raise InterruptedError('Download stopped')
                if time.monotonic() - started > 300:
                    raise TimeoutError('Download exceeded 300 seconds')
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > event['size']:
                    raise ValueError('Download exceeds declared size')
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if total != event['size']:
            raise ValueError('Downloaded size does not match MQTT size')
        if digest.hexdigest() != event['sha256'].lower():
            raise ValueError('Downloaded SHA-256 does not match MQTT sha256')
        os.replace(str(temp), str(destination))
        sync_directory(destination.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
