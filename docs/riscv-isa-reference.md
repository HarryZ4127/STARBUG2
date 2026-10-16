# RISC-V Standard Instruction Set: Organized Reference (RV32 focus)

Instructions are grouped by **extension**, then by **function**. Because STARBUG is RV32, the main tables are RV32. RV64-only instructions are collected in a short appendix.

## 0. Which extensions STARBUG enables

From `cvw/config/starbug/config.vh` (`XLEN = 32`):

| Extension | What it adds | `main` | `origin/lm/fp` |
|---|---|:-:|:-:|
| **I** | Base integer | ✅ | ✅ |
| **M** (+ Zmmul) | Multiply / divide | ✅ | ✅ |
| **A** = Zalrsc + Zaamo | Atomics | ✅ | ✅ |
| **Zicsr** | CSR access | ✅ | ✅ |
| **Zifencei** | `fence.i` | ✅ | ✅ |
| **Zicntr / Zihpm** | Cycle, time, instret, and HPM counters | ✅ | ✅ |
| **Zicond** | Conditional zero | ✅ | ✅ |
| **Zba / Zbb / Zbc / Zbs** | Bit manipulation | ✅ | ✅ |
| **Zbkb / Zbkc / Zbkx** | Crypto bit manipulation | ✅ | ✅ |
| **Zknd / Zkne / Zknh** | AES decrypt/encrypt, SHA-2 | ✅ | ✅ |
| **Zca / Zcb** | Compressed 16-bit | ✅ | ✅ |
| **Zcf** | Compressed FP load/store | ❌ | ✅ |
| **Zicbom / Zicboz / Zicbop** | Cache-block ops | ✅ | ✅ |
| **S, U**, Svinval, Svadu, Sstc | Privileged modes and VM | ✅ | ✅ |
| **F / D** | Single / double float | ❌ | ✅ |
| **Zfh / Zfa** | Half float, additional FP | ❌ | ✅ |
| Q, V, E, Zicclsm, Svpbmt, Svnapot | — | ❌ | ❌ |

> **STARBUG bundle marker:** `c.li x0, N` (a Zca HINT, §10) starts a VLIW bundle of the next N instructions. On any non-STARBUG core it's a legal no-op.

---

## 1. Instruction formats (32-bit)

```
         31        25 24   20 19   15 14  12 11         7 6      0
R-type  |  funct7    |  rs2  |  rs1  |funct3|     rd     | opcode |
I-type  |    imm[11:0]       |  rs1  |funct3|     rd     | opcode |
S-type  | imm[11:5]  |  rs2  |  rs1  |funct3|  imm[4:0]  | opcode |
B-type  |imm[12|10:5]|  rs2  |  rs1  |funct3|imm[4:1|11]| opcode |
U-type  |           imm[31:12]              |     rd     | opcode |
J-type  |     imm[20|10:1|11|19:12]         |     rd     | opcode |
R4-type | rs3 |fmt|  rs2  |  rs1  |  rm  |     rd     | opcode |   (FP fused multiply-add)
```

Immediates are always sign-extended from their top bit. B and J offsets are multiples of 2.

### Major opcodes (bits [6:0])

| Opcode | Name | Used by |
|---|---|---|
| `0110111` 0x37 | LUI | `lui` |
| `0010111` 0x17 | AUIPC | `auipc` |
| `1101111` 0x6F | JAL | `jal` |
| `1100111` 0x67 | JALR | `jalr` |
| `1100011` 0x63 | BRANCH | `beq … bgeu` |
| `0000011` 0x03 | LOAD | `lb … lhu` |
| `0100011` 0x23 | STORE | `sb sh sw` |
| `0010011` 0x13 | OP-IMM | `addi …`, Zbb/Zbs/Zbkb/Zknh immediates, `prefetch.*` |
| `0110011` 0x33 | OP | `add …`, M, Zicond, Zba/Zbb/Zbc/Zbs/Zbk*, Zkn* |
| `0001111` 0x0F | MISC-MEM | `fence`, `fence.i`, `cbo.*` |
| `1110011` 0x73 | SYSTEM | `ecall`, `ebreak`, CSRs, `mret/sret/wfi`, `sfence.vma` |
| `0101111` 0x2F | AMO | A extension |
| `0000111` 0x07 / `0100111` 0x27 | LOAD-FP / STORE-FP | `flw/fld/flh`, `fsw/fsd/fsh` |
| `1000011` `1000111` `1001011` `1001111` | MADD / MSUB / NMSUB / NMADD | FP fused ops |
| `1010011` 0x53 | OP-FP | All other FP |
| `0011011` 0x1B / `0111011` 0x3B | OP-IMM-32 / OP-32 | RV64 `*w` instructions only |

