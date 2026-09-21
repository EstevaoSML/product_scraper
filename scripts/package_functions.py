"""Stage only Azure Functions source files. Publish this folder with remote build."""
from datetime import datetime, timezone
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def package():
    destination = ROOT / "outputs" / ("functions-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    destination.mkdir(parents=True)
    (destination / "app").mkdir()
    for source in (ROOT / "app").glob("*.py"):
        shutil.copyfile(source, destination / "app" / source.name)
    for name in ("requirements.txt", "constraints.txt"):
        shutil.copyfile(ROOT / name, destination / name)
    for source in (ROOT / "deploy/functions").iterdir():
        if source.is_file():
            shutil.copyfile(source, destination / source.name)
    print(destination)
    return destination


if __name__ == "__main__":
    package()
