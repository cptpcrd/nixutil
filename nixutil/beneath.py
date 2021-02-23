import collections
import errno
import os
import stat
import sys
from typing import AnyStr, Callable, Generator, List, Optional, Tuple, Union

from . import ffi, plat_util

DIR_OPEN_FLAGS = os.O_DIRECTORY

if sys.platform.startswith("freebsd"):
    # On FreeBSD, O_EXEC on directories has very similar semantics to O_SEARCH, and has for a while
    # (on newer versions O_SEARCH is an alias for O_EXEC)
    DIR_OPEN_FLAGS |= os.O_EXEC  # pylint: disable=no-member
else:
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
    audit_func: Optional[Callable[[str, int, AnyStr], None]] = None,
) -> int:
    """
    Open a file "beneath" a given directory.

    This function guarantees that no ``..`` component in ``path``, or in a symbolic link encountered
    in resolving ``path``, will ever be allowed to escape the "root" directory specified by
    ``dir_fd``. (In very specific circumstances, race conditions may allow multiple ``..``
    components in a row to cause ``open_beneath()`` to temporarily leave the directory in question,
    but it will check for such an escape before continuing and resolving any non-``..`` components).

    Currently, ``open_beneath()`` is able to take advantage of OS-specific path resolution features
    on the following platforms:

    - Linux 5.6+

    The ``path``, ``flags``, and ``mode`` arguments are as for ``os.open(...)``.

    If ``dir_fd`` is given and not ``None``, it is used to determine the directory relative to which
    paths will be resolved. Otherwise, the current working directory is used.

    ``path`` can be an absolute path, or it can contain references to symlinks that target absolute
    paths. In either case, the path is interpreted as if the process had ``chroot()``ed to the
    directory referenced by ``dir_fd`` (or the current working directory, as described above).

    If ``no_symlinks`` is True, no symlinks will be allowed during resolution of the path.

    If ``audit_func`` is not ``None``, it indicates a function that will be called to "audit"
    components of the path as they are resolved. The function will be called with three arguments:
    a "description" string indicating the context, a file descriptor referring to the most recently
    resolved directory, and a path whose meaning depends on the "description". The following
    "descriptions" are currently used (though more may be added):

    - ``"before"``: This is called at each stage of the path resolution, just before the next
      component is resolved. In this case, the third argument is the component that is about to be
      resolved (which may be ``/`` or ``..``).
    - ``"symlink"``: This is called immediately after encountering a symbolic link. In this case,
      the third argument is the target of the symlink that was encountered.

    The function should NOT perform any operations on the given file descriptor, or behavior is
    undefined. Additionally, it should always return ``None``; other return values may have special
    meanings in the future.

    If an exception is raised in an ``audit_func``, ``open_beneath()`` will clean up properly and
    pass the exception up to the caller.

    Here is an example ``audit_func`` that blocks ``..`` components in symlinks::

        def audit(desc, cur_fd, path):
            if desc == "symlink":
                while path:
                    path, part = os.path.split(path.rstrip("/"))
                    if part == "..":
                        raise RuntimeError("'..' component encountered")

    If ``remember_parents`` is True, it triggers an alternate escape prevention strategy. This flag
    makes ``open_beneath()`` retain open file descriptors to all of the directories it has
    previously seen. This allows it to simply rewind back to those directories when encountering a
    ``..`` element, instead of having to perform potentially inefficient escape detection. (By
    default, after a series of ``..`` elements, ``open_beneath()`` has to check that the current
    directory is still contained within the "root".)

    This is more efficient, but it requires a large number of file descriptors, and a malicious
    attacker in control of the specified ``path`` *or* the filesystem could easily cause
    ``open_beneath()`` to exhaust all the available file descriptors. Use with caution!

    Note: If ``open_beneath`` is able to take advantage of OS-specific path resolution features,
    then ``remember_parents`` is ignored.
    """

    path = os.fspath(path)

    flags |= os.O_NOCTTY

    if audit_func is None and _try_open_beneath is not None:
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
            audit_func=audit_func,
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
    audit_func: Optional[Callable[[str, int, AnyStr], None]] = None,
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

            # Sanity check -- `flags` can only ever be something other than DIR_OPEN_FLAGS if there
            # are no components left
            assert flags == DIR_OPEN_FLAGS or not parts

            if audit_func is not None:
                audit_func("before", cur_fd, part)

            old_fd = cur_fd

            try:
                if part == slash:
                    cur_fd = dir_fd

                    while parent_fds:
                        os.close(parent_fds.pop())

                    # The way that paths are constructed, this shouldn't be possible!
                    assert not saw_parent_elem

                elif part == dotdot:
                    if remember_parents:
                        if parent_fds:
                            if flags == DIR_OPEN_FLAGS:
                                cur_fd = parent_fds.pop()
                            else:
                                cur_fd = os.open(".", flags, dir_fd=parent_fds[-1])
                                os.close(parent_fds.pop())

                        else:
                            cur_fd = dir_fd

                    else:
                        if cur_fd == dir_fd or os.path.samestat(os.fstat(cur_fd), dir_fd_stat):
                            # We hit the root; stay there
                            cur_fd = dir_fd
                            saw_parent_elem = False
                        else:
                            cur_fd = os.open("..", flags, dir_fd=cur_fd)
                            saw_parent_elem = True

                elif part == dot:
                    if cur_fd != dir_fd and flags != DIR_OPEN_FLAGS:
                        cur_fd = os.open(".", flags, dir_fd=cur_fd)

                else:
                    if saw_parent_elem:
                        # Check that we didn't escape *before* trying to open anything.
                        # This will avoid problems with potential information leakage based on the
                        # error message (i.e. does a given file exist).
                        assert not remember_parents
                        _check_beneath(cur_fd, dir_fd_stat, orig_path)
                        saw_parent_elem = False

                    try:
                        cur_fd = os.open(
                            part, flags | os.O_NOCTTY | os.O_NOFOLLOW, mode=mode, dir_fd=cur_fd
                        )

                        # On Linux, O_PATH|O_NOFOLLOW will return a file descriptor open to the
                        # *symlink* (though adding in O_DIRECTORY will prevent this by only allowing
                        # a directory). Since we "add in" O_NOFOLLOW, if O_PATH was specified and
                        # neither O_NOFOLLOW nor O_DIRECTORY was, we might accidentally open a
                        # symlink when that isn't what the user wants.
                        #
                        # So let's check if it's a symlink in that case.

                        if (
                            sys.platform.startswith("linux")
                            and flags & (os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY) == os.O_PATH
                            and stat.S_ISLNK(os.stat(cur_fd).st_mode)
                        ):
                            os.close(cur_fd)
                            cur_fd = old_fd
                            raise ffi.build_oserror(errno.ELOOP, orig_path)

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

                        if audit_func is not None:
                            audit_func(
                                "symlink",
                                cur_fd,
                                (os.fsencode(target) if isinstance(orig_path, bytes) else target),
                            )

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

    except BaseException:  # pylint: disable=broad-except
        if cur_fd != dir_fd:
            os.close(cur_fd)

        raise

    finally:
        for fd in parent_fds:
            os.close(fd)

    return (
        os.open(".", flags=orig_flags | os.O_NOCTTY, dir_fd=dir_fd) if cur_fd == dir_fd else cur_fd
    )
