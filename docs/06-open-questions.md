# Open questions and next steps

Prioritised as of 2026-10-16. When an item is settled, move it to [`00-history.md`](00-history.md)
with a date. If it changes a decision, also update [`01-design-rationale.md`](01-design-rationale.md).

## A. Tasks someone can do now (no designer input needed)

### A1. Enforce the lane rules in our own tools (high)
- **Why:** MPACT and `bundle_check.py` accept bundles the RTL can't execute
  ([rationale §3](01-design-rationale.md#3-lane-legality-why-a-correct-output-on-mpact-isnt-proof-of-a-correct-schedule)).
- **Do:**
  1. Extend `mpact_run/bundle_check.py` to flag any load, store, AMO, branch, jump, CSR or privileged
     op in bundle slots 1–3. Slot 0 maps to lane 0.
  2. Optionally make MPACT's `riscv32_vliw_decoder.cc` reject such bundles, or have `RiscVExVLIW`
     mimic the RTL: a worker-lane load writes 0, and a store or branch is dropped.
  3. Re-schedule `dp_vliw_better.S` with all memory ops in lane 0, and re-measure it against the
     same-binary and `-O2` baselines.
- **Done when:** the old `dp_vliw_better.S` is flagged, and a legal dp schedule has a measured
  speedup.

### A2. Confirm the suspected RTL bugs in simulation (high)
The bugs are in [`03-rtl-analysis.md`](03-rtl-analysis.md#suspected-bugs-code-reading-confirm-in-wsim).
Write a small bare-metal assembly test for cvw `lm/fp`, run under `wsim` (Questa) or Verilator with
`config/starbug`:
1. A bundle `c.li x0,2; auipc t0,0; addi t1,x0,1`. Compare `t0` with the address of the `auipc`.
2. A bundle whose op 0 is `beq x0,x0,target`. Check whether execution lands at `target` or at
   `target − 2`.
3. A bundle whose op 0 is `jal ra,f`, where `f` is `ret`. Check `ra` and whether the bundle's ops
   execute twice.
4. A bundle with `lw` in slot 1. Check that rd = 0 and that no trap occurs. Likewise `sw` in slot 1
   (memory unchanged) and a branch in slot 1 (not taken).

`cvw-lmfp` commit `4d353231e` "Add architectural test for the STARBUG bundle hint" is a good template.
Use the `+STARBUG_VLIW_DEBUG` plusarg in `wallypipelinedcore.sv` to print the lanes.
- **Done when:** each bug is confirmed or refuted, with waveform/log evidence. Then report upstream to
  rvru/cvw.

### A3. Make the RTL comparison fair (medium)
- Re-run Leo's `embench_starbug` RTL comparison with `BPRED_SUPPORTED = 1` (gshare, as in `rv32gc`)
  in `config/starbug`, or with gshare off in both configs.
- Report both ratios next to the 1.40× headline
  ([rationale §10e](01-design-rationale.md#10e-the-140-rtl-headline-compares-unequal-configs)).

### A4. Port MPACT to the STARBUG 2 header (medium; partly blocked on B1)
- **Decoder:** match custom-0 (`0001011`) instead of `kCliHint`, and derive the op count from the
  p-bits under the chosen B1 reading.
- **`RiscVExVLIW`:**
  - snapshot all sources before executing any child;
  - evaluate each op's predicate from the snapshot;
  - buffer the writes and commit them at the end;
  - allow complementary same-destination writes;
  - reject more than one memory op.
- **Tests:** a register swap inside a packet, and complementary writes to one register.

### A5. Extend the model as the design firms up (low)
- Add configs to `CONFIGS` in `mpact_run/vliw_model.py` for any new proposal, e.g. X/Y memories, or a
  load-only lane in slot 1 vs 2.
- Add software pipelining to the scheduler to bound what a real compiler could reach.

## B. Questions for the designer / Dr. Simar
1. **Packet length.** Does the header always cover 4 ops (p-bits split them into cycles), or does the
   packet end at the first p = 0? If the latter, what does a non-thermometer pattern such as `1010`
   mean? (Action item: Rocky.)
2. **creg → register mapping.** Is creg k exactly x(8+k)? What does `creg=000, z=1` mean?
3. **Predicate registers vs. the ABI.** x10–x15 are argument registers. Is that intended, and how will
   the compiler handle predicates across calls?
4. **Traps and branches in a packet.** Is commit atomic? Is `mepc` = the header? Which slots may hold a
   branch, and does it take effect after the packet?
5. **Non-complementary double writes.** Trap or undefined?
6. **Alignment.** Must a packet (up to 20 bytes) fit in one I-cache line? About 9% of today's bundles
   straddle one.
7. **Second memory path.** Does the team adopt LS + load-only, and who owns bank-aware data placement
   (compiler, linker, or X/Y memories)?

## C. Context still to collect
- The Design Foundation Document and weekly reports are on the team drive; this repo only summarises
  them.
- Leo's LLVM fork (`leomarek/starbug-llvm`, `-mcpu=starbug-vliw`) was built on a lab server. Rebuilding
  it would allow evaluating real compiler output instead of the list-scheduler stand-in.
- Dr. Simar offered TI VLIW compiler contacts, an ARM interleaved-memory example and a Rice
  interleaved-memory paper. Fold these into A5 when they arrive.
