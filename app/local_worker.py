"""Own one Commerce worker for the local development server, never Gunicorn."""
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread

log = logging.getLogger(__name__)


def should_start(config, environ):
    return (config.get('COMMERCE_HUB_ENABLED', False)
            and environ.get('WERKZEUG_RUN_MAIN') != 'true'
            and bool(environ.get('COMMERCE_WORKER_DATABASE_URL')))


def init_flask_cli_worker(app):
    """Attach the supervisor only to Flask's built-in development run command."""
    if app.testing or os.environ.get('FLASK_RUN_FROM_CLI') != 'true':
        return
    import click
    from flask.cli import run_command
    context = click.get_current_context(silent=True)
    if context is None or context.command is not run_command:
        return
    if not should_start(app.config, os.environ) or 'commerce_local_worker' in app.extensions:
        return
    worker = LocalWorker()
    worker.__enter__()
    app.extensions['commerce_local_worker'] = worker
    # Click closes the run-command context on normal exit and Ctrl+C.
    context.call_on_close(lambda: worker.__exit__())


class LocalWorker:
    def __init__(self, root=None, retry_seconds=5, poll_seconds=1):
        self.root = Path(root) if root else Path(__file__).resolve().parents[1]
        self.retry_seconds = retry_seconds
        self.poll_seconds = poll_seconds
        self.stopping = Event()
        self.thread = Thread(target=self.run, name='commerce-local-worker', daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stopping.set()
        self.thread.join(timeout=10)

    def source_stamp(self):
        files = list((self.root / 'commerce_hub').rglob('*.py'))
        model = self.root / 'app' / 'models.py'
        if model.exists():
            files.append(model)
        return tuple(sorted((str(path.relative_to(self.root)),path.stat().st_mtime_ns) for path in files))

    def run(self):
        while not self.stopping.is_set():
            process = None
            code_changed = False
            try:
                stamp = self.source_stamp()
                # The child loads the ignored .env itself. Never put secrets in argv.
                options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
                process = subprocess.Popen([sys.executable, '-B', '-m', 'commerce_hub.worker'],
                    cwd=str(self.root), stdin=subprocess.DEVNULL, **options)
                log.warning('Local Commerce worker started; queued jobs will be processed automatically.')
                while not self.stopping.wait(self.poll_seconds):
                    if process.poll() is not None:
                        break
                    if self.source_stamp() != stamp:
                        code_changed = True
                        break
            except OSError as exc:
                log.warning('Local Commerce worker could not start (%s).', type(exc).__name__)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            if not self.stopping.is_set():
                if code_changed:
                    log.warning('Commerce Python files changed; reloading the local worker.')
                else:
                    log.warning('Local Commerce worker stopped; retrying in %s seconds.', self.retry_seconds)
                    self.stopping.wait(self.retry_seconds)
