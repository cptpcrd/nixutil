import collections
import errno
import os
import stat
from typing import AnyStr, Callable, Generator, List, Optional, Tuple, Union

from . import ffi, plat_util

DIR_OPEN_FLAGS = os.O_DIRECTORY

# Use O_PATH or O_SEARCH if available, otherwise just O_RDONLY
DIR_OPEN_FLAGS |= getattr(os, "O_PATH", getattr(os, "O_SEARCH", os.O_RDONLY))

_try_open_beneath: Optional[Callable[..., int]] = getattr(plat_util, "try_open_beneath", None)


def _split_path(
    path: AnyStr, *, slash: AnyStr, flags: int, orig_path: AnyStr
) -> Generator[Tuple[AnyStr, int], None, None]:
    if not path:
        raise ffi.build_oserror(errno.ENOENT, orig_path)

    if path.endswith(slash):
        flags |= os.O_DIRECTORY

    split_parts = list(filter(bool, path.split(slash)))

    if path.startswith(slash):
        yield slash, (DIR_OPEN_FLAGS if split_parts else flags)

    for part in split_parts[:-1]:
        yield part, DIR_OPEN_FLAGS

    if split_parts:
        yield split_parts[-1], flags


def open_beneath(
    path: Union[AnyStr, "os.PathLike[AnyStr]"],
    flags: int,
    *,
    mode: int = 0o777,
    dir_fd: Optional[int] = None,
    no_symlinks: bool = False,
    remember_parents: bool = False,
) -> int:
    path = os.fspath(path)

    if _try_open_beneath is not None:
        fd = _try_open_beneath(path, flags, mode=mode, dir_fd=dir_fd, no_symlinks=no_symlinks)
        if fd is not None:
            return fd

    slash: AnyStr
    dot: AnyStr
    if isinstance(path, bytes):
        slash = b"/"
        dot = b"."
    else:
        slash = "/"
        dot = "."

    # We need a file descriptor that won't move (the current directory might) that we can use to
    # perform lookups from.
    new_dir_fd = os.open(".", DIR_OPEN_FLAGS) if dir_fd is None else dir_fd

    try:
        return _open_beneath(
            path,
            new_dir_fd,
            flags,
            mode,
            no_symlinks,
            slash=slash,
            dot=dot,
            remember_parents=remember_parents,
        )
    finally:
        if new_dir_fd != dir_fd:
            os.close(new_dir_fd)  # pytype: disable=bad-return-type


def _check_beneath(cur_fd: int, dir_fd_stat: os.stat_result, orig_path: AnyStr) -> None:
    # We need to rewind up the directory tree and make sure that we didn't escape because of
    # race conditions with "..".

    orig_fd = cur_fd
    prev_stat = None

    try:
        while True:
            cur_stat = os.fstat(cur_fd)

            if os.path.samestat(cur_stat, dir_fd_stat):
                # We found it! We *didn't* escape.
                return
            elif prev_stat is not None and os.path.samestat(cur_stat, prev_stat):
                # Trying to open ".." brought us the same directory. That means we're at "/"
                # (the REAL "/").
                # So we escaped the "beneath" directory.
                raise ffi.build_oserror(errno.EXDEV, orig_path)

            new_fd = os.open("..", DIR_OPEN_FLAGS, dir_fd=cur_fd)
            if cur_fd != orig_fd:
                os.close(cur_fd)
            cur_fd = new_fd

            prev_stat = cur_stat

    finally:
        if cur_fd != orig_fd:
            os.close(cur_fd)


