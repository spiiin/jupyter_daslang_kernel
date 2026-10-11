import ctypes as C
import os
import threading
import unittest
from native_output import stream_native_output


class NativeOutputTests(unittest.TestCase):
    def test_native_stderr_and_partial_utf8_are_streamed(self):
        chunks = {"stdout": [], "stderr": []}
        ready = threading.Event()
        def publish(name, text):
            chunks[name].append(text)
            if "".join(chunks["stderr"]) == "Ошибка без newline":
                ready.set()
        crt = C.CDLL("ucrtbase")
        stream = crt["__acrt_iob_func"]
        stream.argtypes = [C.c_uint]
        stream.restype = C.c_void_p
        crt.fwrite.argtypes = [C.c_void_p, C.c_size_t, C.c_size_t, C.c_void_p]
        crt.fwrite.restype = C.c_size_t
        with stream_native_output(publish):
            data = "Ошибка без newline".encode()
            crt.fwrite(data, 1, len(data), stream(2))
            self.assertTrue(ready.wait(timeout=3))
            os.write(1, b"\xd0")
            os.write(1, b"\x96")
        self.assertEqual("".join(chunks["stdout"]), "Ж")
        self.assertEqual("".join(chunks["stderr"]), "Ошибка без newline")


if __name__ == "__main__":
    unittest.main()
