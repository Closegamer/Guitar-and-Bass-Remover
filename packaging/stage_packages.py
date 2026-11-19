"""Copy only the installed Python packages the app imports into the staged runtime.

The build used to copy the whole development site-packages, which carried
unrelated tools (OpenCV, Playwright, Jupyter) into the installer. This resolves
the dependency closure of the app's imports and copies just those packages.

writtenBy('Closegamer', 2026)
"""

import importlib.metadata as md
import shutil
import sys
from pathlib import Path

from packaging.requirements import Requirement

ROOTS = [
    "demucs", "basic-pitch", "librosa", "soundfile", "onnxruntime",
    "torch", "torchaudio", "lameenc", "pyyaml",
]
# Only needed to build or export models, never at runtime.
SKIP = ("torch/include/",)


def main():
    target = Path(sys.argv[1])
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    seen = {}
    queue = list(ROOTS)
    while queue:
        name = queue.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        seen[key] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            queue.append(req.name)

    for dist in seen.values():
        for rel in dist.files or []:
            rel = Path(rel)
            posix = rel.as_posix()
            if any(posix.startswith(prefix) for prefix in SKIP):
                continue
            source = dist.locate_file(rel)
            if not source.is_file():
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)

    print(f"staged {len(seen)} packages into {target}")


if __name__ == "__main__":
    main()
