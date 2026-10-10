#!/usr/bin/env python3
"""Offline evidence extraction; never runs firmware executables.

Symlinks and special files are inventoried only. rootfs/ is an analysis copy,
not a rebuild-ready filesystem. Original metadata is in rootfs-manifest.json.
"""
import hashlib
import json
import pathlib
import re
import struct
import sys

BASE = pathlib.Path(__file__).resolve().parents[1] / '.work/analysis-v2.1'
sys.path.insert(0, str(BASE / "tools/python"))
from dissect.squashfs import SquashFS
from elftools.elf.elffile import ELFFile


def extract():
    out = BASE / "rootfs"
    out.mkdir(exist_ok=False)
    manifest = []
    with (BASE / "package/recovery-update/rootfs.squashfs").open("rb") as fh:
        fs = SquashFS(fh)

        def visit(node, rel):
            if any(p in ("..", "") for p in rel.parts) or rel.is_absolute():
                raise ValueError(rel)
            dst = out / rel
            item = dict(path=str(rel), inode=node.inode_number, mode=oct(node.mode),
                        uid=node.uid, gid=node.gid, mtime=node.mtime.isoformat(), size=node.size)
            if node.is_dir():
                item["type"] = "directory"
                dst.mkdir(exist_ok=True)
                for child in node.iterdir():
                    if child.name not in (".", ".."):
                        visit(child, rel / child.name)
            elif node.is_file():
                item["type"] = "file"
                with node.open() as stream:
                    data = stream.read()
                if len(data) != node.size:
                    raise ValueError(f"Size mismatch: {rel}")
                dst.write_bytes(data)
                item["sha256"] = hashlib.sha256(data).hexdigest()
            elif node.is_symlink():
                item.update(type="symlink", target=node.link)
            else:
                item.update(type="special", header=str(node.header))
            manifest.append(item)

        for child in fs.root.iterdir():
            if child.name not in (".", ".."):
                visit(child, pathlib.PurePosixPath(child.name))
    manifest.sort(key=lambda x: x["path"])
    (BASE / "rootfs-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Extracted/inventoried entries:", len(manifest))


def inventory():
    records = []
    stringdir = BASE / "strings"
    symbol_dir = BASE / "symbols"
    stringdir.mkdir(exist_ok=True)
    symbol_dir.mkdir(exist_ok=True)
    for p in sorted((BASE / "rootfs").rglob("*")):
        if not p.is_file():
            continue
        data = p.read_bytes()
        if data[:4] != b"\x7fELF":
            continue
        rel = str(p.relative_to(BASE / "rootfs"))
        with p.open("rb") as f:
            elf = ELFFile(f)
            record = dict(path=rel, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                          bits=elf.elfclass, little_endian=elf.little_endian,
                          machine=elf.header.e_machine, type=elf.header.e_type,
                          entry=hex(elf.header.e_entry), flags=hex(elf.header.e_flags),
                          sections=[s.name for s in elf.iter_sections()], needed=[], symbols={})
            for seg in elf.iter_segments():
                if seg.header.p_type == "PT_INTERP":
                    record["interpreter"] = seg.get_interp_name()
            abi = elf.get_section_by_name(".MIPS.abiflags")
            if abi:
                record["mips_abi_flags"] = dict(zip(
                    ["version", "isa_level", "isa_rev", "gpr_size", "cpr1_size", "cpr2_size",
                     "fp_abi", "isa_ext", "ases", "flags1", "flags2"],
                    struct.unpack("<H6B4I", abi.data())))
            dynamic = elf.get_section_by_name(".dynamic")
            if dynamic:
                record["needed"] = [t.needed for t in dynamic.iter_tags() if t.entry.d_tag == "DT_NEEDED"]
            for section_name in (".symtab", ".dynsym"):
                section = elf.get_section_by_name(section_name)
                if section:
                    symbols = [dict(name=s.name, address=hex(s.entry.st_value), size=s.entry.st_size,
                                    type=s.entry.st_info.type, bind=s.entry.st_info.bind,
                                    section=s.entry.st_shndx) for s in section.iter_symbols() if s.name]
                    record["symbols"][section_name] = len(symbols)
                    target = symbol_dir / (rel.replace("/", "__") + section_name + ".json")
                    target.write_text(json.dumps(symbols, indent=2) + "\n")
        records.append(record)
        (stringdir / (rel.replace("/", "__") + ".txt")).write_text("".join(
            f"0x{m.start():08x}\t{m.group().decode('ascii')}\n"
            for m in re.finditer(rb"[\x20-\x7e]{5,}", data)))
    (BASE / "elf-inventory.json").write_text(json.dumps(records, indent=2) + "\n")
    print("ELF objects:", len(records))
    for r in sorted(records, key=lambda r: r["size"], reverse=True)[:25]:
        print(r["path"], r["size"], r["machine"], r["symbols"])


if __name__ == "__main__":
    if not (BASE / "rootfs-manifest.json").exists():
        extract()
    inventory()
