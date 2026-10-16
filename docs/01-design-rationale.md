# Design rationale: the thought process behind each finding and decision

Each section covers one question:
- **the question**;
- **what we saw**;
- **the reasoning**;
- **the conclusion**;
- **what it changed**.

Sections are roughly in the order the questions came up; [`00-history.md`](00-history.md) has dates.
Statements marked *(code reading)* have not been confirmed in simulation.

---

## 1. Why start from the MPACT simulator, and why not take its numbers at face value

**Question.** How fast is Starbug really, and what tools exist to measure it?

**What we saw.**
- The RTL repo has no simulator. The MPACT fork, `rvru/rvr-starbug`, was the team's early
  architecture-exploration tool (Sep–Nov 2025).
- Later work moved to RTL simulation (`wsim`, Verilator, Questa), and nothing on `lm/fp` calls MPACT.
- MPACT is a *functional* instruction-set simulator:
  - `mcycle` goes up by 1 per top-level instruction, and a whole bundle is one top-level instruction;
  - stalls, cache misses, branch penalties and multi-cycle latencies are not modelled;
  - the ops inside a bundle run *in order*, so op k sees results from ops 0..k−1.

**Reasoning.**
- An instruction-set simulator is the fastest way to run real code and check outputs, so it was
  brought up first.
- Its cycle count is really "bundles plus scalar instructions retired". That is an idealised count, not
  a timing measurement.
- Its in-order semantics differ from the RTL, where all lanes read the register file in Decode
  together. So a bundle can pass on MPACT and still break on hardware.

**Conclusion.**
- Use MPACT to run code and check outputs.
- Treat its cycle counts as an upper bound on what bundling can achieve.
- Get timing from something calibrated against the RTL (§9).

## 2. Which baseline a speedup should be quoted against

**Question.** `profile.py` reported a 28–30× speedup for the dot product. Is that real?

**What we saw.** The 28–30× compared the hand-bundled kernel with scalar C at `-O0`. Kernel-only
measurements at length 4096:
- 30× vs `-O0`;
- **4.5× vs `-O2`**;
- **3.5× vs the same VLIW binary run on the scalar simulator**, where the hints are no-ops.

**Reasoning.**
- A hand-optimised kernel against unoptimised C mixes two effects: what the hardware feature buys and
  what optimisation buys. Only "the same binary with the feature off" isolates the feature. Only "the
  best scalar code" shows what a user gains.
- **Timing method.** The original script subtracted an "empty kernel" whole-program run. But the
  program prints its results, and printing real numbers costs more than printing zeros. That inflated
  every kernel figure by about 90k cycles, and the inflation didn't cancel.
- So the fix was to read `mcycle` directly around the call (`mpact_run/dp_main_timed.c`).

**Conclusion.**
- Always report both baselines, and measure only the kernel.
- The same rule applies to machine configs: diff them before quoting a ratio (§10).

## 3. Lane legality: why a correct output on MPACT isn't proof of a correct schedule

**Question.** The bundled dot product matched the scalar output at every length. Is it a valid Starbug
schedule?

**What we saw** *(code reading, `wallypipelinedcore.sv` on `lm/fp`)*.
- There is one `lsu` instance, fed only by lane 0.
- Lanes 1–3 have `ReadDataW_1..3` tied to `'0`.
- Their `PCSrcE`/`IEUAdrE` connections are commented out.
- The privileged unit sees only lane 0's instruction.
- `dp_vliw_better.S` puts `c.lw` and `sw` in worker lanes.

**Reasoning.**
- An ISS checks what a program computes, not whether its bundles are legal on the hardware. The ISS
  can be more permissive than the hardware in three ways:
  - order within a bundle;
  - resources per bundle: MPACT's memory has unlimited ports;
  - encoding limits: width, alignment, staying within one cache line.
