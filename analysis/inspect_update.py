#!/usr/bin/env python3
"""Validate this update container offline, without executing firmware code."""
import argparse
import hashlib
import json
import pathlib
import struct
import tarfile
import zlib


def inspect(path):
    expected = {"firmware_v20.info", "recovery-update/xImage", "recovery-update/rootfs.squashfs"}
    with tarfile.open(path) as tar:
        members = list(tar)
        regular = [m for m in members if m.isfile()]
        if len(regular) != len(expected) or {m.name for m in regular} != expected:
            raise ValueError("Unexpected archive layout")
        for m in members:
            p = pathlib.PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts or not (m.isfile() or m.isdir()):
                raise ValueError("Unsafe/unexpected member: " + m.name)
        files = {m.name: tar.extractfile(m).read() for m in regular}
    lines = files["firmware_v20.info"].decode("utf-8").splitlines()
    if len(lines) != 4:
        raise ValueError("Expected model, version, and two MD5 lines")
    if lines[0] != "Shanling M1 Plus":
        raise ValueError("Unexpected model")
    hashes = {}
    for line in lines[2:]:
        digest, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if name not in expected - {"firmware_v20.info"} or name in hashes:
            raise ValueError("Unexpected/repeated payload: " + name)
        data = files[name]
        actual = hashlib.md5(data).hexdigest()
        if actual != digest:
            raise ValueError("Payload MD5 mismatch: " + name)
        hashes[name] = dict(size=len(data), md5=actual, sha256=hashlib.sha256(data).hexdigest())
    if set(hashes) != expected - {"firmware_v20.info"}:
        raise ValueError("Missing payload digest")
    image = files["recovery-update/xImage"]
    h = struct.unpack(">7I4B32s", image[:64])
    header = bytearray(image[:64])
    header[4:8] = b"\0" * 4
    if h[0] != 0x27051956 or h[3] != len(image) - 64:
        raise ValueError("Invalid uImage header/length")
    if zlib.crc32(header) != h[1] or zlib.crc32(image[64:]) != h[6]:
        raise ValueError("uImage CRC mismatch")
    fs = files["recovery-update/rootfs.squashfs"]
    sb = struct.unpack("<5I6H8Q", fs[:96])
    if sb[0] != 0x73717368 or sb[9:11] != (4, 0) or sb[12] > len(fs):
        raise ValueError("Invalid SquashFS header/bounds")
    return dict(
        source=str(path.resolve()), size=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        model=lines[0], version=lines[1], payloads=hashes,
        uimage=dict(name=h[11].split(b"\0")[0].decode(), arch_id=h[8],
                    load=hex(h[4]), entry=hex(h[5]), compression_id=h[10],
                    header_crc_valid=True, payload_crc_valid=True),
        squashfs=dict(version="4.0", inode_count=sb[1], compression_id=sb[5],
                      block_size=sb[3], bytes_used=sb[12], image_size=len(fs)),
        limitation="Validates container consistency only; does not establish device acceptance, signatures, flash capacity, or bootability.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=pathlib.Path)
    parser.add_argument("--json-out", type=pathlib.Path)
    args = parser.parse_args()
    result = inspect(args.binary)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.json_out:
        args.json_out.write_text(encoded)
    print(encoded, end="")
