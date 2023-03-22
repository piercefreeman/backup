from abc import ABC, abstractmethod


class BaseBackend(ABC):
    @abstractmethod
    def exists(self, path: str):
        pass

    @abstractmethod
    def write_file(self, path: str, data: bytes):
        pass