- On the RTL, those worker-lane loads would write 0 and the stores would vanish.
- The RTL would not trap, so even hardware wouldn't have flagged the problem.

**Conclusion.**
- **Take legality rules from the RTL, and encode them as a checker** (assembler, decoder, or ELF lint)
  so that illegal bundles fail loudly instead of being measured.
- The 3.3–4.5× dp figure is not reachable on today's RTL.
- The later RTL-targeted kernels (`embench_starbug`) keep all 513 memory ops in lane 0.

**Changed.** The trade-study model enforces lane-0-only memory, branches and CSRs for every Starbug 1
config. Extending `bundle_check.py` with the same rule is still open.

## 4. Silent drops in worker lanes should trap

**Question.** What should happen when an op lands in a lane that can't execute it?

**What we saw** *(code reading)*. Today the op becomes a wrong value or a no-op:
- a load returns 0;
- a branch is never taken;
- a CSR write never happens;
- an illegal instruction has its controls zeroed (`controller.sv` L332).

**Reasoning.**
- A placement error that silently turns into a wrong value surfaces far downstream, as a puzzling data
  bug with nothing pointing back to the cause.
- A trap at the instruction costs much less to debug than a corruption found by a benchmark.

**Recommendation.**
- Worker lanes should raise illegal-instruction for any op they can't execute.
- The compiler/assembler should also refuse such bundles.
- If trapping is too expensive, at least add a sticky "dropped op" flag and test that it stays zero.

## 5. Every lane needs its own PC

**Question** *(code reading)*. What PC do the ops in a bundle see?

**What we saw.**
- All four lanes receive `PCE`, the header's address.
- `PCLinkE = PCE + (CompressedE ? 2 : 4)`, where `CompressedE` belongs to the 16-bit header.

**Reasoning.**
- The assembler encodes branch/`jal` offsets and `auipc` relative to *each instruction's own address*.
  With a shared header PC:
  - a lane-0 branch lands 2 bytes before its target (4 bytes with the STARBUG 2 header);
  - `auipc` is wrong;
  - `jal` links to header+2, i.e. op 0, so a return re-runs the bundle's ops as scalar code.
- Fixing this needs two values per bundle:
  - **a per-op PC** = header PC + header size + byte offset. The extraction walk already computes the
    offset, so it only needs to be pipelined.
  - **the bundle's fall-through PC**, to use as the link value.

**Status.** Unconfirmed. Next step: an assembly test in `wsim` (see [`06-open-questions.md`](06-open-questions.md)).

## 6. Why STARBUG 2 moves the header from a HINT to a custom-0 opcode

**Question.** Starbug 1 marks bundles with `c.li x0, N`, which is architecturally a no-op. Why does
the proposal switch to a 32-bit custom-0 opcode that traps on other cores?

**Reasoning.**
- **What the HINT buys.**
  - Binaries still run on stock cores and simulators.
  - The RTL can always back out. When a bundle can't be formed (it crosses a cache line, or the memory
    is uncacheable), the RTL runs the header as a nop and the ops as scalar code. See
    [`03-rtl-analysis.md`](03-rtl-analysis.md).
- **When that stops being safe.** The HINT is only safe while *ignoring it gives the same result*:
  bundling may change speed but never results. STARBUG 2 breaks that in three ways:
  - **parallel read-then-write semantics:** a swap inside one packet differs from doing it
    sequentially;
  - **predication;**
  - **two complementary-predicated writes to the same register.**
- Ignoring such a header would silently produce wrong answers, so it must trap where it isn't
  understood.

**Corollary for the RTL.**
- The internal "fall back to scalar" path is no longer legal either.
- STARBUG 2 hardware must handle a packet that crosses a cache line, or the toolchain must guarantee
  that packets never cross one.
- The trade study measured how often this happens: about 9% of bundles straddle a 64 B line, and
  today's fetch can't issue those as bundles.

## 7. Reading the header proposal: what's settled and what isn't

