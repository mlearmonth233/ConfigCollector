"""PyInstaller entry point for the Packrat desktop build.

Nothing lives here on purpose: the behaviour is app.desktop, which also
runs unbundled as `python -m app.desktop` from backend/.
"""

import multiprocessing
import sys

multiprocessing.freeze_support()

from app.desktop.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
