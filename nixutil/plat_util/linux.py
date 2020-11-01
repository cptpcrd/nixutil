import ctypes
import ctypes.util
import dataclasses
import enum
import errno
import os
from typing import AnyStr, Optional, Union

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


class ResolveFlags(enum.Flag):
    EMPTY = 0

    NO_XDEV = 0x01
    NO_MAGICLINKS = 0x02
    NO_SYMLINKS = 0x04
    BENEATH = 0x08
    IN_ROOT = 0x10


@dataclasses.dataclass
class OpenHow:
    flags: int
    mode: Optional[int] = None
    resolve: ResolveFlags = ResolveFlags.EMPTY

    @property
    def _raw_mode(self) -> int:
        if self.mode is not None:
            return self.mode
        else:
            return (
                0o777
                if self.flags & os.O_CREAT == os.O_CREAT
                or self.flags & os.O_TMPFILE == os.O_TMPFILE
                else 0
            )

    def _build_raw(self, *, inheritable: bool) -> "_RawOpenHow":
        flags = self.flags
        if not inheritable:
            flags |= os.O_CLOEXEC

        return _RawOpenHow(flags=flags, mode=self._raw_mode, resolve=self.resolve.value)


class _RawOpenHow(ctypes.Structure):  # pylint: disable=too-few-public-methods
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


def openat2(
    path: Union[AnyStr, "os.PathLike[AnyStr]"],
    how: OpenHow,
    *,
    dir_fd: Optional[int] = None,
    inheritable: bool = False,
) -> int:
    """Call the `openat2()` syscall.

    Raises:
        TypeError: If one of the arguments is of an invalid type.
        OSError: If an error was encountered while opening the file, or if the
            running kernel either does not support the `openat2()` syscall or does
            not support some of the fields passed in `how`.

    """

    if SYS_OPENAT2 is None:
        raise ffi.build_oserror(errno.ENOSYS, os.fsdecode(path))

    path = os.fspath(path)
    c_path = ctypes.create_string_buffer(os.fsencode(path))

    raw_how = how._build_raw(inheritable=inheritable)  # pylint: disable=protected-access

    if dir_fd is None:
        dir_fd = AT_FDCWD
    elif not isinstance(dir_fd, int):
        raise TypeError(
            "argument should be integer or None, not {}".format(dir_fd.__class__.__name__)
        )

    while True:
        fd: int = libc.syscall(
            SYS_OPENAT2, dir_fd, c_path, ctypes.byref(raw_how), ctypes.sizeof(raw_how)
        )

        if fd >= 0:
            return fd

        eno = ctypes.get_errno()
        if eno != errno.EINTR:
            raise ffi.build_oserror(eno, path)


def try_recover_fd_path(fd: int) -> Optional[str]:
    try:
        path = os.readlink("/proc/self/fd/{}".format(fd))
    except OSError:
        return None

    return path if path.startswith("/") and not path.endswith(" (deleted)") else None
