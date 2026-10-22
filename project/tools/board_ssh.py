"""Maintenance transport: pinned host key; password supplied via private temp file."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HOST = "192.168.11.102"
KEY = "SHA256:gBkDs0Lq+3w4uVcvIRUVagpGEBwq78cns8+JRPPWMb8"
PUTTY = Path("C:/Program Files/PuTTY")


def base(tool):
    return [str(PUTTY / tool), "-batch", "-hostkey", KEY, "-pwfile",
            str(Path(os.environ["TEMP"]) / "mira-ssh-repair.local.txt")]


def remote(command, timeout=180):
    with tempfile.TemporaryDirectory(prefix="mira-command-") as folder:
        script = Path(folder) / "command.sh"
        script.write_bytes(command.encode("utf-8"))
        return subprocess.run(base("plink.exe") + ["-ssh", "-m", str(script), "root@" + HOST],
                              capture_output=True, timeout=timeout)


def main():
    mode, source = sys.argv[1:3]
    if mode == "script":
        command = Path(source).read_text(encoding="utf-8")
        result = remote(command, int(sys.argv[3]) if len(sys.argv) > 3 else 180)
    elif mode == "exec":
        result = remote(source)
    elif mode in ("get", "put"):
        target = sys.argv[3]
        if mode == "get":
            source = "root@" + HOST + ":" + source
        else:
            target = "root@" + HOST + ":" + target
        result = subprocess.run(base("pscp.exe") + ["-scp", source, target],
                                capture_output=True, timeout=180)
    else:
        raise ValueError("script/exec/get/put")
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
