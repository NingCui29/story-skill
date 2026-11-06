import threading
import unittest
from types import SimpleNamespace
from test_chrome_worker_controller import worker

class ManualRetryTests(unittest.TestCase):
    def test_retry_keeps_unclean_worker_blocked(self):
        for done, confirmed, error in [(False, True, None), (True, False, None), (True, True, 'cleanup')]:
            c = worker.ChromeWorkerController(SimpleNamespace())
            event = threading.Event()
            if done: event.set()
            c._workers['owned'] = {'done': event, 'worker_done_confirmed': confirmed, 'cleanup_error': error}
            c._connection_suspension = {'retry_blocked': True}
            with self.assertRaises(worker.WorkerError): c.retry_connection()
            self.assertTrue(c.connection_suspension()['retry_blocked'])

    def test_retry_after_cleanup_does_not_claim_readiness_or_launch(self):
        c = worker.ChromeWorkerController(SimpleNamespace())
        event = threading.Event(); event.set()
        c._workers['owned'] = {'done': event, 'worker_done_confirmed': True}
        c._connection_suspension = {'retry_blocked': True}
        self.assertEqual(c.retry_connection(), {'ok': True, 'retry_allowed': True, 'ready': False})
        self.assertIsNone(c.connection_suspension())
        self.assertEqual(len(c._workers), 1)
