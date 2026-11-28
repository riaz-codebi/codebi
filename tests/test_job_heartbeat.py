import time
import unittest
from threading import Event
from unittest.mock import Mock

from commerce_hub.job_heartbeat import JobHeartbeat
from commerce_hub.worker import LeaseLost


class JobHeartbeatTests(unittest.TestCase):
    def test_frequent_product_updates_do_not_make_database_calls_or_delay_finish(self):
        renew = Mock()
        heartbeat = JobHeartbeat(renew, LeaseLost, interval=60)
        heartbeat.start()
        try:
            for progress in range(100):
                heartbeat.update(progress)
        finally:
            started = time.monotonic()
            heartbeat.close()
        self.assertLess(time.monotonic() - started, 1)
        renew.assert_not_called()

    def test_renews_during_work_without_product_callbacks(self):
        renewed = Event()
        seen = []
        heartbeat = JobHeartbeat(lambda progress: (seen.append(progress), renewed.set()), LeaseLost, interval=.01)
        heartbeat.update(35)
        heartbeat.start()
        try:
            self.assertTrue(renewed.wait(2))
            self.assertEqual(seen[0], 35)
        finally:
            heartbeat.close()

    def failure_heartbeat(self, error):
        observed = Event()
        def renew(_progress):
            observed.set()
            raise error
        heartbeat = JobHeartbeat(renew, LeaseLost, interval=.01)
        heartbeat.start()
        self.assertTrue(observed.wait(2))
        heartbeat.close()  # Join ensures the failure has been recorded.
        return heartbeat

    def test_lost_ownership_stops_further_product_processing(self):
        heartbeat = self.failure_heartbeat(LeaseLost())
        with self.assertRaises(LeaseLost):
            heartbeat.update(50)

    def test_database_failure_is_sanitized_and_fails_closed(self):
        heartbeat = self.failure_heartbeat(RuntimeError('private-database-connection-detail'))
        with self.assertRaisesRegex(RuntimeError, '^Job lease renewal failed\\.$'):
            heartbeat.check()


if __name__ == '__main__':
    unittest.main()