def _open_beneath(
    orig_path: AnyStr,
    dir_fd: int,
    orig_flags: int,
    mode: int,
    no_symlinks: bool,
    *,
    slash: AnyStr,
    dot: AnyStr,
    remember_parents: bool,
) -> int:
    dir_fd_stat = os.fstat(dir_fd)

    if not stat.S_ISDIR(dir_fd_stat.st_mode):
        raise ffi.build_oserror(errno.ENOTDIR, orig_path)

    parts = collections.deque(
        _split_path(orig_path, slash=slash, flags=orig_flags, orig_path=orig_path)
    )

    dotdot = dot + dot

    if no_symlinks and dotdot not in parts:
        # We will *never* see ".."
        remember_parents = False

    max_symlinks = 0 if no_symlinks else 40
    found_symlinks = 0

    parent_fds: List[int] = []

    cur_fd = dir_fd

    # Hypothetical scenario: We're asked to open "a/../../../etc/passwd". We descend into "a", but
    # before we can ascend out, somebody renames it so that it's outside the directory specified by
    # `dir_fd`.
    # Now, as we rewind up the directory tree, we don't see a directory that's the same as `dir_fd`!
    # So we keep going, and we may in fact open "/etc/passwd" without realizing it!
    #
    # So when we see a ".." element with remember_parents=False, we resolve it, then we set
    # saw_parent_elem=True. The next time around, if we don't see ".." or "/", we checl to make sure
    # that we haven't escaped before resolving that component.
    saw_parent_elem = False

    try:
        while parts:
            part, flags = parts.popleft()

            old_fd = cur_fd

            try:
                if part == slash:
                    cur_fd = dir_fd

                    while parent_fds:
                        os.close(parent_fds.pop())

                    # The way that paths are constructed, this shouldn't be possible!
                    assert not saw_parent_elem

                elif part == dotdot:
                    if cur_fd != dir_fd:
                        if remember_parents:
                            cur_fd = parent_fds.pop() if parent_fds else dir_fd
                        else:
                            if os.path.samestat(os.fstat(cur_fd), dir_fd_stat):
                                # We hit the root; stay there
                                cur_fd = dir_fd
                                saw_parent_elem = False
                            else:
                                cur_fd = os.open("..", flags, dir_fd=cur_fd)
                                saw_parent_elem = True

                elif part != dot:
                    if saw_parent_elem:
                        # Check that we didn't escape *before* trying to open anything.
                        # This will avoid problems with potential information leakage based on the
                        # error message (i.e. does a given file exist).
                        assert not remember_parents
                        _check_beneath(cur_fd, dir_fd_stat, orig_path)
                        saw_parent_elem = False

                    try:
                        cur_fd = os.open(part, flags | os.O_NOFOLLOW, mode=mode, dir_fd=cur_fd)
                    except OSError as ex:
                        # When flags=O_DIRECTORY|O_NOFLLOW, if the last component is a symlink then
                        # it will fail with ENOTDIR.
                        # Otherwise, when the last component is a symlink, most OSes return ELOOP.
                        # However, FreeBSD returns EMLINK and NetBSD returns EFTYPE.

                        if ex.errno not in (
                            errno.ELOOP,
                            errno.ENOTDIR,
                            errno.EMLINK,
                            getattr(errno, "EFTYPE", None),
                        ):
                            raise

                        # It may have failed because it's a symlink.
                        # (If ex.errno != errno.ENOTDIR, it's definitely a symlink.)

                        try:
                            target = os.readlink(part, dir_fd=cur_fd)
                        except OSError as ex2:
                            if ex2.errno == errno.EINVAL:
                                # It's not a symlink
                                if ex.errno == errno.ENOTDIR:
                                    # All we knew was that it wasn't a directory, so it's probably
                                    # another file type. Re-raise the original exception.
                                    raise ex from ex2
                                else:
                                    # The OS told us it was a symlink; now it's telling us it isn't.
                                    # Probably a race condition.
                                    raise ffi.build_oserror(errno.EAGAIN, orig_path) from ex2
                            else:
                                raise

                        found_symlinks += 1
                        if flags & os.O_NOFOLLOW or found_symlinks > max_symlinks:
                            raise ffi.build_oserror(errno.ELOOP, orig_path) from ex

                        parts.extendleft(
                            reversed(
                                list(
                                    _split_path(
                                        target, slash=slash, flags=flags, orig_path=orig_path
                                    )
                                )
                            )
                        )

                    else:
                        # Successfully opened

                        if remember_parents and old_fd != dir_fd:
                            parent_fds.append(old_fd)

            finally:
                if old_fd not in (cur_fd, dir_fd) and old_fd not in parent_fds:
                    os.close(old_fd)

        if saw_parent_elem:
            assert not remember_parents
            assert not parent_fds
            assert cur_fd != dir_fd
            _check_beneath(cur_fd, dir_fd_stat, orig_path)

    except OSError:
        if cur_fd != dir_fd:
            os.close(cur_fd)

        raise

    finally:
        for fd in parent_fds:
            os.close(fd)

    return os.dup(cur_fd) if cur_fd == dir_fd else cur_fd
