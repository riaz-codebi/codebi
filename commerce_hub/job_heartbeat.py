"""Renew a job lease independently of product processing."""
from threading import Event, Lock, Thread

class HeartbeatUnavailable(RuntimeError):
    def __init__(self,details):
        super().__init__('Job lease renewal failed.')
        self.safe_diagnostic={'component':'lease_renewal',**details}


class JobHeartbeat:
    def __init__(self, renew, lease_lost, interval=15,max_failures=2):
        if interval <= 0:
            raise ValueError('Heartbeat interval must be positive.')
        if max_failures<1:raise ValueError('Heartbeat failure limit must be positive.')
        self.renew = renew
        self.lease_lost = lease_lost
        self.interval = interval
        self.max_failures=max_failures
        self._details={}
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
            raise HeartbeatUnavailable(dict(self._details)) from None

    def update(self, progress=0):
        self.check()
        with self._lock:
            self._progress = max(self._progress, min(99, max(0, int(progress))))

    def _run(self):
        delay=self.interval;failures=0
        while not self._stop.wait(delay):
            with self._lock:
                progress = self._progress
            try:
                self.renew(progress)
            except Exception as exc:
                # Do not retain/log database exception text, URLs or credentials.
                details={'exception_type':type(exc).__name__}
                state=getattr(getattr(exc,'orig',None),'sqlstate',None)
                if isinstance(state,str) and len(state)==5 and state.isalnum():details['sqlstate']=state
                with self._lock:
                    self._details=details
                    if isinstance(exc,self.lease_lost):self._failure='ownership';return
                    failures+=1
                    if failures>=self.max_failures:self._failure='database';return
                delay=min(5,self.interval)
            else:
                failures=0;delay=self.interval
