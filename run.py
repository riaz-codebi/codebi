from app import create_app
from contextlib import nullcontext
import os

app = create_app()

if __name__ == "__main__":
    from app.local_worker import LocalWorker, should_start
    # The reloader parent owns one worker. The reloader child runs Flask only.
    # Gunicorn imports run:app, so its processes never enter this block.
    if app.config['COMMERCE_HUB_ENABLED'] and not os.environ.get('COMMERCE_WORKER_DATABASE_URL'):
        app.logger.warning('Local Commerce worker is not configured; COMMERCE_WORKER_DATABASE_URL is missing.')
    with LocalWorker() if should_start(app.config, os.environ) else nullcontext():
        app.run(debug=True)
