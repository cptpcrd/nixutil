# pylint: disable=invalid-name
import ctypes
import ctypes.util
import os
from typing import Union

pid_t = ctypes.c_int
uid_t = ctypes.c_uint32
gid_t = ctypes.c_uint32


_libc = None


def load_libc() -> ctypes.CDLL:
    global _libc  # pylint: disable=global-statement

    if _libc is None:
        libc_path = ctypes.util.find_library("c")
        _libc = ctypes.CDLL(libc_path, use_errno=True)

    return _libc


def build_oserror(
    eno: int,
    filename: Union[str, bytes, None] = None,
    filename2: Union[str, bytes, None] = None,
) -> OSError:
    return OSError(eno, os.strerror(eno), filename, None, filename2)
