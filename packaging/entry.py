"""Entry point of the packaged app: no arguments -> GUI, otherwise the CLI
(the background queue worker is started as `PolySub queue run`)."""
import multiprocessing
import sys

from polysub.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    args = [a for a in sys.argv[1:] if not a.startswith("-psn")]  # Finder's process serial number
    sys.argv = [sys.argv[0]] + (args or ["gui"])
    sys.exit(main())
