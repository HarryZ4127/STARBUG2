# STARBUG 2 execute-packet header: interpretation

This interprets the team's one-page proposal, [`spec/starbug2-custom0-header.pdf`](../spec/starbug2-custom0-header.pdf),
written on 2026-10-03. The proposal replaces Starbug 1's 16-bit `c.li x0, N` hint with a 32-bit header
and adds C6x-style predication. Where the PDF is silent, this doc says so rather than guessing.

## Layout
```
 31  29 28 27  25 24 23  21 20 19  17 16 15 14 13 12 11     7 6      0
|creg0|z0|creg1|z1|creg2|z2|creg3|z3|p0|p1|p2|p3| reserved | 0001011 |
|<- op 0 ->|<- op 1 ->|<- op 2 ->|<- op 3 ->| parallel  |  5 bits  | custom-0|
```

| Field | Bits | Meaning |
|---|---|---|
| opcode | 6:0 | `0001011`, which is **custom-0**. Bits [1:0] = 11, so it is a 32-bit instruction. A core without the extension raises illegal-instruction |
| reserved | 11:7 | Would be `rd`. Freed when post-increment addressing moved into a custom-1 load/store format. Kept for a wider slot count: 8 slots would need 32 bits of creg/z and so a second header word |
| p0..p3 | 15:12 | pᵢ = 1 means op i+1 issues in parallel with op i. There are 3 boundaries among 4 ops, so p3 is spare |
| creg/z | 31:16 | One nibble per op, in the C6x positions: 3-bit creg, then 1-bit z |

### Predicate nibble
- `creg = 000, z = 0`: unconditional.
- `creg = 1..7`: x9–x15. The mapping x(8 + creg) is **assumed; the PDF doesn't state it**.
- `z = 0`: execute if the register is nonzero. `z = 1`: execute if it is zero.
- `creg = 000, z = 1`: **undefined** in the PDF. That leaves one spare code per slot; C6x also treats it
  as reserved.

The PDF figure draws bit 7 as a separate blank cell, while the text says reserved covers 11:7.

## Packet semantics, as stated in the PDF
1. There is no mode. The header's presence makes the following ops a packet. You enter by branching to
   a header; execution leaves packet form when there is no header.
2. Op lengths are not in the header. Each op declares its own width in bits [1:0], and the decoder
   walks the packet. The maximum is 4 + 4×4 = 20 bytes.
3. All ops read their sources at the start of the cycle and commit at the end, so a swap within a
   packet works.
4. At most one memory op per packet (single load/store data path).
5. Two predicated ops may write the same destination if their predicates are complementary (same
   register, opposite z).

## Worked example
```asm
    .word 0x0230C00B          # header
    lw    t0, 0(a0)           # op0: unconditional (the one memory op)
    addi  a1, a1, 1           # op1: [x9 != 0]  creg=001 z=0 -> nibble 0x2
    addi  a1, a1, -1          # op2: [x9 == 0]  creg=001 z=1 -> nibble 0x3
```
The header decodes as op nibbles 0, 2, 3, 0; p-bits `1100` (op0 ∥ op1 ∥ op2); reserved 0; opcode
`0x0B`. Both writes to `a1` are legal because their predicates are complementary.

## Comparison with Starbug 1
| | Starbug 1 (`c.li x0, N`) | STARBUG 2 (custom-0) |
|---|---|---|
| Header size | 16 bits | 32 bits |
| On a core without the extension | No-op, so binaries still run as scalar code | Illegal-instruction trap |
| Op count | N in the immediate | **Not encoded** (open question 1) |
| Predication | none | 7 predicate registers, per op |
| Same-register writes in a bundle | Assumed not to happen | Allowed if predicates are complementary |
| Semantics in MPACT today | Sequential | Read-all-then-commit (needs a simulator change) |

## Open questions for the designer
1. **How many ops follow the header?** Two readings:
   - **(a)** Always 4, with p-bits splitting them into execute packets over several cycles, as in a
     C6x fetch packet. This conflicts with rule 3, and short packets would need padding `nop`s.
   - **(b)** The packet ends at the first p = 0: `1110` = 4 ops, `1100` = 3, `1000` = 2, `0000` = 1.
     This fits rule 3 and explains why p3 is spare. But a non-thermometer pattern such as `1010` then
     needs a meaning, probably illegal.

   Leaning (b). The team's action item went to Rocky: ask Dr. Simar about the P-bits.
2. **Predicate registers vs. the ABI.** x9 is `s1`, and x10–x15 are `a0`–`a5`, the argument and return
   registers. The likely motive is that x8–x15 are the registers 16-bit compressed instructions can
   address (`rd'`), which makes setting a predicate cheap. The calling-convention cost should be stated
   explicitly.
3. **Traps and branches inside a packet.** Is commit atomic? Does `mepc` point at the header? Which
   slots may hold a branch, and does it take effect after the packet?
4. **Non-complementary double writes.** If both fire, is that a trap or undefined behaviour?
5. **Alignment.** Starbug 1 requires a bundle to fit in one I-cache line. Does a packet of up to 20
   bytes have to? In the trade study, about 9% of bundles straddle a 64 B line.

## Consequences for the RTL and MPACT
- **No safe fallback.** The Starbug 1 trick of running a bundle as scalar code when it can't be formed
  becomes wrong. Hardware must handle line-crossing packets, or the toolchain must forbid them. See
  [rationale §6](01-design-rationale.md#6-why-starbug-2-moves-the-header-from-a-hint-to-a-custom-0-opcode).
- **Predicate reads.** Up to 4 predicate registers must be read in Decode, which needs more read ports
  or a small dedicated predicate copy. Each lane's write enable is gated by its predicate.
- **Forwarding.** The current forwarding logic assumes one writer per register. With complementary
  same-destination writes, the predicate must choose the forwarded value.
- **PC bug gets worse.** Per-lane PCs matter more: with a 4-byte header, a shared header PC puts a
  lane-0 branch 4 bytes off. See [rationale §5](01-design-rationale.md#5-every-lane-needs-its-own-pc).
- **MPACT changes.**
  - Its decoder must trigger on custom-0 instead of `kCliHint` and derive the op count from the
    p-bits.
  - `RiscVExVLIW` must snapshot the sources, evaluate predicates, buffer the writes, and enforce one
    memory op per packet.
- **Cost (trade study).** The header costs 0 cycles. Hot code grows by 14%. Predication saves about 0
  on GCC `-O3` code.
