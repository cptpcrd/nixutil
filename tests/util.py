import contextlib
import os
from typing import Generator, Union


@contextlib.contextmanager
def managed_open(
    path: Union[str, bytes, "os.PathLike[str]", "os.PathLike[bytes]"], flags: int
) -> Generator[int, None, None]:
    fd = os.open(path, flags)

    try:
        yield fd
    finally:
        os.close(fd)
