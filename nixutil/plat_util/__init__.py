import sys

__all__ = ["try_recover_fd_path"]

if sys.platform.startswith("linux"):
    from .linux import OpenHow, ResolveFlags, openat2, try_recover_fd_path  # noqa

    __all__.extend(["try_recover_fd_path", "openat2", "ResolveFlags", "OpenHow"])
elif sys.platform.startswith("freebsd"):
    from .freebsd import try_recover_fd_path
elif sys.platform.startswith("darwin"):
    from .macos import try_recover_fd_path
