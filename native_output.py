"""Capture native CRT output on Windows for publication through Jupyter."""
from contextlib import contextmanager
import ctypes
import os
import tempfile
import threading
import codecs


@contextmanager
def capture_native_output():
    # The daslang DLL and CPython use the shared Windows Universal CRT.
    crt = ctypes.CDLL("ucrtbase")
    crt.fflush.argtypes = [ctypes.c_void_p]
    crt.fflush.restype = ctypes.c_int
    output = {"stdout": "", "stderr": ""}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        crt.fflush(None)
        saved = [os.dup(1), os.dup(2)]
        try:
            os.dup2(out.fileno(), 1)
            os.dup2(err.fileno(), 2)
            yield output
        finally:
            crt.fflush(None)
            for fd, original in zip((1, 2), saved):
                os.dup2(original, fd)
                os.close(original)
            for name, stream in (("stdout", out), ("stderr", err)):
                stream.seek(0)
                output[name] = stream.read().decode("utf-8", errors="replace")


@contextmanager
def stream_native_output(callback):
    """Redirect the shared CRT to pipes; publish UTF-8 chunks as they arrive."""
    crt = ctypes.CDLL("ucrtbase")
    crt.fflush.argtypes = [ctypes.c_void_p]
    crt.__acrt_iob_func.argtypes = [ctypes.c_uint]
    crt.__acrt_iob_func.restype = ctypes.c_void_p
    crt.setvbuf.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    crt.setvbuf.restype = ctypes.c_int
    crt.fflush(None)
    saved = [os.dup(1), os.dup(2)]
    readers = []

    def read(fd, name):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while data := os.read(fd, 4096):
                text = decoder.decode(data)
                if text:
                    callback(name, text)
            tail = decoder.decode(b"", final=True)
            if tail:
                callback(name, tail)
        finally:
            os.close(fd)

    try:
        for fd, name in ((1, "stdout"), (2, "stderr")):
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, fd)
            os.close(write_fd)
            if crt.setvbuf(crt.__acrt_iob_func(fd), None, 4, 0) != 0:
                os.close(read_fd)
                raise RuntimeError("Could not disable native output buffering")
            thread = threading.Thread(target=read, args=(read_fd, name), daemon=True)
            thread.start()
            readers.append(thread)
        yield
    finally:
        crt.fflush(None)
        for fd, original in zip((1, 2), saved):
            os.dup2(original, fd)
            os.close(original)
        for thread in readers:
            thread.join()
