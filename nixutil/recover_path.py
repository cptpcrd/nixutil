import errno
import os
import stat

from . import ffi, plat_util


def recover_fd_path(fd: int) -> str:
    """
    Attempt to determine the path that the file descriptor ``fd`` is open to.

    Note: This only works reliably on directories! On some platforms (Linux, macOS, and FreeBSD) it
    may also work for regular files, but this is not guaranteed.

    If a file descriptor is passed that is open to a file with a disallowed file type (such as a
    pipe, socket, or a regular file on platforms where that isn't supported), an ``OSError`` with
    ``errno`` set to ``ENOTSUP`` will be raised.

    If the file to which ``fd`` is open has been deleted, the following behaviors are allowed:
    - Returning the path to which the file was open before it was deleted
    - Raising a ``FileNotFoundError``
    - Raising an ``OSError`` with ``errno=ENOTSUP`` (if ``fd`` is open to a regular file)
    """

    # Some of the platform-specific methods (FreeBSD in particular) might silently return erroneous
    # results for values < 0
    if fd < 0:
        raise ffi.build_oserror(errno.EBADF)

    if hasattr(plat_util, "try_recover_fd_path"):
        path = plat_util.try_recover_fd_path(fd)
        if path is not None:
            return path

    orig_fd = fd
    orig_stat = os.fstat(orig_fd)

    # Without OS-specific help, we can only handle directories.
    if not stat.S_ISDIR(orig_stat.st_mode):
        raise ffi.build_oserror(errno.ENOTSUP)

    sub_fd = orig_fd
    sub_stat = orig_stat

    built_path = ""

    while True:
        try:
            parent_fd = os.open("..", os.O_RDONLY, dir_fd=sub_fd)
        finally:
            if sub_fd != orig_fd:
                os.close(sub_fd)

        try:
            parent_stat = os.fstat(parent_fd)
        except OSError:
            os.close(parent_fd)
            raise

        if os.path.samestat(sub_stat, parent_stat):
            # Opening ".." returned the same directory; probably means we found "/"
            os.close(parent_fd)

            if os.path.samestat(parent_stat, os.stat("/")):
                # We made it to the filesystem root
                return "/" + built_path
            else:
                # Maybe we're in a chroot and the file descriptor is open to a file outside the
                # chroot?
                raise ffi.build_oserror(errno.ENOENT)

        try:
            with os.scandir(parent_fd) as parent_dir_it:
                for entry in parent_dir_it:
                    # We can't check entry.inode() for speedups because that doesn't work properly
                    # if the file that was pointed to by `sub_fd` is a mountpoint.

                    try:
                        if not entry.is_dir(follow_symlinks=False) or not os.path.samestat(
                            sub_stat, entry.stat(follow_symlinks=False)
                        ):
                            continue
                    except OSError:
                        # Yes, errors could occur when trying to stat() it. For example, trying to
                        # stat() the root directory of a FUSE filesystem that died without being
                        # properly unmounted will fail with ENOTCONN.
                        continue

                    built_path = os.path.join(entry.name, built_path) if built_path else entry.name
                    break

                else:
                    # Unable to find a matching entry; probably means the directory was deleted
                    raise ffi.build_oserror(errno.ENOENT)

        except OSError:
            os.close(parent_fd)
            raise

        sub_fd = parent_fd
        sub_stat = parent_stat
