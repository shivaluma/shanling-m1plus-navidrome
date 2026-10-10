#!/usr/bin/env python3
"""Dump a named MIPS32 function and annotate direct calls and nearby constants.

Constant comments are local syntactic candidates, not control-flow analysis.
"""
import pathlib
import struct
import sys

BASE = pathlib.Path(__file__).resolve().parents[1] / '.work/analysis-v2.1'
sys.path.insert(0, str(BASE / "tools/python"))
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
from capstone.mips import MIPS_OP_IMM, MIPS_OP_REG, MIPS_OP_MEM
from elftools.elf.elffile import ELFFile


def dump(path, names):
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        if elf.header.e_machine != "EM_MIPS" or not elf.little_endian:
            raise ValueError("Expected little-endian MIPS")
        symtab = elf.get_section_by_name(".dynsym")
        syms = {s.name: s for s in symtab.iter_symbols() if s.name}
        byaddr = {s.entry.st_value: s.name for s in syms.values() if s.entry.st_value}
        sections = list(elf.iter_sections())
        gpsec = elf.get_section_by_name(".reginfo")
        gp = struct.unpack("<6I", gpsec.data())[5] if gpsec else 0

        def raw(addr, limit=160):
            for s in sections:
                start, size = s.header.sh_addr, s.header.sh_size
                if s.header.sh_type != "SHT_NOBITS" and start <= addr < start + size:
                    return s.data()[addr-start:addr-start+limit]
            return b""

        def label(addr):
            if addr in byaddr:
                return byaddr[addr]
            data = raw(addr).split(b"\0", 1)[0]
            if len(data) >= 4 and all(32 <= b < 127 or b in (9, 10, 13) for b in data):
                return repr(data.decode("ascii"))
            return None

        md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
        md.detail = True
        out = BASE / "disassembly"
        out.mkdir(exist_ok=True)
        for name in names:
            sym = syms[name]
            addr, size = sym.entry.st_value, sym.entry.st_size
            code = raw(addr, size)
            if not size or len(code) != size:
                raise ValueError("Not a defined file-backed function: " + name)
            lines = [f"{path.relative_to(BASE)}: {name} @ {addr:#x}, {size} bytes; gp={gp:#x}",
                     "Candidate constants are syntactic annotations; branch/delay-slot dataflow is not modeled."]
            regs = {"gp": gp, "t9": addr, "zero": 0}
            decoded = 0
            for ins in md.disasm(code, addr):
                decoded += ins.size
                ops = ins.operands
                comment = []
                if ins.mnemonic in ("jal", "j", "bal") and ops and ops[0].type == MIPS_OP_IMM:
                    target = ops[0].imm
                    if target in byaddr:
                        comment.append("target=" + byaddr[target])
                dst = ins.reg_name(ops[0].reg) if ops and ops[0].type == MIPS_OP_REG else None
                if ins.mnemonic == "lui":
                    regs[dst] = (ops[1].imm << 16) & 0xffffffff
                elif ins.mnemonic == "addu":
                    left, right = ins.reg_name(ops[1].reg), ins.reg_name(ops[2].reg)
                    if left in regs and right in regs:
                        regs[dst] = (regs[left] + regs[right]) & 0xffffffff
                    else:
                        regs.pop(dst, None)
                elif ins.mnemonic in ("addiu", "ori") and ops[1].type == MIPS_OP_REG:
                    src = ins.reg_name(ops[1].reg)
                    if src in regs:
                        value = ((regs[src] + ops[2].imm) if ins.mnemonic == "addiu" else (regs[src] | ops[2].imm)) & 0xffffffff
                        regs[dst] = value
                        text = label(value)
                        if text:
                            comment.append(f"candidate {value:#x}: {text}")
                elif ins.mnemonic == "lw" and ops[1].type == MIPS_OP_MEM:
                    src = ins.reg_name(ops[1].mem.base)
                    if src in regs:
                        location = (regs[src] + ops[1].mem.disp) & 0xffffffff
                        data = raw(location, 4)
                        if len(data) == 4:
                            value = struct.unpack("<I", data)[0]
                            regs[dst] = value
                            text = label(value)
                            if text:
                                comment.append(f"loaded {value:#x}: {text}")
                elif ins.mnemonic == "move" and ops[1].type == MIPS_OP_REG:
                    regs[dst] = regs.get(ins.reg_name(ops[1].reg), 0)
                elif dst and ins.mnemonic not in ("sw", "sb", "sh", "beq", "bne", "jr", "jalr"):
                    regs.pop(dst, None)
                if ins.mnemonic == "jalr":
                    reg = ins.reg_name(ops[-1].reg)
                    target = regs.get(reg)
                    if target in byaddr:
                        comment.append("candidate indirect call=" + byaddr[target])
                lines.append(f"{ins.address:08x}  {ins.bytes.hex():8s}  {ins.mnemonic:9s} {ins.op_str}" + ("  # " + "; ".join(comment) if comment else ""))
            lines.append(f"Decode coverage: {decoded}/{size} bytes.")
            if decoded != size:
                lines.append("WARNING: trailing bytes were not decoded; no code/data classification is claimed.")
                lines.append(f"Undecoded range: {addr + decoded:#x}..{addr + size:#x} (end exclusive)")
                lines.append(code[decoded:].hex())
            target = out / (name + ".asm")
            target.write_text("\n".join(lines) + "\n")
            print(target)


if __name__ == "__main__":
    dump(BASE / "rootfs/release/bin/demo", sys.argv[1:])
