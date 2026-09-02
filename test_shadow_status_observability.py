from types import SimpleNamespace

import crypto_quote_readiness_sampler as sampler


class Worker:
    def __init__(self):
        self.messages = []
        self.log = SimpleNamespace(info=self._info)

    def _info(self, fmt, *args):
        self.messages.append(fmt % args)


def test_shadow_status_logs_zero_capture_reason_on_cadence(monkeypatch):
    worker = Worker()
    sampler._LAST_SHADOW_STATUS_LOG = 0.0
    monkeypatch.setattr(sampler.time, "monotonic", lambda: 100.0)
    result = {
        "status": "PASS",
        "capture": {"status": "NO_NEW_REFERENCE_BARS", "captured": 0, "skipped": 0},
        "evaluate": {"status": "NO_DUE_ORDERS", "evaluated": 0},
    }

    sampler._emit_shadow_status(worker, result)

    assert len(worker.messages) == 1
    assert "PASSIVE SHADOW STATUS" in worker.messages[0]
    assert "capture_status=NO_NEW_REFERENCE_BARS" in worker.messages[0]
    assert "broker_submission=NONE" in worker.messages[0]


def test_shadow_status_is_bounded_but_capture_is_immediately_visible(monkeypatch):
    worker = Worker()
    sampler._LAST_SHADOW_STATUS_LOG = 100.0
    monkeypatch.setattr(sampler.time, "monotonic", lambda: 110.0)
    idle = {
        "status": "PASS",
        "capture": {"status": "NO_NEW_REFERENCE_BARS", "captured": 0},
        "evaluate": {"status": "COOLDOWN", "evaluated": 0},
    }
    sampler._emit_shadow_status(worker, idle)
    assert worker.messages == []

    captured = {
        "status": "PASS",
        "capture": {"status": "PASS", "captured": 2, "skipped": 0},
        "evaluate": {"status": "COOLDOWN", "evaluated": 0},
    }
    sampler._emit_shadow_status(worker, captured)
    assert len(worker.messages) == 1
    assert "captured=2" in worker.messages[0]
