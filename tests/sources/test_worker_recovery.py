import httpx

from apps.worker import main as worker


def test_worker_recovers_from_startup_outage_then_returns_to_hourly_schedule(monkeypatch):
    waits = []

    class Shutdown:
        def is_set(self):
            return len(waits) == 2

        def set(self):
            pass

        def wait(self, seconds):
            waits.append(seconds)

    attempts = []

    def sync():
        attempts.append(True)
        if len(attempts) == 1:
            raise httpx.ConnectError('API starting')
        return {'sources': []}

    monkeypatch.setattr(worker.threading, 'Event', Shutdown)
    monkeypatch.setattr(worker.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(worker, 'sync_once', sync)
    monkeypatch.setattr('sys.argv', ['worker'])
    monkeypatch.setenv('WORKER_INTERVAL_SECONDS', '3600')
    monkeypatch.setattr(worker, 'load_dotenv', lambda **kwargs: None)
    worker.main()
    assert len(attempts) == 2
    assert waits == [60, 3600]
