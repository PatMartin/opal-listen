"""EventBus backpressure: bounded queues, disposable-vs-important drop policy, loss reporting."""

from __future__ import annotations

import asyncio
import types

import pytest

from opal_listener.service.events import EventBus


class _SyncLoop:
    """call_soon_threadsafe stand-in that runs the callback immediately."""

    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


class _ClosedLoop:
    def call_soon_threadsafe(self, fn, *args):
        raise RuntimeError("Event loop is closed")


@pytest.fixture
def bus(monkeypatch):
    """An EventBus whose subscribe() works without a running loop."""
    loop = _SyncLoop()
    fake = types.SimpleNamespace(
        Queue=asyncio.Queue,
        QueueEmpty=asyncio.QueueEmpty,
        QueueFull=asyncio.QueueFull,
        get_running_loop=lambda: loop,
    )
    monkeypatch.setattr("opal_listener.service.events.asyncio", fake)
    return EventBus(max_queue=4), loop


def _msg(event: str, disposable: bool = False) -> dict:
    return {"event": event, "data": {"n": event}, "disposable": disposable}


def _contents(queue: asyncio.Queue) -> list[str]:
    items = []
    while True:
        try:
            items.append(queue.get_nowait()["event"])
        except asyncio.QueueEmpty:
            return items


def test_publish_fanout_and_debug_filter(bus):
    b, _loop = bus
    plain = b.subscribe(debug=False)
    debug = b.subscribe(debug=True)
    b.publish("result", {"ok": True})
    b.publish("audio_level", {"dbfs": -20}, debug_only=True)
    assert _contents(plain) == ["result"]
    assert _contents(debug) == ["result", "audio_level"]


def test_full_queue_evicts_oldest_disposable_for_important(bus):
    b, _loop = bus
    q = b.subscribe()
    for i in range(4):
        b._put(q, _msg(f"level{i}", disposable=True))
    b._put(q, _msg("result"))
    assert _contents(q) == ["level1", "level2", "level3", "result"]
    assert b._dropped[q] == 1
    assert b.dropped_total == 1


def test_important_event_drops_oldest_when_all_important(bus):
    b, _loop = bus
    q = b.subscribe()
    for i in range(4):
        b._put(q, _msg(f"result{i}"))
    b._put(q, _msg("result4"))
    contents = _contents(q)
    assert contents == ["result1", "result2", "result3", "result4"]
    assert b._dropped[q] == 1


def test_disposable_never_evicts_important(bus):
    b, _loop = bus
    q = b.subscribe()
    for i in range(4):
        b._put(q, _msg(f"result{i}"))
    b._put(q, _msg("level", disposable=True))
    # incoming diagnostic is dropped; all important events survive
    assert _contents(q) == ["result0", "result1", "result2", "result3"]
    assert b._dropped[q] == 1
    assert b.dropped_total == 1


def _drain_raw(queue: asyncio.Queue) -> list[dict]:
    items = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items


def test_drop_notice_injected_with_cumulative_count(bus):
    b, _loop = bus
    q = b.subscribe()
    for i in range(4):
        b._put(q, _msg(f"level{i}", disposable=True))
    b._put(q, _msg("result1"))
    b._put(q, _msg("result2"))  # queue refills — no room for the notice yet
    assert b._dropped[q] == 2
    # consumer drains; the next event leaves room and the notice reports the cumulative total
    q.get_nowait()
    q.get_nowait()
    b._put(q, _msg("result3"))
    items = _drain_raw(q)
    notice = [m for m in items if m["event"] == "dropped"]
    assert len(notice) == 1
    assert notice[0]["data"] == {"dropped": 2}
    assert notice[0]["disposable"] is True
    assert b._reported[q] == 2
    # a second notice is not injected while the count is unchanged
    b._put(q, _msg("result4"))
    assert not any(m["event"] == "dropped" for m in _drain_raw(q))


def test_unsubscribe_clears_drop_counters(bus):
    b, _loop = bus
    q = b.subscribe()
    for _ in range(4):
        b._put(q, _msg("r"))
    b._put(q, _msg("r2"))
    assert b._dropped[q] == 1
    b.unsubscribe(q)
    assert q not in b._dropped and q not in b._reported
    assert b.subscriber_count == 0


def test_publish_survives_closed_loop(bus, monkeypatch):
    b, _loop = bus
    q = b.subscribe()
    with b._lock:
        b._subscribers[q] = (_ClosedLoop(), False)
    b.publish("result", {})  # must not raise
    assert _contents(q) == []


def test_queue_stays_bounded_under_saturation(bus):
    b, _loop = bus
    q = b.subscribe()
    for i in range(50):
        b._put(q, _msg(f"level{i}", disposable=True))
    assert q.qsize() == 4
    assert b._dropped[q] == 46
    assert b.dropped_total == 46
