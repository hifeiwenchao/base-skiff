"""PyInstaller entry point; absolute import supports execution as a script."""
import os
import sys

from baseskiff.native.worker import main

if __name__ == '__main__':
    arguments = sys.argv[1:]
    if not any(value == '--data-dir' or value.startswith('--data-dir=') for value in arguments):
        data_dir = os.environ.get('BASESKIFF_DATA_DIR')
        if data_dir:
            arguments = ['--data-dir', data_dir, *arguments]
    raise SystemExit(main(arguments))
