"""Compatibility entry point; install the Python package first."""
import sys


def main():
    try:
        from daslang_jupyter.cli import main as install
    except ImportError:
        raise SystemExit("Install the package first: python -m pip install .")
    install(["install", *sys.argv[1:]])


if __name__ == "__main__":
    main()