---

## 2. RV32I: Base integer (40 instructions)

### 2.1 Upper immediates
| Instr | Fmt | Encoding | Operation |
|---|---|---|---|
| `lui rd, imm` | U | op=0x37 | `rd = imm << 12` |
| `auipc rd, imm` | U | op=0x17 | `rd = pc + (imm << 12)` |

### 2.2 Integer arithmetic and logic: register–immediate (OP-IMM, 0x13)
| Instr | funct3 | funct7 / imm[11:5] | Operation |
|---|---|---|---|
| `addi` | 000 | — | `rd = rs1 + imm` |
| `slti` | 010 | — | `rd = (rs1 <ₛ imm)` |
| `sltiu` | 011 | — | `rd = (rs1 <ᵤ imm)` |
| `xori` | 100 | — | `rd = rs1 ^ imm` |
| `ori` | 110 | — | `rd = rs1 \| imm` |
| `andi` | 111 | — | `rd = rs1 & imm` |
| `slli` | 001 | 0000000 | `rd = rs1 << shamt` |
| `srli` | 101 | 0000000 | `rd = rs1 >>ᵤ shamt` |
| `srai` | 101 | 0100000 | `rd = rs1 >>ₛ shamt` |

### 2.3 Integer arithmetic and logic: register–register (OP, 0x33)
| Instr | funct3 | funct7 | Operation |
|---|---|---|---|
| `add` | 000 | 0000000 | `rd = rs1 + rs2` |
| `sub` | 000 | 0100000 | `rd = rs1 - rs2` |
| `sll` | 001 | 0000000 | `rd = rs1 << rs2[4:0]` |
| `slt` | 010 | 0000000 | `rd = (rs1 <ₛ rs2)` |
| `sltu` | 011 | 0000000 | `rd = (rs1 <ᵤ rs2)` |
| `xor` | 100 | 0000000 | `rd = rs1 ^ rs2` |
| `srl` | 101 | 0000000 | `rd = rs1 >>ᵤ rs2[4:0]` |
| `sra` | 101 | 0100000 | `rd = rs1 >>ₛ rs2[4:0]` |
| `or` | 110 | 0000000 | `rd = rs1 \| rs2` |
| `and` | 111 | 0000000 | `rd = rs1 & rs2` |

### 2.4 Loads (I, 0x03) and stores (S, 0x23)
| Instr | funct3 | Operation |
|---|---|---|
| `lb` | 000 | `rd = sext(M8[rs1+imm])` |
| `lh` | 001 | `rd = sext(M16[rs1+imm])` |
| `lw` | 010 | `rd = M32[rs1+imm]` |
| `lbu` | 100 | `rd = zext(M8[rs1+imm])` |
| `lhu` | 101 | `rd = zext(M16[rs1+imm])` |
| `sb` | 000 | `M8[rs1+imm] = rs2[7:0]` |
| `sh` | 001 | `M16[rs1+imm] = rs2[15:0]` |
| `sw` | 010 | `M32[rs1+imm] = rs2` |

### 2.5 Control transfer
| Instr | Fmt | Encoding | Operation |
|---|---|---|---|
| `jal rd, off` | J | op=0x6F | `rd = pc+4; pc += off` |
| `jalr rd, off(rs1)` | I | op=0x67 f3=000 | `t = pc+4; pc = (rs1+off) & ~1; rd = t` |
| `beq` | B | op=0x63 f3=000 | `if rs1 == rs2: pc += off` |
| `bne` | B | f3=001 | `if rs1 != rs2` |
| `blt` | B | f3=100 | `if rs1 <ₛ rs2` |
| `bge` | B | f3=101 | `if rs1 ≥ₛ rs2` |
| `bltu` | B | f3=110 | `if rs1 <ᵤ rs2` |
| `bgeu` | B | f3=111 | `if rs1 ≥ᵤ rs2` |

