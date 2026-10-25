"""PyInstaller entry point.

Freezing ``phytospectra_desktop/__main__.py`` directly breaks its package
relative imports (``from . import ...``), because PyInstaller runs the entry
script with no parent package.  This shim imports the package absolutely and
hands off to its ``main()``.
"""

from phytospectra_desktop.__main__ import main

if __name__ == "__main__":
    main()
