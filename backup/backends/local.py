from os.path import exists

from backup.backends.base import BaseBackend


class LocalBackend(BaseBackend):
    def exists(self, path: str):
        return exists(path)

    def write_file(self, path: str, data: bytes):
        with open(path, "wb") as file:
            file.write(data)
