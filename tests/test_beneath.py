import contextlib
import errno
import os
import pathlib
import re
import sys
from typing import Any, Generator

import pytest

import nixutil


@contextlib.contextmanager
def open_beneath_managed(*args: Any, **kwargs: Any) -> Generator[int, None, None]:
    fd = nixutil.open_beneath(*args, **kwargs)

    try:
        yield fd
    finally:
        os.close(fd)


def test_open_beneath_basic() -> None:
    # dir_fd must be an int or None
    with pytest.raises(TypeError):
        nixutil.open_beneath("/", os.O_RDONLY, dir_fd="a")  # type: ignore

    # Not inheritable
    with open_beneath_managed("/", os.O_RDONLY) as fd:
        assert not os.get_inheritable(fd)

    # Basic errors

    with pytest.raises(FileNotFoundError):
        nixutil.open_beneath("", os.O_RDONLY)

    with open(sys.executable) as file:
        with pytest.raises(NotADirectoryError):
            nixutil.open_beneath("a", os.O_RDONLY, dir_fd=file.fileno())


def test_open_beneath(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "a")

    with open(tmp_path / "b", "w"):
        pass

    os.symlink("b", tmp_path / "c")
    os.symlink("/b", tmp_path / "d")

    os.mkdir(tmp_path / "a/e")

    os.symlink("a/e", tmp_path / "f")

    os.symlink("recur", tmp_path / "recur")

    tmp_dfd = os.open(tmp_path, os.O_RDONLY)

    try:
        tmp_stat = os.stat(tmp_dfd)

        for (path, flags, stat_fname) in [
            ("/", os.O_RDONLY, None),
            ("..", os.O_RDONLY, None),
            ("/..", os.O_RDONLY, None),
            ("a/..", os.O_RDONLY, None),
            ("/a/..", os.O_RDONLY, None),
            ("a/../..", os.O_RDONLY, None),
            ("/a/../..", os.O_RDONLY, None),
            ("a/e/../..", os.O_RDONLY, None),
            ("a/e/../../..", os.O_RDONLY, None),
            ("a", os.O_RDONLY, "a"),
            ("a/.", os.O_RDONLY, "a"),
            ("a/e/..", os.O_RDONLY, "a"),
            ("a/e", os.O_RDONLY, "a/e"),
            ("a/e/../e", os.O_RDONLY, "a/e"),
            ("b", os.O_RDONLY, "b"),
            ("c", os.O_RDONLY, "b"),
            ("d", os.O_RDONLY, "b"),
            ("f", os.O_RDONLY, "a/e"),
            ("f/..", os.O_RDONLY, "a"),
            (b"f/..", os.O_RDONLY, "a"),
            ("f/..", os.O_RDONLY | os.O_NOFOLLOW, "a"),
        ]:
            expect_stat = (
                tmp_stat
                if stat_fname is None
                else os.stat(stat_fname, dir_fd=tmp_dfd, follow_symlinks=False)
            )

            with open_beneath_managed(
                path,
                flags,
                dir_fd=tmp_dfd,
            ) as fd:
                assert os.path.samestat(os.stat(fd), expect_stat)

        for (path, flags, kwargs, eno) in [
            ("NOEXIST", os.O_RDONLY, {"no_symlinks": True}, errno.ENOENT),
            ("b/a", os.O_RDONLY, {"no_symlinks": True}, errno.ENOTDIR),
            ("d", os.O_RDONLY, {"no_symlinks": True}, errno.ELOOP),
            ("d", os.O_RDONLY | os.O_NOFOLLOW, {"no_symlinks": False}, errno.ELOOP),
            ("f", os.O_RDONLY | os.O_NOFOLLOW, {"no_symlinks": False}, errno.ELOOP),
            ("f", os.O_RDONLY, {"no_symlinks": True}, errno.ELOOP),
            ("f/..", os.O_RDONLY, {"no_symlinks": True}, errno.ELOOP),
        ]:
            with pytest.raises(
                OSError, match="^" + re.escape("[Errno {}] {}".format(eno, os.strerror(eno)))
            ):
                nixutil.open_beneath(path, flags, dir_fd=tmp_dfd, **kwargs)

    finally:
        os.close(tmp_dfd)