The interpretation is in [`04-starbug2-header.md`](04-starbug2-header.md). The reasoning behind the
open questions:

- **Packet length isn't encoded.** Two readings are possible:
  - **(a)** always 4 ops, with the p-bits splitting them into cycles, like a C6x fetch packet;
  - **(b)** the packet ends at the first p = 0, i.e. the p-bits form a thermometer code.

  The proposal also says that "all ops in a packet read at the start of the cycle". That only makes
  sense if a packet is one cycle, which favours (b). Reading (b) also explains why p3 is spare.
  Escalated to the designer; Rocky took the action item to ask Dr. Simar about the P-bits.
- **The predicate registers x9–x15 are s1 and a0–a5**, i.e. ABI argument registers.
  - The likely motive is that x8–x15 are the registers 16-bit compressed instructions can address, so
    setting a predicate is cheap.
  - But the compiler must then keep predicates live across calls carefully.
  - Flagged as a cost to state explicitly.
- **Not specified yet:**
  - trap and branch semantics inside a packet (atomic commit? does `mepc` = the header?);
  - what happens if two writes with non-complementary predicates both fire;
  - packet alignment.

## 8. Is predication worth its encoding bits?

**Question.** creg/z predication takes 16 of the header's 25 usable bits. What does it buy?

**What we saw.** The trade study scanned for short forward hammocks that if-conversion could remove.
- Only the Q15 FIR has them: 2.9% of cycles.
- GCC `-O3` already removes the rest with branchless code.

**Conclusion.**
- On today's compiled code, predication saves about 0 cycles.
- Its value depends on a compiler that schedules across branches (software pipelining, hyperblocks),
  which doesn't exist yet.
- Worth keeping the encoding space, but it shouldn't drive the schedule.

## 9. Why build a trace-driven model instead of sweeping RTL configs

**Question.** How do we price changes that don't exist in RTL yet: a second memory port, 8 lanes,
banking, branch features?

**Reasoning.**
- RTL sweeps would need each feature implemented first.
- MPACT has no timing.
- A trace-driven model can re-schedule the same dynamic instruction stream under any machine
  description, in seconds.

**Model design** (`mpact_run/vliw_model.py`):
- in-order, lock-step issue;
- latencies: load, mul and FP = 2; ALU = 1; a taken branch or mispredict = 2;
- 16 KB 4-way I$ and D$ with an 18-cycle fill;
- the RTL's lane rules;
- a list scheduler that re-packs each basic block;
- oracle memory disambiguation from the trace;
- bank conflicts replayed from traced addresses.

**Trusting it.**
- Validated against Leo's Questa RTL counts on 3 runs per DSP kernel: scalar on RV32GC, hand-VLIW on
  RV32GC (hints = nops), and hand-VLIW on Starbug.
- Median error is about 4%. Starbug is within 10% on 8/10 kernels.
- Two mechanisms were found only because the errors had patterns, and were then added:
  - table-heavy dct4/rfft came out 18–30% fast, which pointed to the **D-cache**;
  - scalar FIR came out 28% slow, which pointed to the **baseline's gshare**.

**Known limits.**
- Cycles only: no area or clock frequency.
- The scheduler stands in for a compiler that doesn't exist yet, and does no software pipelining.
- Banking uses the addresses the linker happened to produce.

## 10. What the numbers say about where to spend hardware

### 10a. Memory ports, not issue width

Geomean over 16 kernels, unrolled code, conflict-free:

| lanes \ mem ops per bundle | 1 | 2 | 4 |
|---|---|---|---|
| 4 | 1.00× | 1.38× | 1.54× |
| 8 | 1.00× | 1.39× | 1.62× |
| 16 | 1.01× | 1.39× | 1.62× |

