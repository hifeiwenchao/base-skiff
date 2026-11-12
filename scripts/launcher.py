"""Absolute-import entry point for the frozen desktop executable."""

import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout


def main():
    log_path = os.environ.get('BASESKIFF_SMOKE_LOG')
    if '--smoke-test' in sys.argv[1:] and log_path:
        # Windows windowed executables have no console streams. Start logging
        # before importing the app so missing packaged dependencies are visible.
        with open(log_path, 'w', encoding='utf-8', buffering=1) as smoke_log:
            with redirect_stdout(smoke_log), redirect_stderr(smoke_log):
                try:
                    from baseskiff.app import main as application_main
                    return application_main()
                except Exception:
                    traceback.print_exc()
                    # An unhandled exception would open the windowed bootloader's
                    # traceback dialog instead of promptly failing CI.
                    return 1
    from baseskiff.app import main as application_main
    return application_main()

if __name__ == "__main__":
    raise SystemExit(main())
