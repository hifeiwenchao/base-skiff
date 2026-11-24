"""Application entry point and deterministic native build smoke checks."""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(prog='BaseSkiff', description='Your data, in one thoughtful workspace.')
    parser.add_argument('--smoke-test', action='store_true', help='Run a self-contained startup and SQLite check, then exit.')
    parser.add_argument('--screenshot', type=Path, help='Save a screenshot of the sample workspace, then exit.')
    parser.add_argument('--language', choices=['zh', 'en'])
    parser.add_argument('--data-dir', type=Path, help='Use an alternative local application data directory.')
    args = parser.parse_args()
    if args.smoke_test or args.screenshot:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QThreadPool, QTimer
    from PySide6.QtWidgets import QApplication

    from baseskiff.service import DatabaseService
    from baseskiff.ui.window import MainWindow
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setStyle('Fusion')
    application.setApplicationName('BaseSkiff')
    application.setApplicationVersion('0.1.0')
    application.setOrganizationName('BaseSkiff')
    temporary = tempfile.TemporaryDirectory(prefix='baseskiff-smoke-') if args.smoke_test or args.screenshot else None
    try:
        service = DatabaseService(args.data_dir or (temporary.name if temporary else None))
        service.ephemeral = bool(args.smoke_test or args.screenshot)
        window = MainWindow(service, language=args.language)
        window.show()
        if args.smoke_test or args.screenshot:
            status = {'code': 1}
            def verify():
                if window.data_table.rowCount() and not window.busy:
                    try:
                        if window.current_table != 'customers':
                            raise RuntimeError(f'Expected customers workspace, got {window.current_table!r}')
                        if window.total_rows != 128:
                            raise RuntimeError(f'Expected workspace row count 128, got {window.total_rows!r}')
                        result = service.execute(window.current_connection.id, 'SELECT COUNT(*) AS total FROM customers')
                        if not result.rows or result.rows[0].get('total') != 128:
                            raise RuntimeError(f'SQLite customers count must be 128, got {result.rows!r}')
                        # External drivers, SSH crypto and durable local credentials
                        # must work inside frozen builds.
                        if args.smoke_test:
                            for name in ('pymysql', 'psycopg', 'pymongo', 'redis', 'oracledb', 'paramiko'):
                                importlib.import_module(name)
                            if importlib.import_module('psycopg').pq.version() <= 0:
                                raise RuntimeError('PostgreSQL libpq returned an invalid version')
                            ssh = importlib.import_module('baseskiff.ssh')
                            key = importlib.import_module('paramiko').RSAKey.generate(2048)
                            if not ssh.fingerprint(key).startswith('SHA256:'):
                                raise RuntimeError('SSH key fingerprint must use SHA256')
                            codec = importlib.import_module('baseskiff.redis_codec')
                            packed = importlib.import_module('msgpack').packb(
                                {'name': 'BaseSkiff', 'binary': b'\xff\x00'}, use_bin_type=True)
                            if codec.preview_value(packed).format != 'msgpack':
                                raise RuntimeError('Redis MessagePack preview did not detect the binary payload')
                            if codec.raw_bytes(packed) != packed:
                                raise RuntimeError('Redis raw bytes did not preserve the binary payload')
                            from baseskiff.models import Connection
                            credential_dir = Path(temporary.name) / 'credential-check'
                            credential_service = DatabaseService(credential_dir)
                            candidate = Connection('Local credential check', kind='MySQL',
                                                   password='smoke-database-secret',
                                                   ssh_password='smoke-ssh-secret',
                                                   ssh_passphrase='smoke-key-passphrase')
                            try:
                                credential_service.save_connection(candidate)
                                reopened = DatabaseService(credential_dir)
                                restored = reopened._connection(candidate.id)
                                for field in ('password', 'ssh_password', 'ssh_passphrase'):
                                    if getattr(restored, field) != getattr(candidate, field):
                                        raise RuntimeError('Local credentials were not restored after reopening')
                                    if getattr(candidate, field).encode() in reopened.metadata_path.read_bytes():
                                        raise RuntimeError('Local credentials were written without encryption')
                            finally:
                                credential_service.delete_connection(candidate.id)
                        if args.screenshot:
                            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                            if not window.grab().save(str(args.screenshot)):
                                raise RuntimeError(f'Could not save screenshot to {args.screenshot}')
                        print('BaseSkiff: native workspace and SQLite checks passed' + ('; packaged drivers, Redis codecs and local credentials verified.' if args.smoke_test else '.'), flush=True)
                        status['code'] = 0
                    except Exception as exc:
                        print(f'BaseSkiff smoke test failed: {exc}', file=sys.stderr, flush=True)
                    poll.stop()
                    window.close()
                    application.quit()
            poll = QTimer(window)
            poll.setInterval(100)
            poll.timeout.connect(verify)
            poll.start()
            def timeout():
                if status['code']:
                    print('BaseSkiff smoke test timed out.', file=sys.stderr, flush=True)
                    window.close()
                    application.quit()
            QTimer.singleShot(25000, timeout)
            application.exec()
            QThreadPool.globalInstance().waitForDone(15000)
            return status['code']
        return application.exec()
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == '__main__':
    raise SystemExit(main())
