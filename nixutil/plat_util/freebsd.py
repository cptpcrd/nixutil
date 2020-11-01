# pylint: disable=invalid-name,too-few-public-methods
import ctypes
import os
from typing import Iterator, Optional

from . import bsd_util

CTL_KERN = 1
KERN_PROC = 14
KERN_PROC_FILEDESC = 33

KF_TYPE_VNODE = 1

PATH_MAX = 1024

pid_t = ctypes.c_int

sa_family_t = ctypes.c_uint8

_SS_MAXSIZE = 128
_SS_ALIGNSIZE = ctypes.sizeof(ctypes.c_int64)
_SS_PAD1SIZE = _SS_ALIGNSIZE - ctypes.sizeof(ctypes.c_ubyte) - ctypes.sizeof(sa_family_t)
_SS_PAD2SIZE = (
    _SS_MAXSIZE
    - ctypes.sizeof(ctypes.c_ubyte)
    - ctypes.sizeof(sa_family_t)
    - _SS_PAD1SIZE
    - _SS_ALIGNSIZE
)

CAP_RIGHTS_VERSION = 0


class SockaddrStorage(ctypes.Structure):
    _fields_ = [
        ("ss_len", ctypes.c_ubyte),
        ("ss_family", sa_family_t),
        ("ss_pad1", (ctypes.c_char * _SS_PAD1SIZE)),
        ("ss_align", ctypes.c_int64),
        ("ss_pad2", (ctypes.c_char * _SS_PAD2SIZE)),
    ]


class CapRights(ctypes.Structure):
    _fields_ = [
        ("cr_rights", (ctypes.c_uint64 * (CAP_RIGHTS_VERSION + 2))),
    ]


class KinfoFile11(ctypes.Structure):
    _fields_ = [
        ("kf_vnode_type", ctypes.c_int),
        ("kf_sock_domain", ctypes.c_int),
        ("kf_sock_type", ctypes.c_int),
        ("kf_sock_protocol", ctypes.c_int),
        ("kf_sa_local", SockaddrStorage),
        ("kf_sa_peer", SockaddrStorage),
    ]


class KinfoFileSock(ctypes.Structure):
    _fields_ = [
        ("kf_sock_sendq", ctypes.c_uint32),
        ("kf_sock_domain0", ctypes.c_int),
        ("kf_sock_type0", ctypes.c_int),
        ("kf_sock_protocol0", ctypes.c_int),
        ("kf_sa_local", SockaddrStorage),
        ("kf_sa_peer", SockaddrStorage),
        ("kf_sock_pcb", ctypes.c_uint64),
        ("kf_sock_inpcb", ctypes.c_uint64),
        ("kf_sock_unpconn", ctypes.c_uint64),
        ("kf_sock_snd_sb_state", ctypes.c_uint16),
        ("kf_sock_rcv_sb_state", ctypes.c_uint16),
        ("kf_sock_recvq", ctypes.c_uint32),
    ]


class KinfoFileFile(ctypes.Structure):
    _fields_ = [
        ("kf_file_type", ctypes.c_int),
        ("kf_spareint", (ctypes.c_int * 3)),
        ("kf_spareint64", (ctypes.c_uint64 * 30)),
        ("kf_file_fsid", ctypes.c_uint64),
        ("kf_file_rdev", ctypes.c_uint64),
        ("kf_file_fileid", ctypes.c_uint64),
        ("kf_file_size", ctypes.c_uint64),
        ("kf_file_fsid_freebsd11", ctypes.c_uint32),
        ("kf_file_rdev_freebsd11", ctypes.c_uint32),
        ("kf_file_mode", ctypes.c_uint16),
        ("kf_file_pad0", ctypes.c_uint16),
        ("kf_file_pad1", ctypes.c_uint32),
    ]


class KinfoFileSem(ctypes.Structure):
    _fields_ = [
        ("kf_spareint", (ctypes.c_uint32 * 4)),
        ("kf_spareint64", (ctypes.c_uint64 * 32)),
        ("kf_sem_value", ctypes.c_uint32),
        ("kf_sem_mode", ctypes.c_uint16),
    ]


class KinfoFilePipe(ctypes.Structure):
    _fields_ = [
        ("kf_spareint", (ctypes.c_uint32 * 4)),
        ("kf_spareint64", (ctypes.c_uint64 * 32)),
        ("kf_pipe_addr", ctypes.c_uint64),
        ("kf_pipe_peer", ctypes.c_uint64),
        ("kf_pipe_buffer_cnt", ctypes.c_uint32),
        ("kf_pts_pad0", (ctypes.c_uint32 * 3)),
    ]


class KinfoFilePts(ctypes.Structure):
    _fields_ = [
        ("kf_spareint", (ctypes.c_uint32 * 4)),
        ("kf_spareint64", (ctypes.c_uint64 * 32)),
        ("kf_pts_dev_freebsd11", ctypes.c_uint32),
        ("kf_pts_pad0", ctypes.c_uint32),
        ("kf_pts_dev", ctypes.c_uint64),
        ("kf_pts_pad1", (ctypes.c_uint32 * 4)),
    ]


class KinfoFileProc(ctypes.Structure):
    _fields_ = [
        ("kf_spareint", (ctypes.c_uint32 * 4)),
        ("kf_spareint64", (ctypes.c_uint64 * 32)),
        ("kf_pid", pid_t),
    ]


class KinfoFileUn(ctypes.Union):
    _fields_ = [
        ("kf_freebsd11", KinfoFile11),
        ("kf_sock", KinfoFileSock),
        ("kf_file", KinfoFileFile),
        ("kf_sem", KinfoFileSem),
        ("kf_pipe", KinfoFilePipe),
        ("kf_pts", KinfoFilePts),
        ("kf_proc", KinfoFileProc),
    ]


class KinfoFile(ctypes.Structure):
    _fields_ = [
        ("kf_structsize", ctypes.c_int),
        ("kf_type", ctypes.c_int),
        ("kf_fd", ctypes.c_int),
        ("kf_ref_count", ctypes.c_int),
        ("kf_flags", ctypes.c_int),
        ("kf_pad0", ctypes.c_int),
        ("kf_offset", ctypes.c_int64),
        ("kf_un", KinfoFileUn),
        ("kf_status", ctypes.c_uint16),
        ("kf_pad1", ctypes.c_uint16),
        ("_kf_ispare0", ctypes.c_int),
        ("kf_cap_rights", CapRights),
        ("_kf_cap_spare", ctypes.c_uint64),
        ("kf_path", (ctypes.c_char * PATH_MAX)),
    ]


def _iter_kinfo_files(pid: int) -> Iterator[KinfoFile]:
    kinfo_file_data = bsd_util.sysctl_bytes_retry(
        [CTL_KERN, KERN_PROC, KERN_PROC_FILEDESC, pid], None
    )

    kinfo_file_size = ctypes.sizeof(KinfoFile)

    i = 0
    while i < len(kinfo_file_data):
        kfile_data = kinfo_file_data[i: i + kinfo_file_size].ljust(kinfo_file_size, b"\0")
        kfile = KinfoFile.from_buffer_copy(kfile_data)

        if kfile.kf_structsize == 0:
            break

        yield kfile

        i += kfile.kf_structsize


def try_recover_fd_path(fd: int) -> Optional[str]:
    for kfile in _iter_kinfo_files(os.getpid()):
        if kfile.kf_fd == fd and kfile.kf_type == KF_TYPE_VNODE:
            # Sometimes the path is empty ("") for no apparent reason.
            return os.fsdecode(kfile.kf_path) or None

    return None