- DSP/ML inner loops move one or two words per arithmetic op; a FIR tap is 2 loads and an FMA.
- Extra ALU lanes sit idle while every bundle waits on the one memory port.
- Width only starts to matter at 4 memory ops per bundle, and even then it adds 5%.
- **Decision input:** decide memory ops per bundle first, then add just enough lanes to keep those
  ports busy. This reorders the team's earlier plan, which put 8-wide ahead of the second path.

### 10b. Load-only vs full load/store second path
- Load-only keeps **76%** of the full-LS gain: 1.29× vs 1.37×.
- It also sidesteps the team's concern about two stores hitting memory in the same cycle.
- **Recommended:** one load/store path plus one load-only path.

### 10c. Interleaved banks need data placement
- Two single-port, word-interleaved banks are the cheap way to get a second port.
- But kernels read their two streams in lockstep, so the bank pairing is fixed by where the linker
  placed the arrays. Each kernel then sees either almost no conflicts or one nearly every iteration.
- Effect: 1.29× drops to 1.14× (dwconv: 1.40× → 1.03×; FIR: 1.63× → 1.24×).
- **Decision input:** stall on conflict for correctness, and make placement someone's job (compiler,
  linker, or separate X/Y memories).
- The team's marginal ≥1.2× target is met with placement (1.29×) and missed without it (1.14×).

### 10d. Loop-branch hardware pays only when the compiler doesn't unroll
- Zero-overhead loops gave 1.50× on FIR at plain `-O3`, but only 1.11× once the code was unrolled.
- Unrolling plus several accumulators was itself worth 1.7–2.6×, more than any hardware feature.
- The "up to 4× from low-overhead branches" estimate came from non-unrolled baselines.
- **Decision input:** state the compiler assumption next to every hardware speedup. If unrolling is
  cheap, spend silicon on what unrolling can't supply, i.e. memory ports.

### 10e. The 1.40× RTL headline compares unequal configs
- `config/starbug` has `BPRED_SUPPORTED = 0`; `config/rv32gc` has gshare (1 K PHT, 1 K BTB, 16-entry
  RAS).
- So part of the measured ratio is the missing predictor, not the VLIW.
- Re-enabling gshare on Starbug is worth 1.07× by itself.

### 10f. The 32-bit header has no cycle cost
- Hot code grows by 14%.
- The P-bit chaining and second-word encodings for 8-wide give the same code size, because 8-wide
  bundles average fewer than 4 ops.

## 11. Why the team's 10-08/09 estimates were superseded rather than averaged in

**The estimates.** 2.7–3.1× for a second load path (matmul / dwconv) and 4× for low-overhead branches
(SHA-256 / CRC-32).

**The model.** About 1.4–1.7× and about 1.0× for the same kernels.

**Why replace instead of average.**
- The two studies used different baselines (non-unrolled code, and different reference machines).
- Averaging numbers with different denominators would be meaningless.
- The model is validated against RTL; the earlier estimates were not.
- So the model's figures replace them in the Design Foundation Document, and the two sets should never
  appear side by side as if comparable.

## 12. Recommended STARBUG 2 direction (from the evidence above)
1. **Add one load/store path plus one load-only path**, with interleaved banks *and* placement by the
   compiler or linker. Conflict-free this is about 1.29×; with naive placement about 1.14×.
2. **Re-enable gshare** in `config/starbug`: about 1.07×, and it also makes the RTL comparison fair.
3. **Adopt the 32-bit custom-0 header**, since it costs 0 cycles. Trap on unknown or illegal lane use.
   Give each lane its own PC.
4. **Add 8-wide only after the second memory path.** Alone it adds about 1%.
5. **Compiler work (unrolling, multiple accumulators, bank-aware placement) is worth more than any
   single hardware feature.**
6. Predication and zero-overhead loops are low priority until the compiler can use them.

Combined 4-wide (load-only path + 2 banks + gshare + ZOL): about **1.27×** over Starbug 1. The ceiling
(16-wide, 4 memory ops, no limits) is about **1.86×** on this code.
