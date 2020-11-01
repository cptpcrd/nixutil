import sys

__all__ = ["try_recover_fd_path"]

if sys.platform.startswith("linux"):
    from .linux import try_open_beneath, try_recover_fd_path  # noqa

    __all__.append("try_open_beneath")
elif sys.platform.startswith("freebsd"):
    from .freebsd import try_recover_fd_path
elif sys.platform.startswith("darwin"):
    from .macos import try_recover_fd_path
