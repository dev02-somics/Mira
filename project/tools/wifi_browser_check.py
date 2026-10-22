"""Chrome smoke test for the local demo, with mobile screenshots (stdlib only)."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class CDP:
    def __init__(self, url):
        target = urlsplit(url)
        self.socket = socket.create_connection((target.hostname, target.port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET %s HTTP/1.1\r\nHost: %s:%s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (target.path, target.hostname, target.port, key))
        self.socket.sendall(request.encode())
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            header += self.socket.recv(1)
        assert header.split(b"\r\n",1)[0].split()[1] == b"101", header
        accept = base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        assert accept in header
        self.seq = 0
    def read(self, length):
        chunks = bytearray()
        while len(chunks) < length:
            part = self.socket.recv(length-len(chunks))
            if not part:
                raise RuntimeError("Browser disconnected")
            chunks.extend(part)
        return bytes(chunks)
    def send(self, value):
        data = json.dumps(value).encode()
        mask = os.urandom(4)
        header = bytes([0x81, 0x80 | len(data)]) if len(data) < 126 else bytes([0x81,0xfe])+struct.pack("!H",len(data))
        self.socket.sendall(header+mask+bytes(b^mask[i%4] for i,b in enumerate(data)))
    def receive(self):
        full = b""
        while True:
            a,b = self.read(2)
            length = b&127
            if length == 126: length = struct.unpack("!H", self.read(2))[0]
            elif length == 127: length = struct.unpack("!Q", self.read(8))[0]
            mask = self.read(4) if b&128 else None
            data = self.read(length)
            if mask: data = bytes(v^mask[i%4] for i,v in enumerate(data))
            if a&15 == 8: raise RuntimeError("WebSocket closed")
            full += data
            if a&128: return json.loads(full)
    def call(self, method, **params):
        self.seq += 1
        self.send(dict(id=self.seq, method=method, params=params))
        while True:
            reply = self.receive()
            if reply.get("id") == self.seq:
                if "error" in reply: raise RuntimeError(str(reply["error"]))
                return reply.get("result", {})
    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result: raise RuntimeError(str(result["exceptionDetails"]))
        return result["result"].get("value")
    def wait(self, expression):
        until = time.monotonic()+20
        while time.monotonic() < until:
            if self.evaluate(expression): return
            time.sleep(.25)
        raise RuntimeError("Browser condition timed out: "+expression)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", action="store_true")
    args = parser.parse_args()
    url = "http://192.168.11.102:8088/" if args.device else "http://127.0.0.1:8088/"
    password = ((ROOT/"config/mira-wifi-access.local.txt").read_text().splitlines()[1].split(": ",1)[1]
                if args.device else "demo-wifi")
    with tempfile.TemporaryDirectory(prefix="mira-browser-") as profile:
        proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                                 "--no-default-browser-check", "--remote-debugging-port=9225",
                                 "--user-data-dir="+profile, "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        cdp = None
        try:
            until = time.monotonic()+20
            while True:
                try:
                    targets = json.load(urlopen("http://127.0.0.1:9225/json", timeout=1))
                    break
                except OSError:
                    if time.monotonic() > until: raise
                    time.sleep(.3)
            cdp = CDP(next(t["webSocketDebuggerUrl"] for t in targets if t["type"]=="page"))
            cdp.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
            cdp.call("Page.navigate", url=url)
            cdp.wait("typeof api === 'function' && document.readyState === 'complete'")
            time.sleep(.5)
            cdp.evaluate("document.getElementById('adminPassword').value=" + json.dumps(password) + "; document.querySelector('#loginForm button').click()")
            cdp.wait("!document.getElementById('dashboard').hidden && document.querySelectorAll('#networks .network').length>0")
            assert not cdp.evaluate("document.cookie")
            assert cdp.evaluate("document.documentElement.scrollWidth <= innerWidth")
            shot = cdp.call("Page.captureScreenshot", format="png", captureBeyondViewport=True)
            (ROOT/("docs/wifi-device-mobile.png" if args.device else "docs/wifi-preview-mobile.png")).write_bytes(base64.b64decode(shot["data"]))
            if args.device:
                print("PASS device browser login, real scan list, mobile layout, cookie protection.")
                return
            cdp.evaluate("document.querySelector('#networks button').click()")
            cdp.wait("document.getElementById('join').open")
            cdp.evaluate("document.getElementById('wifiPassword').value='test-password'; document.querySelector('#joinForm button[type=submit]').click()")
            cdp.wait("document.getElementById('ssh').textContent==='ssh root@192.168.1.120'")
            assert cdp.evaluate("document.querySelectorAll('#saved .network').length") == 1
            cdp.evaluate("document.getElementById('logout').click()")
            cdp.wait("!document.getElementById('loginCard').hidden")
            print("PASS mobile layout, login, scan rendering, join dialog, connected IP, saved network, logout.")
        finally:
            if cdp: cdp.socket.close()
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