### 2.6 Memory ordering and system
| Instr | Encoding | Operation |
|---|---|---|
| `fence pred, succ` | op=0x0F f3=000 | Order memory/IO accesses |
| `fence.tso` | `fence` with fm=1000, rw,rw | TSO fence |
| `pause` (Zihintpause) | `fence w, 0` | Spin-wait hint |
| `ecall` | 0x00000073 | Environment call (trap) |
| `ebreak` | 0x00100073 | Breakpoint (trap) |

---

## 3. Zifencei and Zicsr

| Instr | Encoding (op=0x73 unless noted) | Operation |
|---|---|---|
| `fence.i` | op=0x0F f3=001 | Synchronize I-stream with stores |
| `csrrw rd, csr, rs1` | f3=001 | `t = CSR; CSR = rs1; rd = t` |
| `csrrs rd, csr, rs1` | f3=010 | `t = CSR; CSR \|= rs1; rd = t` |
| `csrrc rd, csr, rs1` | f3=011 | `t = CSR; CSR &= ~rs1; rd = t` |
| `csrrwi rd, csr, uimm` | f3=101 | Same as `csrrw`, using a 5-bit zero-extended immediate |
| `csrrsi rd, csr, uimm` | f3=110 | Same as `csrrs`, immediate |
| `csrrci rd, csr, uimm` | f3=111 | Same as `csrrc`, immediate |

**Zicntr counters** (read-only, via `csrr`): `cycle` 0xC00, `time` 0xC01, `instret` 0xC02, plus `cycleh/timeh/instreth` 0xC80–0xC82 on RV32. Machine versions: `mcycle` 0xB00, `minstret` 0xB02. **Zihpm**: `hpmcounter3–31` 0xC03–0xC1F.

---

## 4. M: Multiply / divide (OP, 0x33, funct7 = 0000001)

| Instr | funct3 | Operation |
|---|---|---|
| `mul` | 000 | `rd = (rs1 × rs2)[31:0]` |
| `mulh` | 001 | `rd = (rs1ₛ × rs2ₛ)[63:32]` |
| `mulhsu` | 010 | `rd = (rs1ₛ × rs2ᵤ)[63:32]` |
| `mulhu` | 011 | `rd = (rs1ᵤ × rs2ᵤ)[63:32]` |
| `div` | 100 | `rd = rs1 /ₛ rs2` (÷0 → −1) |
| `divu` | 101 | `rd = rs1 /ᵤ rs2` (÷0 → 2³²−1) |
| `rem` | 110 | `rd = rs1 %ₛ rs2` (÷0 → rs1) |
| `remu` | 111 | `rd = rs1 %ᵤ rs2` (÷0 → rs1) |

**Zmmul** is just the four `mul*` instructions, without divide.

---

## 5. A: Atomics (op=0x2F, funct3=010 for `.w`, `aq`/`rl` bits in [26:25])

| Instr | funct5 [31:27] | Sub-ext | Operation |
|---|---|---|---|
| `lr.w rd, (rs1)` | 00010 | Zalrsc | Load and set a reservation |
| `sc.w rd, rs2, (rs1)` | 00011 | Zalrsc | Store if the reservation holds; `rd = 0` on success |
| `amoswap.w` | 00001 | Zaamo | `rd = M[rs1]; M[rs1] = rs2` |
| `amoadd.w` | 00000 | Zaamo | `… M = rd + rs2` |
| `amoxor.w` | 00100 | Zaamo | `… M = rd ^ rs2` |
| `amoand.w` | 01100 | Zaamo | `… M = rd & rs2` |
| `amoor.w` | 01000 | Zaamo | `… M = rd \| rs2` |
| `amomin.w` | 10000 | Zaamo | Signed min |
| `amomax.w` | 10100 | Zaamo | Signed max |
| `amominu.w` | 11000 | Zaamo | Unsigned min |
| `amomaxu.w` | 11100 | Zaamo | Unsigned max |

Ordering suffixes: `.aq`, `.rl`, `.aqrl` (e.g. `amoadd.w.aqrl`).

---

## 6. Zicond: Conditional zero (OP, funct7 = 0000111)

| Instr | funct3 | Operation |
|---|---|---|
| `czero.eqz rd, rs1, rs2` | 101 | `rd = (rs2 == 0) ? 0 : rs1` |
| `czero.nez rd, rs1, rs2` | 111 | `rd = (rs2 != 0) ? 0 : rs1` |

---

## 7. B: Bit manipulation (B = Zba + Zbb + Zbs; Zbc is separate)

