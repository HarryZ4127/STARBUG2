# The STARBUG compiler: what exists, what each hardware change needs, and how to get the best code

Written 2026-10-16 from a read-through of Leo's compiler and the model experiments in
[`07-design-space-exploration.md`](07-design-space-exploration.md). Numbers marked *model* come from
`mpact_run/dse.py` (trace-driven model, ~4% median error against RTL for the hand-VLIW runs).
Numbers marked *RTL* are Leo's Questa measurements, quoted from his repo.

## 1. What exists today

**Source.** The overlay and docs are in [leomarek/starbug-llvm](https://github.com/leomarek/starbug-llvm)
@ `5b35333`. The passes themselves are in the LLVM fork
[leomarek/llvm-project](https://github.com/leomarek/llvm-project), branch `codex/starbug-vliw`
@ `4a1edc71`, under `llvm/lib/Target/RISCV/Starbug*`. Target: `--target=riscv32 -mcpu=starbug-vliw`.

**Pipeline** (`RISCVTargetMachine.cpp`):
```
IR:     loop unroll policy (RISCVTargetTransformInfo.cpp: factor 8, max 16, only for
        constant trip count, simple CFG, no calls)
pre-RA: [StarbugVLIWTraceScheduler]  off by default; only acts with -starbug-vliw-assume-no-memory-alias
        MachinePipeliner             "enabled" for starbug-vliw, but see gap G1: it never runs
RA, post-RA scheduler
pre-emit:   StarbugVLIWPacketizer    post-RA, per basic block: groups instructions, emits c.li x0,N
            BranchRelaxation, MakeCompressible
pre-emit 2: ... StarbugVLIWBundleLayout   last: splits bundles that straddle a 64 B I$ line
AsmPrinter: STARBUG_BUNDLE_HINT -> c.li zero,N
```

**The packetizer** (`StarbugVLIWPacketizer.cpp`, ~1000 lines):
- walks each basic block and grows one packet at a time from a 64-instruction look-ahead window;
- rejects RAW/WAW with the packet, and accepts WAR only while the packet is still in program order
  (so the bytes stay a correct scalar program: Starbug 1's fallback and stock RV32C cores run
  them sequentially);
- puts loads/stores, branches, CSRs and `auipc` in lane 0, with one memory op per packet;
- picks the candidate by a hand-tuned score: a memory op as packet anchor (+40), MAC-chain
  overlap, pointer bumps, scarce-lane ops first;
- treats calls, terminators, pseudos and side-effecting instructions as barriers.

**Status (RTL, Leo's five-benchmark A/B):** the compiler reaches 1.40× over `rv32gc` scalar against
1.47× for the hand-written kernels (95.6% of hand), and beats hand code on `rfft512`.
Leo's paper notes (`docs/paper/03-compiler.md`) already list the remaining gaps:
- no modulo scheduling;
- bundles can't span basic blocks;
- branches end a packet;
- a fixpoint branch-size model for the layout pass;
- the linker-relaxation interaction.

## 2. Gaps found in this read-through

### G1. `starbug-vliw` has no scheduling model, so the machine pipeliner never runs
`RISCVProcessors.td` defines the CPU as
`RISCVProcessorModel<"starbug-vliw", NoSchedModel, ...>`. Two consequences:

1. **No modulo scheduling.** `RISCVSubtarget::enableMachinePipeliner()` returns
   `getSchedModel().hasInstrSchedModel()` (`RISCVSubtarget.cpp:237`). `MachinePipeliner` checks that
   first and returns (`MachinePipeliner.cpp:375`). So `-starbug-vliw-enable-machine-pipeliner`,
   which defaults to on, is a **silent no-op**. That explains Leo's note that the flag "doesn't
   change these kernels by a single instruction". The note puts this down to CMSIS already
   unrolling; the pass never ran in the first place.
2. **Only generic latencies.** With no model, `TargetSchedModel` falls back to
   `TargetInstrInfo::defaultDefLatency`: loads get `MCSchedModel`'s default `LoadLatency` (4),
   and everything else 1. So the pre-RA `MachineScheduler` spreads loads from their uses.
   - FP adds, FMAs and multiplies (2 cycles on Wally) look free, so accumulator chains are packed
     back to back.
   - There are no resources, so it can't see that every load and store competes for one LSU.
   - The packetizer's `LatencyWeight*` fields are only score bonuses; it doesn't track cycles.

### G2. The packetizer chooses *which* ops go together, not *when* they issue
It is a packet-filler: an op may join as soon as its producers are in an earlier packet, whatever
their latency. On an in-order interlocked pipeline the stall then happens at run time. The model
reproduces this policy as `sched="packetizer"` and compares it with a latency-aware list
scheduler on the same code (§4).

### G3. Memory disambiguation is off by default
`AssumeNoMemoryAlias = false`. Without alias information, a load can't move above a store in the
same block. That matters most once there are two memory paths (§4).

### G4. The lane model is hard-coded to Starbug 1
`isLikelyLSUOp(...) && (PacketHasLSU || CandLane != 0)` is wired into both candidate loops.
There is no notion of a load-only lane, a second store path, banks, or an 8-wide fetch.
`starbug_vliw_lanes.yaml` describes lane classes, but it is **stale**: its worker lanes lack logic, FP
and move ops, which `StarbugVLIWConfig.cpp` now allows. `starbug-tests/compile_starbug.sh` still
defaults to unroll 32/128, against the measured knee of 8/16 that the C++ defaults use.

### G5. Hand-written kernels and compiler output are checked by different tools
`mpact_run/bundle_check.py` now checks the lane rules too (this commit). On the hand-written
Embench DSP kernels it finds two violations the old checker missed:
- **`arm_bitreversal_16`** (rfft/dct4 ELFs) has `sh x11,2(x31) | srli x16,x28,2 | slli x31,x16,1`.
  That is a RAW on `x16` inside one bundle: on the parallel lanes `slli` reads the old `x16`, a
  pointer.
  - In the 512-point builds this bundle straddles a 64 B line (`0x80000cb8`–`0x80000cc5`), so
    today's fetch unit declines it and runs it scalar: correct by accident of placement.
  - In the 2048-point builds (`0x80000730`) it does not straddle.
  - None of these benchmarks executes `arm_bitreversal_16` (trace count 0), so no measured result
    is affected. It is a latent bug, and with the STARBUG 2 header there is no scalar fallback to
    hide it.
- **`arm_dct4_f32`** has `fsw f15,-4(x29) | beq x30,x0,...`, a branch in slot 1. Per the RTL
  read-through, a worker-lane branch is ignored.
  - It runs once per call and is never taken in these runs (trace), so results are unaffected.
  - It is still an illegal schedule.

Leo's compiler output (`starbug-tests/build/fir_filter.o`, 209 bundles) is clean under the same
checker.

## 3. What each STARBUG 2 hardware change asks of the compiler

| Hardware change | Compiler work | Where in LLVM | Worth doing? (model, §4 and doc 07) |
|---|---|---|---|
| **32-bit custom-0 header** (p-bits, creg/z) | Emit a header *word* instead of `c.li`. Encode p-bits: with reading (b) of the header doc, p-bits = thermometer code of the packet length. Assembler/disassembler syntax for packets. Since a packet is no longer a valid scalar program, stop relying on the scalar-fallback invariant. That **frees WAR reordering** (§2.1 of Leo's notes), but packets must never be split by later passes | Make packets real `BUNDLE` MIs (`finalizeBundle`, as Hexagon does) so `BranchRelaxation`/`MakeCompressible`/`MoveMerge` can't insert between header and members. Then lower the bundle in `RISCVAsmPrinter`/`RISCVMCCodeEmitter`. New pseudo `STARBUG_PACKET` with the p-bit/creg immediate | Required. 0 cycles; +14% hot code size |
| **No scalar fallback for a line-straddling packet** | Either the layout pass guarantees no straddle (padding or splitting; a split now costs a 4 B header) or the fetch unit handles straddles | `StarbugVLIWBundleLayout.cpp`: needs the fixpoint branch-size model Leo lists as his top item, plus header-aware sizes | Hardware support is the better trade. Issuing straddling bundles scalar costs 9–10% on SB1 and 17–19% on SB2 in the model, before any layout pass (doc 07 §4); Leo's pass recovers 2–3% on RTL |
| **Predication (creg/z)** | If-conversion of short hammocks. Allocate predicates in x9–x15 (`creg` 1..7) and keep them live across the hammock. ABI clash with a0–a5 (header doc Q2) | `IfConverter`/`EarlyIfConverter` need `isPredicable`, `PredicateInstruction`, `isProfitableToIfCvt*` and `ClobbersPredicate` in `RISCVInstrInfo`. None exist today. The cost model needs the scheduling model (G1) | Low: ~0 on these kernels (only `fir_q15` has hammocks). Do last |
| **Second memory path** (LS + load-only) | Lane classes: lane 1 takes loads. ≤ 1 store per packet. Packets may hold two loads. Unrolling/pairing should put two independent loads in each packet | `StarbugVLIWConfig` lane classes, plus replace the hard-coded single-LSU test in the packetizer with a per-class port count. Better: express ports as `ProcResource`s in the new scheduling model and let a DFA packetizer enforce them | **Biggest hardware gain**: 1.27× (2 banks) / 1.33× (4 banks) / 1.44× (conflict-free) over SB1, with gshare + ZOL |
| **Interleaved banks** | Bank-aware pairing (only pair loads that are provably in different banks) and bank-aware placement (align or offset arrays so paired streams start in different banks). Stall on conflict for correctness | Pairing: an address-parity analysis on the two loads' base + offset (SCEV at IR level, or base register + immediate at MI level). Placement: `__attribute__((aligned, section(".bank1")))` plus linker-script sections, or X/Y memories as on C6x/DSP56k | 2 → 4 banks recovers about a third of the conflict loss; placement can recover the rest (doc 07 §2) |
| **Wider issue (8 lanes)** | `NumLanes = 8`. Unroll factor scales with lanes × latency; watch register pressure (32 GPRs/FPRs) | Unroll policy in TTI; register-pressure-aware scheduling (`MachineScheduler` with the new model) | ≤ 1% unless paired with four conflict-free memory ops per bundle (doc 07 §1) |
| **Zero-overhead loops** | Recognise counted innermost loops; emit loop-setup (count, start, end) and drop the backward branch | LLVM `HardwareLoops` IR pass (`TTI::isHardwareLoopProfitable`) plus a late MI pass that lowers the pseudo, as ARM `LowOverheadLoops` / PowerPC CTR loops / Hexagon `HardwareLoops` do. CORE-V's `Xcvhwlp` is a RISC-V encoding precedent | Moderate on unrolled code; large on plain `-O3` and with modulo scheduling (doc 07) |
| **Branch predictor** (gshare / BTFN) | None for gshare. For BTFN, keep loop back-edges backward and cold paths forward (block placement already does this) | `MachineBlockPlacement` | ~1.07×; no compiler work |
| **Post-increment loads/stores** (the custom-1 format mentioned in the header doc) | Fold pointer bumps into memory ops | Like CORE-V `XCVmem` patterns; `RISCVLoadStoreOpt` | Small: worker lanes are idle on SB1, so bumps are free today. It frees slots only on wide, memory-rich configs |
| **Trap on illegal lane use** (rationale §4) | None if the compiler is right; the checker becomes a test oracle | `bundle_check.py` / `tools/starbug_bundle_check.py` in CI | Correctness, not speed |

## 4. How to get the best performance: what the model says

Set up: the same traces and machine model as doc 07. Three scheduling policies are applied to the
same code:
1. **greedy packetizer:** in program order, memory op first, latency ignored, like G2.
2. **list scheduler:** latency-aware, critical-path priority, the model's default.
3. **list + modulo scheduling:** single-block loops run at their modulo-schedule lower bound
   MII = max(ResMII, RecMII), which is optimistic for register pressure.

Each policy runs with and without in-block alias information, on plain `-O3` and on unrolled code.
Speedups are geomeans over 16 kernels against `-O3` scalar code on RV32GC.

| code | alias info | scheduler | SB1 | SB2 candidate | 8-wide, 2 LS, 2 banks, gshare, ZOL |
|---|---|---|---|---|---|
| unrolled | yes | greedy packetizer | 1.53 | 1.96 | 1.98 |
| unrolled | yes | list scheduler | 1.95 | 2.47 | 2.62 |
| unrolled | yes | list + modulo scheduling | **2.09** | **2.88** | **3.01** |
| unrolled | no | greedy packetizer | 1.48 | 1.79 | 1.80 |
| unrolled | no | list scheduler | 1.74 | 2.05 | 2.07 |
| unrolled | no | list + modulo scheduling | 1.98 | 2.40 | 2.44 |
| `-O3` | yes | greedy packetizer | 1.16 | 1.62 | 1.64 |
| `-O3` | yes | list scheduler | 1.24 | 1.70 | 1.71 |
| `-O3` | yes | list + modulo scheduling | 1.54 | 2.47 | 2.81 |
| `-O3` | no | list + modulo scheduling | 1.48 | 2.18 | 2.41 |

For reference, Leo's hand-written kernels on SB1 reach 1.49× on the float DSP subset (model); on
that subset the modelled policies give 1.59× (greedy), 2.00× (list), 2.15× (+ modulo).

What the table says:
1. **Latency-aware scheduling is worth +27% on SB1 and +26% on SB2** over greedy packing of the
   same code: 1.53 → 1.95 and 1.96 → 2.47.
   - This is an upper bound for Leo's pass. Its scoring, its 64-op look-ahead and the pre-RA
     scheduler's default load latency already recover some of it (§2, G1).
   - Expect the real gain on FP-heavy loops, whose 2-cycle FP latency is invisible to the compiler
     today.
2. **Modulo scheduling adds +7% on SB1, +17% on SB2 and +15% on 8-wide** on top of unrolled list
   scheduling. On `-O3` code it adds +24–64%. With it, plain `-O3` code reaches what unrolling
   reaches on SB2 (2.47).
   - Software pipelining also makes zero-overhead loops less important, because the loop overhead
     is overlapped.
3. **Alias information is worth +12% on SB1 and +20% on SB2** (list scheduler, unrolled).
   - It matters more with two memory paths: the second port is only useful if a load can move
     above a store.
   - `restrict` on kernel pointers is the cheapest lever in this table.
4. **The compiler determines how much hardware you get to use.** SB1 + list + modulo (2.09)
   beats SB2 + greedy packing (1.96). And the SB2 hardware's own gain over SB1 grows from 1.28×
   to 1.38× as the compiler improves.

The model's "greedy packetizer" is a simplification (§2, G2): in program order, memory op first,
ignoring latency. Absolute numbers aren't comparable with Leo's RTL measurements: different
compiler (GCC 10.2 vs clang), oracle alias information, and no I$-line layout effects. The
*differences* between policies on identical instruction streams are what this section reports.

## 5. Recommended compiler roadmap, in order

1. **Write a Starbug scheduling model** (`RISCVSchedStarbug.td`). Contents:
   - `IssueWidth = 4` (or 8);
   - `ProcResource`s: `SBLSU` (1), `SBLoadOnly` (0 or 1), `SBALU` (4), `SBFPU` (4),
     `SBMDU` (shared, see `HARDWARE_CONTRACT.md` §6), `SBBranch` (1);
   - latencies: load 2, mul 2, FP 2, fcvt 2, div 20, fdiv 15 (the model's, matched to Wally).

   Then switch `STARBUG_VLIW` from `NoSchedModel` to it. This one change:
   - turns on the machine pipeliner that is already wired in;
   - gives `MachineScheduler` the latencies to hide;
   - gives if-conversion and unrolling a cost model.
   
   Keep one source of truth: generate the `.td`, `StarbugVLIWConfig` defaults, `make_cfg()` in the
   model and the checker's lane table from a single machine-description file.
2. **Make packetizing latency-aware.** Either:
   - (a) keep the packetizer but feed it a schedule from a latency-aware post-RA list scheduler and
     only let it pack within a cycle; or
   - (b) the Hexagon route: `VLIWMachineScheduler`/`ConvergingVLIWScheduler` pre-RA plus a
     `DFAPacketizer` (`VLIWPacketizerList`) built from the scheduling model's resources.

   (b) gets port and lane legality from the resource model for free. That matters once
   STARBUG 2 has two memory paths and banks, because hand-written `isLikelyLSUOp` tests don't scale.
3. **Modulo-schedule innermost loops.** Run `MachinePipeliner`/SMS once (1) exists. Tune it so the
   resource MII binds on the memory ports and the recurrence MII on accumulator chains. Give
   unroll-and-jam enough accumulators to push RecMII below ResMII.
   - This is the main software lever for FIR/LMS-type kernels (Leo's notes: the 1.42 vs 1.56 gap
     on FIR).
   - With zero-overhead loops it removes the per-iteration branch cost as well.
4. **Supply alias information.** Options: `restrict` in the kernels; `-starbug-vliw-assume-disjoint-memory`
   behind a runtime check (loop versioning, as `LoopVersioningLICM` does); or alias metadata
   carried to MI (`MachineMemOperand`s already hold it; `canHoistCandidate` should use
   `MI.mayAlias(AA, ...)` instead of a blanket no-reorder).
5. **Target the second memory path.**
   - Lane classes for load-only lanes.
   - A per-packet store limit.
   - **Bank-aware pairing and placement** if the banks are interleaved. Pair `x[i]` with `x[i+1]`
     (opposite banks by construction) before pairing two different arrays, whose relative bank
     depends on where the linker put them. Or give the second stream its own memory (X/Y).
6. **Emit the 32-bit header as real bundles** (`BUNDLE` MIs, finalized after packetizing). Encode
   the p-bits and predicates in MC. Make the layout pass header-aware, or rely on hardware
   straddle support (cheaper overall, doc 07).
7. **Hardware loops**, then **if-conversion with creg/z**, then **superblock/trace scheduling**
   across blocks (Leo's §7.1, biquad). Each is worth less than items 1–5 on the current kernels.

Verification at every step:
- run `bundle_check.py` (now with lane rules) on every built ELF;
- run each binary on the scalar reference (Spike, or MPACT `rv32g_sim`) and compare outputs;
- for STARBUG 2, compare against the extended MPACT executor (open item A4).
