"""Entry point for the PyInstaller build (the package itself uses relative imports)."""
import sys

from karatap.__main__ import main

sys.exit(main())
