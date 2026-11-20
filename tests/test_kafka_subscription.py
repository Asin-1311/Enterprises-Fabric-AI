"""The Kafka subscription's error handling, without a broker (M15b).

A long pause between polls (a slow batch, or a paused machine) makes Kafka remove the
consumer from its group (_MAX_POLL_EXCEEDED). That is recoverable: the next poll rejoins
and uncommitted events come again. Every other consumer error still raises.
"""

import pytest

confluent_kafka = pytest.importorskip("confluent_kafka")  # full-profile dependency

from app.adapters.full.events import MAX_POLL_INTERVAL_MS, KafkaSubscription  # noqa: E402


class FakeMessage:
    def __init__(self, error=None, value=b'{"n": 1}') -> None:
        self._error, self._value = error, value

    def error(self):
        return self._error

    def value(self):
        return self._value


class FakeConsumer:
    def __init__(self, messages) -> None:
        self.messages = list(messages)

    def poll(self, timeout):
        return self.messages.pop(0) if self.messages else None


def subscription(*messages) -> KafkaSubscription:
    sub = KafkaSubscription.__new__(KafkaSubscription)  # no broker: skip __init__
    sub._consumer, sub._polled = FakeConsumer(messages), False
    return sub


def test_a_long_pause_between_polls_is_recovered_not_raised() -> None:
    paused = confluent_kafka.KafkaError(confluent_kafka.KafkaError._MAX_POLL_EXCEEDED)
    sub = subscription(FakeMessage(error=paused), FakeMessage())
    assert sub.poll(0.1) is None      # warned, not raised
    assert sub.poll(0.1) == {"n": 1}  # the consumer carries on
    assert MAX_POLL_INTERVAL_MS == 30 * 60 * 1000


def test_other_consumer_errors_still_raise() -> None:
    broken = confluent_kafka.KafkaError(confluent_kafka.KafkaError._TRANSPORT)
    with pytest.raises(RuntimeError, match="Kafka consumer error"):
        subscription(FakeMessage(error=broken)).poll(0.1)