### 7.1 Zba: Address generation (OP, funct7 = 0010000)
| Instr | funct3 | Operation |
|---|---|---|
| `sh1add` | 010 | `rd = (rs1 << 1) + rs2` |
| `sh2add` | 100 | `rd = (rs1 << 2) + rs2` |
| `sh3add` | 110 | `rd = (rs1 << 3) + rs2` |

### 7.2 Zbb: Basic bit manipulation
| Group | Instr | Encoding | Operation |
|---|---|---|---|
| Logic with negate | `andn` | OP f7=0100000 f3=111 | `rd = rs1 & ~rs2` |
| | `orn` | OP f7=0100000 f3=110 | `rd = rs1 \| ~rs2` |
| | `xnor` | OP f7=0100000 f3=100 | `rd = ~(rs1 ^ rs2)` |
| Count | `clz` | OP-IMM 0110000_00000 f3=001 | Count leading zeros |
| | `ctz` | OP-IMM 0110000_00001 f3=001 | Count trailing zeros |
| | `cpop` | OP-IMM 0110000_00010 f3=001 | Population count |
| Min/max | `min` | OP f7=0000101 f3=100 | Signed min |
| | `minu` | f3=101 | Unsigned min |
| | `max` | f3=110 | Signed max |
| | `maxu` | f3=111 | Unsigned max |
| Sign/zero extend | `sext.b` | OP-IMM 0110000_00100 f3=001 | `rd = sext(rs1[7:0])` |
| | `sext.h` | OP-IMM 0110000_00101 f3=001 | `rd = sext(rs1[15:0])` |
| | `zext.h` | OP f7=0000100 f3=100 rs2=0 | `rd = zext(rs1[15:0])` |
| Rotate | `rol` | OP f7=0110000 f3=001 | Rotate left |
| | `ror` | OP f7=0110000 f3=101 | Rotate right |
| | `rori` | OP-IMM f7=0110000 f3=101 | Rotate right by immediate |
| Byte ops | `orc.b` | OP-IMM imm=0x287 f3=101 | Each byte = 0xFF if nonzero, otherwise 0x00 |
| | `rev8` | OP-IMM imm=0x698 f3=101 | Byte-reverse the register |

### 7.3 Zbc: Carry-less multiply (OP, funct7 = 0000101)
| Instr | funct3 | Operation |
|---|---|---|
| `clmul` | 001 | Low half of the carry-less product |
| `clmulr` | 010 | Reversed carry-less product (bits 62:31) |
| `clmulh` | 011 | High half of the carry-less product |

### 7.4 Zbs: Single-bit ops
| Instr | Encoding | Operation |
|---|---|---|
| `bset` / `bseti` | f7=0010100 f3=001 | `rd = rs1 \| (1 << idx)` |
| `bclr` / `bclri` | f7=0100100 f3=001 | `rd = rs1 & ~(1 << idx)` |
| `binv` / `binvi` | f7=0110100 f3=001 | `rd = rs1 ^ (1 << idx)` |
| `bext` / `bexti` | f7=0100100 f3=101 | `rd = (rs1 >> idx) & 1` |

The register forms use OP (0x33). The `*i` forms use OP-IMM (0x13).

---

## 8. Scalar crypto (Zk*)

### 8.1 Zbkb: Bit manipulation for crypto
Also includes `rol`, `ror`, `rori`, `andn`, `orn`, `xnor`, and `rev8` (shared with Zbb). Adds:

| Instr | Encoding | Operation |
|---|---|---|
| `pack` | OP f7=0000100 f3=100 | `rd = {rs2[15:0], rs1[15:0]}` |
| `packh` | OP f7=0000100 f3=111 | `rd = zext({rs2[7:0], rs1[7:0]})` |
| `brev8` | OP-IMM imm=0x687 f3=101 | Reverse the bits within each byte |
| `zip` (RV32) | OP-IMM 0000100_01111 f3=001 | Interleave the upper and lower halves |
| `unzip` (RV32) | OP-IMM 0000100_01111 f3=101 | De-interleave |

### 8.2 Zbkc / Zbkx
| Instr | Ext | Encoding | Operation |
|---|---|---|---|
| `clmul`, `clmulh` | Zbkc | As in Zbc | Carry-less multiply |
| `xperm4` | Zbkx | OP f7=0010100 f3=010 | Nibble lookup: `rs1` is the table, `rs2` holds the indices |
| `xperm8` | Zbkx | OP f7=0010100 f3=100 | Byte lookup |

