from abc import ABC, abstractmethod


class AIMPMessageHandlerInf(ABC):
    @abstractmethod
    async def receive_message(self, message):
        pass

