"""Copy the Hugging Face model snapshot into a self-contained cache."""

import os
import shutil
import sys
from pathlib import Path


def main(destination):
    source = Path(os.environ["USERPROFILE"]) / ".cache" / "huggingface" / "hub" / "models--adefossez--HTDemucs-6s"
    snapshot = (source / "refs" / "main").read_text(encoding="utf-8").strip()
    target = Path(destination) / "hub" / "models--adefossez--HTDemucs-6s"
    (target / "refs").mkdir(parents=True, exist_ok=True)
    (target / "refs" / "main").write_text(snapshot, encoding="utf-8")
    snapshot_dir = target / "snapshots" / snapshot
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for file in (source / "snapshots" / snapshot).iterdir():
        if file.is_file():
            shutil.copy2(file, snapshot_dir / file.name)
    print(f"Staged model snapshot {snapshot}")


if __name__ == "__main__":
    main(sys.argv[1])