### 8.3 Zkne / Zknd: AES (RV32, OP f3=000, funct7 = `bs‖xxxxx`)
| Instr | Ext | funct7[4:0] | Operation |
|---|---|---|---|
| `aes32esi rd, rs1, rs2, bs` | Zkne | 10001 | Encrypt, final-round S-box on byte `bs` |
| `aes32esmi` | Zkne | 10011 | Encrypt, middle round (S-box + MixColumns) |
| `aes32dsi` | Zknd | 10101 | Decrypt, final round |
| `aes32dsmi` | Zknd | 10111 | Decrypt, middle round |

### 8.4 Zknh: SHA-2 (RV32)
| Instr | Encoding | Operation |
|---|---|---|
| `sha256sum0` | OP-IMM 0001000_00000 f3=001 | Σ0 |
| `sha256sum1` | OP-IMM 0001000_00001 f3=001 | Σ1 |
| `sha256sig0` | OP-IMM 0001000_00010 f3=001 | σ0 |
| `sha256sig1` | OP-IMM 0001000_00011 f3=001 | σ1 |
| `sha512sum0r` | OP f7=0101000 f3=000 | SHA-512 Σ0 (two-register half) |
| `sha512sum1r` | OP f7=0101001 f3=000 | SHA-512 Σ1 |
| `sha512sig0l` / `sha512sig0h` | OP f7=0101010 / 0101110 | SHA-512 σ0 low / high |
| `sha512sig1l` / `sha512sig1h` | OP f7=0101011 / 0101111 | SHA-512 σ1 low / high |

The shorthand names for these groups are **Zkn** = Zbkb + Zbkc + Zbkx + Zkne + Zknd + Zknh. STARBUG has all of Zkn.

---

## 9. Cache-block management (Zicbo*) and privileged instructions

### 9.1 Zicbom / Zicboz (MISC-MEM 0x0F, f3=010, rd=0, address in rs1)
| Instr | imm[11:0] | Ext | Operation |
|---|---|---|---|
| `cbo.inval (rs1)` | 0x000 | Zicbom | Invalidate the block |
| `cbo.clean (rs1)` | 0x001 | Zicbom | Write the block back |
| `cbo.flush (rs1)` | 0x002 | Zicbom | Write back and invalidate |
| `cbo.zero (rs1)` | 0x004 | Zicboz | Zero the whole block |

### 9.2 Zicbop: Prefetch hints (`ori x0, rs1, off` with imm[4:0] selecting the type)
| Instr | imm[4:0] |
|---|---|
| `prefetch.i off(rs1)` | 00000 |
| `prefetch.r off(rs1)` | 00001 |
| `prefetch.w off(rs1)` | 00011 |

### 9.3 Privileged (SYSTEM 0x73)
| Instr | Encoding | Operation |
|---|---|---|
| `mret` | 0x30200073 | Return from an M-mode trap |
| `sret` | 0x10200073 | Return from an S-mode trap |
| `wfi` | 0x10500073 | Wait for interrupt |
| `sfence.vma rs1, rs2` | f7=0001001 | Flush TLB (by vaddr/ASID) |
| `sinval.vma` | f7=0001011 | Svinval: invalidate without ordering |
| `sfence.w.inval` | f7=0001100 rs2=00000 | Svinval: order prior stores before invalidation |
| `sfence.inval.ir` | f7=0001100 rs2=00001 | Svinval: order invalidation before later accesses |

---

## 10. Compressed 16-bit instructions (Zca, Zcb, Zcf)

Bits [1:0] select the **quadrant** (C0 = 00, C1 = 01, C2 = 10; 11 means a 32-bit instruction). `rd'`/`rs1'`/`rs2'` are 3-bit fields that map to x8–x15 (s0, s1, a0–a5).

