"""Native components in an installed wheel or development checkout."""
from pathlib import Path


def native_path(filename, component):
    root = Path(__file__).parent
    bundled = root / "_bin" / filename
    return bundled if bundled.is_file() else root / component / "build" / "Release" / filename
