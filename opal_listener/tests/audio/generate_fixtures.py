"""Regenerate audio fixtures from manifest.yaml (requires espeak and sox).

python -m opal_listener.tests.audio.generate_fixtures
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


def load_manifest() -> list[dict]:
    return yaml.safe_load((HERE / "manifest.yaml").read_text())["fixtures"]


def synthesize(text: str, voice: str, speed: int, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.wav"
        subprocess.run(["espeak", "-v", voice, "-s", str(speed), "-w", str(raw), text], check=True)
        # 16 kHz mono 16-bit, 0.4 s leading and 1.0 s trailing silence (lets VAD detect the end).
        subprocess.run(
            ["sox", str(raw), "-r", "16000", "-c", "1", "-b", "16", str(target), "pad", "0.4", "1.0"], check=True
        )


def main() -> None:
    for tool in ("espeak", "sox"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required to generate fixtures")
    FIXTURES.mkdir(exist_ok=True)
    for entry in load_manifest():
        target = FIXTURES / entry["file"]
        synthesize(entry["text"], entry.get("voice", "en-us"), int(entry.get("speed", 150)), target)
        print(f"wrote {target.relative_to(HERE)}")


if __name__ == "__main__":
    main()