### 10.1 Zca, by quadrant (RV32)
| Quadrant | Instr | Expands to |
|---|---|---|
| **C0** | `c.addi4spn rd', nzuimm` | `addi rd', sp, nzuimm` |
| | `c.lw rd', off(rs1')` | `lw` |
| | `c.sw rs2', off(rs1')` | `sw` |
| **C1** | `c.nop` | `addi x0, x0, 0` |
| | `c.addi rd, nzimm` | `addi rd, rd, nzimm` |
| | `c.jal off` (RV32 only) | `jal ra, off` |
| | `c.li rd, imm` | `addi rd, x0, imm` (**`rd=x0` is a HINT: the STARBUG bundle header**) |
| | `c.addi16sp nzimm` | `addi sp, sp, nzimm` |
| | `c.lui rd, nzimm` | `lui rd, nzimm` |
| | `c.srli rd', shamt` / `c.srai` / `c.andi` | `srli`/`srai`/`andi rd', rd', …` |
| | `c.sub` / `c.xor` / `c.or` / `c.and rd', rs2'` | `op rd', rd', rs2'` |
| | `c.j off` | `jal x0, off` |
| | `c.beqz rs1', off` / `c.bnez` | `beq/bne rs1', x0, off` |
| **C2** | `c.slli rd, shamt` | `slli rd, rd, shamt` |
| | `c.lwsp rd, off` | `lw rd, off(sp)` |
| | `c.jr rs1` | `jalr x0, 0(rs1)` |
| | `c.mv rd, rs2` | `add rd, x0, rs2` |
| | `c.ebreak` | `ebreak` |
| | `c.jalr rs1` | `jalr ra, 0(rs1)` |
| | `c.add rd, rs2` | `add rd, rd, rs2` |
| | `c.swsp rs2, off` | `sw rs2, off(sp)` |

### 10.2 Zcb: Extra compressed instructions
| Instr | Expands to | Requires |
|---|---|---|
| `c.lbu`, `c.lhu`, `c.lh` | `lbu`/`lhu`/`lh` | — |
| `c.sb`, `c.sh` | `sb`/`sh` | — |
| `c.zext.b rd'` | `andi rd', rd', 0xFF` | — |
| `c.sext.b`, `c.sext.h`, `c.zext.h` | Zbb `sext.b`/`sext.h`/`zext.h` | Zbb |
| `c.not rd'` | `xori rd', rd', -1` | — |
| `c.mul rd', rs2'` | `mul rd', rd', rs2'` | M or Zmmul |

### 10.3 Zcf: Compressed single-precision FP load/store (RV32, `lm/fp` only)
`c.flw`, `c.fsw`, `c.flwsp`, `c.fswsp`. **Zcd** (`c.fld`, `c.fsd`, `c.fldsp`, `c.fsdsp`) is disabled in both configs.

---

## 11. Floating point: F / D / Zfh / Zfa (enabled on `lm/fp` only)

The suffix gives the format: `.s` = single (F, fmt=00), `.d` = double (D, fmt=01), `.h` = half (Zfh, fmt=10). Rounding mode `rm` sits in funct3 (`rne rtz rdn rup rmm`, or `dyn` = use `frm`).

| Group | Instructions (`.s` shown; `.d`/`.h` analogs exist) | Encoding |
|---|---|---|
| Load / store | `flw`, `fsw` · `fld`, `fsd` · `flh`, `fsh` | LOAD-FP/STORE-FP, f3 = 010 / 011 / 001 |
| Arithmetic | `fadd.s`, `fsub.s`, `fmul.s`, `fdiv.s`, `fsqrt.s` | OP-FP f7 = 00000xx / 00001xx / 00010xx / 00011xx / 01011xx |
| Fused multiply-add | `fmadd.s`, `fmsub.s`, `fnmsub.s`, `fnmadd.s` | R4, opcodes 0x43 / 0x47 / 0x4B / 0x4F |
| Sign injection | `fsgnj.s`, `fsgnjn.s`, `fsgnjx.s` | f7=00100xx, f3 = 000/001/010 |
| Min / max | `fmin.s`, `fmax.s` | f7=00101xx, f3 = 000/001 |
| Compare (→ int rd) | `feq.s`, `flt.s`, `fle.s` | f7=10100xx, f3 = 010/001/000 |
| Classify | `fclass.s` | f7=11100xx f3=001 |
| FP → int | `fcvt.w.s`, `fcvt.wu.s` | f7=11000xx, rs2 = 0/1 |
| int → FP | `fcvt.s.w`, `fcvt.s.wu` | f7=11010xx, rs2 = 0/1 |
| Bit moves | `fmv.x.w` (FP→int), `fmv.w.x` (int→FP); `fmv.x.h`/`fmv.h.x` for Zfh | f7 = 11100xx / 11110xx, f3=000 |
| Format conversion | `fcvt.s.d`, `fcvt.d.s`, `fcvt.h.s`, `fcvt.s.h`, `fcvt.h.d`, `fcvt.d.h` | f7=01000xx, rs2 = source fmt |
| **Zfa** additions | `fli.*` (load FP constant), `fminm.*`/`fmaxm.*` (NaN-propagating), `fround.*`/`froundnx.*`, `fleq.*`/`fltq.*` (quiet compares), `fcvtmod.w.d`, RV32: `fmvh.x.d`, `fmvp.d.x` | — |

