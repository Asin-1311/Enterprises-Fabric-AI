"""EventBus on Kafka (full profile; single broker in KRaft mode in deploy/compose.yaml).

Events are JSON. Publishing waits until the broker has the event (idempotent producer,
acks=all), so a change is never silently lost. Consumers commit offsets only when told
to, which gives at-least-once delivery: the ingestion worker (M12) processes an event,
then commits, and must therefore be idempotent (it is keyed by content hash).
"""

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# How long a consumer may go between polls before Kafka removes it from its group. The
# ingestion worker applies a whole batch (up to 50 events, possibly large ICAI PDFs to
# chunk and embed) between polls, which can take longer than Kafka's default 5 minutes.
MAX_POLL_INTERVAL_MS = 30 * 60 * 1000


class KafkaEventBus:
    def __init__(self, bootstrap: str, partitions: int = 1) -> None:
        from confluent_kafka import Producer

        self.bootstrap, self.partitions = bootstrap, partitions
        self._producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True,
                                   "acks": "all"})
        self._known_topics: set[str] = set()

    def _ensure_topic(self, topic: str) -> None:
        if topic in self._known_topics:
            return
        from confluent_kafka.admin import AdminClient, NewTopic

        admin = AdminClient({"bootstrap.servers": self.bootstrap})
        if topic not in admin.list_topics(timeout=10).topics:
            for future in admin.create_topics([NewTopic(topic, self.partitions, 1)]).values():
                try:
                    future.result(10)
                except Exception as exc:  # created by someone else meanwhile is fine
                    if "TOPIC_ALREADY_EXISTS" not in str(exc):
                        raise
        self._known_topics.add(topic)

    def publish(self, topic: str, key: str, event: dict[str, Any]) -> None:
        self._ensure_topic(topic)
        errors: list[str] = []

        def delivered(err, _msg) -> None:
            if err is not None:
                errors.append(str(err))

        self._producer.produce(topic, key=key.encode(), value=json.dumps(event).encode(),
                               on_delivery=delivered)
        self._producer.flush(10)
        if errors:
            raise RuntimeError(f"Kafka did not accept the event: {errors[0]}")

    def subscribe(self, topic: str, group: str) -> "KafkaSubscription":
        self._ensure_topic(topic)
        return KafkaSubscription(self.bootstrap, topic, group)

    def close(self) -> None:
        self._producer.flush(10)


class KafkaSubscription:
    def __init__(self, bootstrap: str, topic: str, group: str) -> None:
        from confluent_kafka import Consumer

        self._consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": group,
                                   "auto.offset.reset": "earliest", "enable.auto.commit": False,
                                   "max.poll.interval.ms": MAX_POLL_INTERVAL_MS})
        self._consumer.subscribe([topic])
        self._polled = False

    def poll(self, timeout_seconds: float) -> dict[str, Any] | None:
        msg = self._consumer.poll(timeout_seconds)
        if msg is None:
            return None
        if msg.error():
            from confluent_kafka import KafkaError

            if msg.error().code() == KafkaError._MAX_POLL_EXCEEDED:
                # Too long between polls (a long batch, or the machine paused): Kafka took
                # this consumer out of its group. The next poll rejoins it, and anything
                # not committed is delivered again, so this is not an error (M15b).
                log.warning("Kafka consumer rejoining its group after a long pause: %s", msg.error())
                return None
            raise RuntimeError(f"Kafka consumer error: {msg.error()}")
        self._polled = True
        return json.loads(msg.value())

    def commit(self) -> None:
        if self._polled:
            self._consumer.commit(asynchronous=False)

    def rewind(self) -> None:
        """Seek every assigned partition back to its committed offset (or its first
        offset if nothing was committed yet), so uncommitted events come again."""
        from confluent_kafka import TopicPartition

        for tp in self._consumer.committed(self._consumer.assignment(), timeout=10):
            offset = tp.offset
            if offset < 0:  # no commit yet for this group: start of the partition
                offset = self._consumer.get_watermark_offsets(
                    TopicPartition(tp.topic, tp.partition), timeout=10)[0]
            self._consumer.seek(TopicPartition(tp.topic, tp.partition, offset))

    def close(self) -> None:
        self._consumer.close()
