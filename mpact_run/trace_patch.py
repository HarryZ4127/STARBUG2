#!/usr/bin/env python3
"""Add instruction/data-address tracing and a UART stub to the MPACT rv32g_sim.

Applies in place to an rvr-starbug checkout (default ~/starbug/rvr-starbug); safe to re-run.

  STARBUG_TRACE=<file> rv32g_sim --exit_on_tohost prog.elf

writes one line per retired instruction ("p<pc hex>") followed by one line per data access it
made ("l<addr hex> <bytes>" / "s<addr hex> <bytes>"). Without STARBUG_TRACE nothing is logged.
The UART stub makes the cvw bare-metal printf (polled 16550 at 0x10000000) work: LSR always
reads THRE|TEMT and bytes written to THR go to stdout.
"""
import os
import sys

root = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/starbug/rvr-starbug")
MARK = "STARBUG_TRACE"


def patch(path, anchor, text, after=True):
    src = open(path).read()
    if MARK in src and text.strip().splitlines()[0] in src:
        return
    assert anchor in src, f"anchor not found in {path}: {anchor!r}"
    src = src.replace(anchor, anchor + text if after else text + anchor, 1)
    open(path, "w").write(src)


top_cc = os.path.join(root, "riscv/riscv_top.cc")
patch(top_cc, '#include "riscv/riscv_top.h"\n',
      "\n// STARBUG_TRACE: per-instruction pc log, set by rv32g_sim when the env var is present.\n"
      "FILE* g_starbug_trace = nullptr;\n")
patch(top_cc, "      auto* inst = rv_decode_cache_->GetDecodedInstruction(pc);\n      SetPc(pc);\n",
      "      if (g_starbug_trace) fprintf(g_starbug_trace, \"p%lx\\n\", (unsigned long)pc);  // STARBUG_TRACE\n")

sim_cc = os.path.join(root, "riscv/rv32g_sim.cc")
patch(sim_cc, "using ::mpact::sim::riscv::RiscV32Decoder;\n", r'''
// STARBUG_TRACE: data-address trace + 16550 UART stub.
extern FILE* g_starbug_trace;
class StarbugTraceMemory : public mpact::sim::util::MemoryInterface {
 public:
  using DataBuffer = mpact::sim::generic::DataBuffer;
  using Instruction = mpact::sim::generic::Instruction;
  using ReferenceCount = mpact::sim::generic::ReferenceCount;
  StarbugTraceMemory(mpact::sim::util::MemoryInterface* m,
                     mpact::sim::generic::DataBufferFactory* f) : m_(m) {
    lsr_ = f->Allocate<uint8_t>(1);
    lsr_->Set<uint8_t>(0, 0x60);
  }
  void Load(uint64_t a, DataBuffer* db, Instruction* inst, ReferenceCount* ctx) override {
    if (g_starbug_trace) fprintf(g_starbug_trace, "l%lx %d\n", (unsigned long)a, (int)db->size<uint8_t>());
    if (a >= 0x10000000 && a < 0x10000008) m_->Store(0x10000005, lsr_);
    m_->Load(a, db, inst, ctx);
  }
  void Load(DataBuffer* a, DataBuffer* mask, int el, DataBuffer* db, Instruction* inst,
            ReferenceCount* ctx) override { m_->Load(a, mask, el, db, inst, ctx); }
  void Store(uint64_t a, DataBuffer* db) override {
    if (g_starbug_trace) fprintf(g_starbug_trace, "s%lx %d\n", (unsigned long)a, (int)db->size<uint8_t>());
    if (a == 0x10000000) { fputc(db->Get<uint8_t>(0), stdout); fflush(stdout); }
    m_->Store(a, db);
  }
  void Store(DataBuffer* a, DataBuffer* mask, int el, DataBuffer* db) override {
    m_->Store(a, mask, el, db);
  }
 private:
  mpact::sim::util::MemoryInterface* m_;
  DataBuffer* lsr_;
};
''')
patch(sim_cc, "  // Set up architectural state and decoder.\n",
      "  // STARBUG_TRACE: wrap the data-side memory (instruction fetch stays on `memory`).\n"
      "  mpact::sim::generic::DataBufferFactory starbug_db_factory;\n"
      "  if (const char* tp = getenv(\"STARBUG_TRACE\")) {\n"
      "    g_starbug_trace = fopen(tp, \"w\");\n"
      "    if (g_starbug_trace) setvbuf(g_starbug_trace, nullptr, _IOFBF, 1 << 22);\n"
      "  }\n"
      "  memory_interface = new StarbugTraceMemory(memory_interface, &starbug_db_factory);\n",
      after=False)
print("patched", root)
