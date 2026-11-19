"""Stop the Guitar and Bass AI Smart Remover server running on port 8765.

writtenBy('Closegamer', 2026)
"""

import ctypes
import subprocess
import sys


def main():
    output = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
    pids = []
    for line in output.splitlines():
        if "127.0.0.1:8765" in line and "LISTENING" in line:
            pid = line.split()[-1]
            if pid not in pids:
                pids.append(pid)
    for pid in pids:
        subprocess.run(
            ["taskkill", "/f", "/pid", pid],
            creationflags=0x08000000,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    message = "Guitar and Bass AI Smart Remover stopped." if pids else "Guitar and Bass AI Smart Remover is not running."
    if "--silent" not in sys.argv:
        ctypes.windll.user32.MessageBoxW(0, message, "Guitar and Bass AI Smart Remover", 0x40)


if __name__ == "__main__":
    main()
