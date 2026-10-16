# Starbug 1 RTL: fetch, decode, lanes, memory system

How the current RTL handles a bundle (16-bit `c.li x0, N` header). This comes from code reading of
[rvru/cvw](https://github.com/rvru/cvw) `origin/lm/fp`, whose source files were last touched at
`93eb1c3d8` (branch tip `a3daf96b4`), on 2026-10-03. **It has not been simulated.** Line numbers refer
to `lm/fp`.

`main` (`8e63d1d86`) has the same structure. `lm/fp` refactors the extraction slicing (`bundle_window`)
and adds per-lane illegal-instruction signals for the FP work.

```
 F (fetch)                         D (decode)                         E / M / W
 I$ line -> c.li x0,N? -> walk  -> 4 instr regs -> 4x decompress -> ieu   (lane 0) -> LSU, CSR, branch, trap
            PC += bundle bytes    VLIWValidD[3:0]   lane muxes        ieu_1 (lane 1)    own MDU + FPU per lane
                                  VLIWModeD                           ieu_2 (lane 2)    shared 8R/4W regfile
                                                                      ieu_3 (lane 3)    one global stall/flush
```

## Fetch (`src/ifu/ifu.sv`)
- **Header detection (L344–354).** Works on the raw halfword before decompression: quadrant 01,
  funct3 010, rd = 0, imm ≠ 0. It reads `PostSpillInstrRawF`, so a header straddling a fetch word is
  still seen correctly.
- **Bundle acceptance (L395).** A bundle is formed only if all of these hold:
  - 1 ≤ N ≤ 4;
  - `!IFUStallF`;
  - `CacheableF`;
  - `!SelIROM`;
  - `!SelSpillNextF`.
- **Extraction walk (L399–508).** One combinational pass:
  1. Shift the **whole I-cache line** (`ReadDataLine`, 512 bits) so the byte after the header is at
     bit 0.
  2. For each op: `[1:0] == 11` means take 32 bits, otherwise 16. Set `VLIWValidF[i]` and add the size
     to `BundleBytesF`.
  3. If any op crosses the end of the line, abort the bundle (`bundle_ok = 0`).
- **PC (L592).** `AdjustedPCPlus2or4F = BundleReadyF ? PCF + BundleBytesF : PCPlus2or4F`, so the
  whole bundle is skipped in one step. This value feeds the branch predictor's `PCPlus2or4F` input.
- **Fallback.** If the bundle is rejected, the header runs as a plain `c.li x0` no-op, and the
  following ops run **one at a time as scalar code**. This is why the HINT encoding is safe for
  Starbug 1. It is no longer safe once semantics change: see
  [rationale §6](01-design-rationale.md#6-why-starbug-2-moves-the-header-from-a-hint-to-a-custom-0-opcode).
- **Uncached and no-cache paths** (L536–577) never form bundles.

## Fetch → Decode
- **Pipeline registers.** Four instruction registers, plus `VLIWValidD` and `VLIWModeD` (L668–676).
- **Decompression.** Four extra `decompress` instances, one per lane (L692–723). The `? :` around
  their inputs is a no-op.
- **Lane muxes** (`src/wally/wallypipelinedcore.sv` L221–229):
  - In bundle mode, lane 0 gets op 0 **instead of the header**. The header itself never executes.
  - In scalar mode, lane 0 gets `InstrD` and lanes 1–3 get `nop`.
  - Invalid lanes get `nop` with `LaneValidD = 0`.

## Execute
- **Four `ieu` instances**, each with its own controller and datapath (L360, 408, 460, 512). Each
  lane also has its own MDU (L686–727) and its own FPU (L730+, `lm/fp`).
- **Register file** (`src/ieu/regfile_widened.sv`): 8 read ports and 4 write ports, writing on
  negedge. If two lanes write the same register, the writes are applied in order and **lane 3 wins**.
- **Parallel semantics come for free.** All lanes read the register file in Decode together. There is
  no intra-bundle RAW/WAW check; `controller.sv` L505 *assumes* none.
- **Forwarding** (`controller.sv` L512–608): each lane checks every lane's M and W destinations and
  muxes in the matching result.
- **Stalls** (L634–684):
  - `MatchDE_0..3` detects a dependence on the previous bundle in any lane.
  - Load/SC (`LoadStallD`), MDU, CSR-read and FCvtInt results from any lane can stall.
  - Store followed by load stalls (L675).
  - All lanes' structural stalls are ORed into **one** hazard unit (`wallypipelinedcore.sv` L632–651),
    so every lane stalls and flushes in lockstep.

## What each lane can do
| | Lane 0 | Lanes 1–3 |
|---|---|---|
| ALU / bit manipulation / Zicond / MDU / FP | yes | yes |
| Load/store/AMO | yes (only lane wired to the LSU) | **load writes 0**: `ReadDataW_1..3 = '0` (L603) |
| Branch/jump redirect | yes | **ignored**: `.PCSrcE`/`.IEUAdrE` commented out (L414, 434, …) |
| CSR / privileged | yes | **ignored**: the priv unit sees only lane 0's `InstrM`/`CSRWriteM` |
| Illegal-instruction trap | yes | **becomes a nop**: controls zeroed (`controller.sv` L332), nothing reaches the priv unit |

## Memory system (`config/starbug/config.vh`, stock Wally)
- **One `lsu` instance**, fed only by lane 0. Commit `78c66588e` reads "first bundles work in parallel
  but only with one load".
- **Caches.** D$ and I$ are both 4-way, 16 KB, 64 B lines; the D$ is write-back. Every way is a
  single-port SRAM (`ram1p1rwe`/`ram1p1rwbe`, `cacheway.sv`), so one access per cycle. The team's only
  cache change is exporting the I$'s 512-bit `ReadDataLine` for bundle extraction.
- **Bus.** The EBU arbitrates the IFU and LSU onto one 32-bit AHB bus, going to RAM at 0x8000_0000
  (128 MB). DTIM, IROM and misaligned access are off.
- **Branch predictor is off.** `BPRED_SUPPORTED = 0` in `config/starbug`, while `config/rv32gc` has
  gshare. See [rationale §10e](01-design-rationale.md#10e-the-140-rtl-headline-compares-unequal-configs).
- **What existing code does.** All 513 memory ops in `lm/fp`'s `examples/C/embench_starbug` hand-VLIW
  kernels are in lane 0. The older MPACT-era kernels put 16 in worker lanes.

## Suspected bugs (code reading; confirm in `wsim`)
1. **All lanes get `PCE`, the header's PC** (`.PCE` on every `ieu` instance).
   - `auipc` in a bundle is wrong.
   - A lane-0 branch target lands **2 bytes early** (4 bytes with a STARBUG 2 header), because the
     assembler encoded the offset from the branch's own address.
   - Rationale: [§5](01-design-rationale.md#5-every-lane-needs-its-own-pc).
2. **`jal` in a bundle links to header + 2.** `PCLinkE = PCE + (CompressedE ? 2 : 4)`, and
   `CompressedE` belongs to the 16-bit header. So `ra` points at op 0, and the return re-runs the
   bundle's ops as scalar code.
3. **Worker lanes fail silently.** See the table above and [§4](01-design-rationale.md#4-silent-drops-in-worker-lanes-should-trap).
4. **Timing risk.** The full-line shift plus a variable-length 4-op walk in one cycle is a likely
   critical path in fetch.

A test plan for confirming these is in [`06-open-questions.md`](06-open-questions.md).