RV32 D has no `fmv.x.d` or `fmv.d.x`; moving a double needs Zfa's `fmvh.x.d`/`fmvp.d.x` or a pass through memory.

FP CSRs: `fflags` 0x001, `frm` 0x002, `fcsr` 0x003.

---

## 12. Common pseudo-instructions (assembler only)

| Pseudo | Real instruction(s) |
|---|---|
| `nop` | `addi x0, x0, 0` |
| `li rd, imm` | `addi` or `lui` + `addi` |
| `la rd, sym` | `auipc` + `addi` |
| `mv rd, rs` | `addi rd, rs, 0` |
| `not rd, rs` | `xori rd, rs, -1` |
| `neg rd, rs` | `sub rd, x0, rs` |
| `seqz` / `snez` / `sltz` / `sgtz` | `sltiu rd,rs,1` / `sltu rd,x0,rs` / `slt rd,rs,x0` / `slt rd,x0,rs` |
| `beqz` / `bnez` / `blez` / `bgez` / `bltz` / `bgtz` | Branch against `x0` |
| `bgt` / `ble` / `bgtu` / `bleu` | `blt` / `bge` / `bltu` / `bgeu` with operands swapped |
| `j off` / `jr rs` / `ret` | `jal x0` / `jalr x0, 0(rs)` / `jalr x0, 0(ra)` |
| `call sym` / `tail sym` | `auipc` + `jalr ra` / `auipc` + `jalr x0` |
| `csrr rd, csr` / `csrw csr, rs` / `csrs` / `csrc` | `csrrs rd,csr,x0` / `csrrw x0,csr,rs` / … |
| `rdcycle` / `rdtime` / `rdinstret` | `csrrs rd, cycle/time/instret, x0` |
| `fmv.s` / `fabs.s` / `fneg.s` | `fsgnj` / `fsgnjx` / `fsgnjn` with rs1 = rs2 |
| `zext.b rd, rs` | `andi rd, rs, 0xFF` |

---

## Appendix A: RV64-only instructions (not in STARBUG)

| Ext | Instructions |
|---|---|
| I | `lwu`, `ld`, `sd`, `addiw`, `slliw`, `srliw`, `sraiw`, `addw`, `subw`, `sllw`, `srlw`, `sraw` |
| M | `mulw`, `divw`, `divuw`, `remw`, `remuw` |
| A | `lr.d`, `sc.d`, `amo*.d` |
| Zba | `add.uw`, `sh1add.uw`, `sh2add.uw`, `sh3add.uw`, `slli.uw` |
| Zbb | `clzw`, `ctzw`, `cpopw`, `rolw`, `rorw`, `roriw` |
| Zkn | `aes64*`, `sha512sum0/1`, `sha512sig0/1` (replace the RV32 `aes32*` and `sha512*h/l/r`) |
| C | `c.ld`, `c.sd`, `c.ldsp`, `c.sdsp`, `c.addiw`, `c.addw`, `c.subw` (`c.jal` is removed); Zcb `c.zext.w` |
| F/D | `fcvt.l[u].s/d`, `fcvt.s/d.l[u]`, `fmv.x.d`, `fmv.d.x` |

## Appendix B: Instruction counts (RV32)

| Ext | Count |
|---|---|
| I | 40 (37 unprivileged ops + `fence`, `ecall`, `ebreak`) |
| Zifencei / Zicsr | 1 / 6 |
| M | 8 |
| A | 11 |
| Zicond | 2 |
| Zba / Zbb / Zbc / Zbs | 3 / 18 / 3 / 8 |
| Zbkb (new only) / Zbkx | 5 / 2 |
| Zkne + Zknd / Zknh | 4 / 10 |
| Zicbom + Zicboz / Zicbop | 4 / 3 (hints) |
| F / D | 26 / 26 |
