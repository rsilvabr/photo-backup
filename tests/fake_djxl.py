"""A stand-in for libjxl's djxl in the tests (selective_backup.DJXL): reads a
JXL of tests/fake_jxlphoto.py on stdin and writes the JPEG it keeps to stdout,
as `djxl - - --output_format jpeg` does. FAKE_DJXL_WRONG: a JPEG that is not
the original's bytes; FAKE_DJXL_FAIL: exit 1."""
import os
import struct
import sys


def main():
    data = sys.stdin.buffer.read()
    if os.environ.get("FAKE_DJXL_FAIL"):
        print("djxl: the fake was told to fail", file=sys.stderr)
        return 1
    calls = os.environ.get("FAKE_JXLPHOTO_CALLS")
    if calls:                               # counted beside the fake jxl-photo's calls
        import json
        import time
        os.makedirs(calls, exist_ok=True)
        with open(os.path.join(calls, f"{time.time_ns():020d}_{os.getpid()}.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(["djxl"] + sys.argv[1:], handle)
    pos, payload = 12, b""
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"jxlc":
            payload = data[pos + 8:pos + length]
        pos += length
    if os.environ.get("FAKE_DJXL_WRONG"):
        payload += b"?"
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
