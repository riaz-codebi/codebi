"""Renew a job lease independently of product processing."""
from threading import Event, Lock, Thread


class JobHeartbeat:
    def __init__(self, renew, lease_lost, interval=15):
        if interval <= 0:
            raise ValueError('Heartbeat interval must be positive.')
        self.renew = renew
        self.lease_lost = lease_lost
        self.interval = interval
        self._stop = Event()
        self._lock = Lock()
        self._progress = 0
        self._failure = None
        self._thread = Thread(target=self._run, name='commerce-job-heartbeat', daemon=True)

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join()

    def check(self):
        with self._lock:
            failure = self._failure
        if failure == 'ownership':
            raise self.lease_lost from None
        if failure:
            raise RuntimeError('Job lease renewal failed.') from None

    def update(self, progress=0):
        self.check()
        with self._lock:
            self._progress = max(self._progress, min(99, max(0, int(progress))))

    def _run(self):
        while not self._stop.wait(self.interval):
            with self._lock:
                progress = self._progress
            try:
                self.renew(progress)
            except Exception as exc:
                # Do not retain/log database exception text, URLs or credentials.
                with self._lock:
                    self._failure = 'ownership' if isinstance(exc, self.lease_lost) else 'database'
                return
