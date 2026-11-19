"""EventBus on Kafka (full profile; single broker in KRaft mode in deploy/compose.yaml).

Events are JSON. Publishing waits until the broker has the event (idempotent producer,
acks=all), so a change is never silently lost. Consumers commit offsets only when told
to, which gives at-least-once delivery: the ingestion worker (M12) processes an event,
then commits, and must therefore be idempotent (it is keyed by content hash).
"""

import json
from typing import Any


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
                                   "auto.offset.reset": "earliest", "enable.auto.commit": False})
        self._consumer.subscribe([topic])
        self._polled = False

    def poll(self, timeout_seconds: float) -> dict[str, Any] | None:
        msg = self._consumer.poll(timeout_seconds)
        if msg is None:
            return None
        if msg.error():
            raise RuntimeError(f"Kafka consumer error: {msg.error()}")
        self._polled = True
        return json.loads(msg.value())

    def commit(self) -> None:
        if self._polled:
            self._consumer.commit(asynchronous=False)

    def close(self) -> None:
        self._consumer.close()
