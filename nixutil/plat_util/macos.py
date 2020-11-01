import fcntl
import os
from typing import Optional

PATH_MAX = 1024

F_GETPATH = getattr(fcntl, "F_GETPATH", 50)


def try_recover_fd_path(fd: int) -> Optional[str]:
    try:
        res = fcntl.fcntl(fd, F_GETPATH, b"\0" * PATH_MAX)
    except OSError:
        return None

    return os.fsdecode(res[: res.index(0)]) or None
