import ctypes
import ctypes.util
import errno
import os
from typing import AnyStr, Optional

from .. import ffi

libc = ffi.load_libc()

libc.syscall.argtypes = (ctypes.c_long,)
libc.syscall.restype = ctypes.c_long

SYS_OPENAT2 = None

machine = os.uname().machine
if machine == "alpha":
    SYS_OPENAT2 = 547
elif machine in ("x86_64", "i386", "i486", "i586", "i686", "sh") or any(
    machine.startswith(s) for s in ("s390", "riscv", "ppc", "parisc", "mips", "arm", "aarch64")
):
    SYS_OPENAT2 = 437

AT_FDCWD = -100

RESOLVE_NO_MAGICLINKS = 0x02
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_IN_ROOT = 0x10


class _OpenHow(ctypes.Structure):  # pylint: disable=too-few-public-methods
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


def try_open_beneath(
    path: AnyStr,
    flags: int,
    *,
    mode: int,
    dir_fd: Optional[int],
    no_symlinks: bool,
) -> Optional[int]:
    if SYS_OPENAT2 is None:
        return None

    c_path = ctypes.create_string_buffer(os.fsencode(path))

    resolve_flags = RESOLVE_NO_MAGICLINKS | RESOLVE_IN_ROOT
    if no_symlinks:
        resolve_flags |= RESOLVE_NO_SYMLINKS

    how = _OpenHow(
        flags=flags | os.O_CLOEXEC,
        mode=(
            mode if flags & os.O_CREAT == os.O_CREAT or flags & os.O_TMPFILE == os.O_TMPFILE else 0
        ),
        resolve=resolve_flags,
    )

    if dir_fd is None:
        dir_fd = AT_FDCWD
    elif not isinstance(dir_fd, int):
        raise TypeError(
            "argument should be integer or None, not {}".format(dir_fd.__class__.__name__)
        )

    while True:
        fd: int = libc.syscall(SYS_OPENAT2, dir_fd, c_path, ctypes.byref(how), ctypes.sizeof(how))

        if fd >= 0:
            return fd

        eno = ctypes.get_errno()
        if eno in (errno.E2BIG, errno.ENOSYS):
            return None
        elif eno != errno.EINTR:
            raise ffi.build_oserror(eno, os.fsdecode(path))


def try_recover_fd_path(fd: int) -> Optional[str]:
    try:
        path = os.readlink("/proc/self/fd/{}".format(fd))
    except OSError:
        return None

    return path if path.startswith("/") and not path.endswith(" (deleted)") else None
