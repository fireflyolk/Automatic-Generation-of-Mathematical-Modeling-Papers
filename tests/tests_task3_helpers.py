import ctypes
from contextlib import contextmanager
from ctypes import wintypes as w
import struct


@contextmanager
def junction(link, target):
    """Create/remove a disposable NTFS mount-point fixture using native APIs."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    kernel.DeviceIoControl.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    link.mkdir()
    handle = kernel.CreateFileW(str(link), 0x40000000, 0, None, 3, 0x02200000, None)
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    substitute = ('\\??\\' + str(target)).encode('utf-16-le')
    display = str(target).encode('utf-16-le')
    names = substitute + b'\0\0' + display + b'\0\0'
    payload = struct.pack('<IHHHHHH', 0xA0000003, 8 + len(names), 0, 0, len(substitute), len(substitute) + 2, len(display)) + names
    buffer = ctypes.create_string_buffer(payload)
    returned = w.DWORD()
    try:
        if not kernel.DeviceIoControl(handle, 0x900A4, buffer, len(payload), None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    try:
        yield link
    finally:
        link.rmdir()
