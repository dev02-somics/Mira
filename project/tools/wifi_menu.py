"""Run the same menu on the developer PC without installing the package."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
from mira_wifi.cli import main
if __name__ == "__main__":
    sys.exit(main(["--url", "http://192.168.11.102:8088",
                   "--access-file", str(ROOT / "config/mira-wifi-access.local.txt")] + sys.argv[1:]))
