"""Start Guitar and Bass AI Smart Remover and open it in the default browser.

writtenBy('Closegamer', 2026)
"""

import ctypes
import os
import shutil
import socket
import subprocess
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
PYTHON = ROOT / "python" / "pythonw.exe"
DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GuitarAndBassAISmartRemover"
MODELS = DATA / "models"
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"


def server_up():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.4):
            return True
    except OSError:
        return False


def show_error(message):
    ctypes.windll.user32.MessageBoxW(0, message, "Guitar and Bass AI Smart Remover", 0x10)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if not (MODELS / "hub" / "models--adefossez--HTDemucs-6s" / "refs" / "main").exists():
        try:
            shutil.copytree(ROOT / "models", MODELS, dirs_exist_ok=True)
        except OSError as exc:
            show_error(f"Could not install the separation model.\n\n{exc}")
            return

    if not server_up():
        env = os.environ.copy()
        env["GH_DATA"] = str(DATA)
        env["HF_HOME"] = str(MODELS)
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        flags = 0x08000000 if os.name == "nt" else 0
        with (DATA / "server.log").open("ab") as log:
            subprocess.Popen(
                [str(PYTHON), str(APP / "server.py")],
                cwd=str(APP),
                env=env,
                stdout=log,
                stderr=log,
                creationflags=flags,
            )
        for _ in range(120):
            if server_up():
                break
            time.sleep(0.5)
        else:
            show_error(f"Guitar and Bass AI Smart Remover could not start.\n\nSee:\n{DATA / 'server.log'}")
            return

    webbrowser.open(URL)


if __name__ == "__main__":
    main()
