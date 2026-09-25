"""Check the built-in model manifest against Hugging Face and print pinned values.

  uv run python scripts/engine/pin_models.py [--source official|mirror]

For every model in src/polysub/engine/manifest.py: confirms the file exists in
the repo and prints its real size and sha256, ready to paste into the manifest
(size=..., sha256=...). Exits 1 if a file is missing.
"""
import argparse
import sys

from polysub import models
from polysub.engine import manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("official", "mirror"), default="official")
    host = models.hosts(ap.parse_args().source)[0]
    bad = 0
    for m in manifest.MODELS.values():
        try:
            size, sha = models.file_info(host, m.repo, m.file, m.revision)
        except Exception as e:  # noqa: BLE001
            print(f"✗ {m.id}: {m.repo}/{m.file}: {e}")
            bad += 1
            continue
        note = "" if abs(size - m.size) < 0.05 * size else f"  (manifest says {m.size:,})"
        print(f"✓ {m.id}: size={size:_}, sha256=\"{sha}\"{note}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
