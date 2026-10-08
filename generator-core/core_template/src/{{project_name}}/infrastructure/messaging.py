"""Broker-agnostic messaging facade.

Concrete brokers (RabbitMQ, Kafka, ...) are contributed by modules and build
this facade, so application code depends on a single ``publish``/``subscribe``
API instead of a transport-specific client. The facade itself has no
third-party dependencies.
"""

from collections.abc import Awaitable, Callable
from typing import Any

MessageHandler = Callable[[Any], Awaitable[None]]


class MessageBroker:
    """Transport-agnostic async message broker facade.

    Wraps any FastStream broker (``RabbitBroker``, ``KafkaBroker``, ...) and
    exposes a single API: ``topic`` maps to a RabbitMQ queue/routing key or a
    Kafka topic, so the service layer never imports a transport-specific type.

    ``start``/``stop`` are idempotent and safe even when the broker could not be
    reached at startup.
    """

    def __init__(self, broker: Any) -> None:
        self._broker = broker
        self._started = False

    @property
    def raw(self) -> Any:
        """The underlying broker, for advanced/transport-specific use."""
        return self._broker

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        await self._broker.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self._broker.stop()
        self._started = False

    async def publish(self, topic: str, message: Any, **kwargs: Any) -> None:
        await self._broker.publish(message, topic, **kwargs)

    def subscribe(self, topic: str, **kwargs: Any) -> Callable[[MessageHandler], MessageHandler]:
        return self._broker.subscriber(topic, **kwargs)
