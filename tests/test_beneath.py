import contextlib
import errno
import itertools
import os
import pathlib
import re
import sys
from typing import Any, Dict, Generator, cast

import pytest

import nixutil

from .util import managed_open


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

    with pytest.raises(TypeError):
        nixutil.open_beneath(  # pytype: disable=wrong-arg-types
            "/", os.O_RDONLY, dir_fd="a", audit_func=lambda desc, fd, name: None  # type: ignore
        )

    # Not inheritable
    with open_beneath_managed("/", os.O_RDONLY) as fd:
        assert not os.get_inheritable(fd)

    # Not inheritable
    with open_beneath_managed("/", os.O_RDONLY, audit_func=lambda desc, fd, name: None) as fd:
        assert not os.get_inheritable(fd)

    # Basic errors

    with pytest.raises(FileNotFoundError):
        nixutil.open_beneath("", os.O_RDONLY)

    with pytest.raises(FileNotFoundError):
        nixutil.open_beneath("", os.O_RDONLY, audit_func=lambda desc, fd, name: None)

    with open(sys.executable) as file:
        with pytest.raises(NotADirectoryError):
            nixutil.open_beneath("a", os.O_RDONLY, dir_fd=file.fileno())

        with pytest.raises(NotADirectoryError):
            nixutil.open_beneath(
                "a", os.O_RDONLY, dir_fd=file.fileno(), audit_func=lambda desc, fd, name: None
            )


def test_open_beneath(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "a")

    with open(tmp_path / "b", "w"):
        pass

    os.symlink("b", tmp_path / "c")
    os.symlink("/b", tmp_path / "d")

    os.mkdir(tmp_path / "a/e")

    os.symlink("a/e", tmp_path / "f")

    os.symlink("../../b", tmp_path / "a/e/g")
    os.symlink("/b", tmp_path / "a/e/h")
    os.symlink("/a", tmp_path / "a/e/i")

    os.mkdir(tmp_path / "a/e/j")

    os.symlink("recur", tmp_path / "recur")

    with managed_open(tmp_path, os.O_RDONLY) as tmp_dfd:
        tmp_stat = os.stat(tmp_dfd)

        for remember_parents, audit_func in itertools.product(
            [False, True], [None, lambda desc, fd, name: None]
        ):
            for (path, flags, stat_fname) in [
                ("/", os.O_RDONLY, None),
                ("..", os.O_RDONLY, None),
                (".", os.O_RDONLY, None),
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
                ("a/e/g", os.O_RDONLY, "b"),
                ("a/e/h", os.O_RDONLY, "b"),
                ("a/e/i/e", os.O_RDONLY, "a/e"),
                ("a/e/j/..", os.O_RDONLY, "a/e"),
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
                    remember_parents=remember_parents,
                    audit_func=audit_func,
                ) as fd:
                    assert os.path.samestat(os.stat(fd), expect_stat)

            for (path, flags, kwargs, eno) in [
                ("NOEXIST", os.O_RDONLY, {"no_symlinks": True}, errno.ENOENT),
                ("a/NOEXIST", os.O_RDONLY, {"no_symlinks": True}, errno.ENOENT),
                ("b/a", os.O_RDONLY, {"no_symlinks": True}, errno.ENOTDIR),
                ("d", os.O_RDONLY, {"no_symlinks": True}, errno.ELOOP),
                ("d", os.O_RDONLY | os.O_NOFOLLOW, {"no_symlinks": False}, errno.ELOOP),
                ("f", os.O_RDONLY | os.O_NOFOLLOW, {"no_symlinks": False}, errno.ELOOP),
                ("f", os.O_RDONLY, {"no_symlinks": True}, errno.ELOOP),
                ("f/..", os.O_RDONLY, {"no_symlinks": True}, errno.ELOOP),
                ("recur", os.O_RDONLY, {"no_symlinks": False}, errno.ELOOP),
                ("a/../recur", os.O_RDONLY, {"no_symlinks": False}, errno.ELOOP),
                ("recur/a", os.O_RDONLY, {"no_symlinks": False}, errno.ELOOP),
            ]:
                with pytest.raises(
                    OSError, match="^" + re.escape("[Errno {}] {}".format(eno, os.strerror(eno)))
                ):
                    nixutil.open_beneath(
                        path,
                        flags,
                        dir_fd=tmp_dfd,
                        remember_parents=remember_parents,
                        audit_func=audit_func,
                        **cast(Dict[str, Any], kwargs)
                    )


def test_open_beneath_escape(tmp_path: pathlib.Path) -> None:
    os.mkdir(tmp_path / "a")
    os.mkdir(tmp_path / "a/b")

    # Simulate a race condition by moving "a/b" out of "a" after it's descended in

    def audit_func(desc: str, fd: int, name: str) -> None:  # pylint: disable=unused-argument
        if desc == "before" and name == ".." and os.path.exists(tmp_path / "a/b"):
            os.rename(tmp_path / "a/b", tmp_path / "b")

    with managed_open(tmp_path / "a", os.O_RDONLY) as a_dfd:
        for path in ["b/..", "b/../..", "b/../b", "b/../../a"]:
            with pytest.raises(OSError, match="^" + re.escape("[Errno {}]".format(errno.EXDEV))):
                nixutil.open_beneath(path, os.O_RDONLY, dir_fd=a_dfd, audit_func=audit_func)

            os.rename(tmp_path / "b", tmp_path / "a/b")


def test_open_beneath_execute(tmp_path: pathlib.Path) -> None:
    if nixutil.beneath.DIR_OPEN_FLAGS == os.O_DIRECTORY | os.O_RDONLY:
        # No extra flags like O_PATH or O_SEARCH available on the current platform
        pytest.skip(
            "Unable to look in subdirectories without 'read' permission on the current platform"
        )

    os.mkdir(tmp_path / "a")

    with open(tmp_path / "a/b", "w"):
        pass

    os.symlink("b", tmp_path / "a/c")

    try:
        # 0o100 is "--x------"; i.e. execute permission but not read permission.
        # That allows us to look at files within the directory, but not list the directory (or open
        # it without O_PATH or O_SEARCH).
        os.chmod(tmp_path / "a", 0o100)

        with managed_open(tmp_path, os.O_RDONLY) as tmp_dfd:
            for remember_parents, audit_func in itertools.product(
                [False, True], [None, lambda desc, fd, name: None]
            ):
                for path in ["a/b", "a/c"]:
                    expect_stat = os.stat(path, dir_fd=tmp_dfd)

                    with open_beneath_managed(
                        path,
                        os.O_RDONLY,
                        dir_fd=tmp_dfd,
                        audit_func=audit_func,
                        remember_parents=remember_parents,
                    ) as fd:
                        assert os.path.samestat(os.fstat(fd), expect_stat)

                with pytest.raises(PermissionError):
                    nixutil.open_beneath(
                        "a",
                        os.O_RDONLY,
                        dir_fd=tmp_dfd,
                        audit_func=audit_func,
                        remember_parents=remember_parents,
                    )

    finally:
        # chmod() it back so pytest can remove it
        os.chmod(tmp_path / "a", 0o755)
