#!/usr/bin/env python3
"""Trace-driven timing model for STARBUG ISA what-if studies.

Input: an ELF and its MPACT trace (trace_patch.py: "p<pc>" per retired instruction, then
"l<addr> <n>" / "s<addr> <n>" per data access). Only the timed region, between start_trigger()
and stop_trigger(), is modelled.

Two ways to turn the trace into cycles:
  as_written(): time the code exactly as laid out -- scalar instructions one per cycle, and
                hand-written `c.li x0,N` bundles as one issue slot (STARBUG 1 rules: N <= 4, the
                bundle must sit in one 64-byte I$ line, otherwise it falls back to scalar).
  scheduled():  re-pack each basic block of (unrolled) scalar code into bundles for a machine
                configuration -- a compiler-style list scheduler with real lane/port limits.
Both feed the same in-order, interlocked, lock-step pipeline model:
  - a bundle issues one cycle after the previous one, later if a source isn't ready;
  - result latency: ALU 1, load 2, mul 2, FP 2, fcvt.w 2, FP->int moves 1, div 20, fdiv 15
    (Wally: LoadStallD / MDUStallD / FPUStallD stall a dependent op directly behind);
  - taken branches and jumps cost `branch_penalty` cycles (2: starbug config has no predictor);
  - two memory ops in one bundle that hit the same bank (word-interleaved) cost one extra cycle.
make_cfg() also models (all off by default, so CONFIGS reproduce the 2026-10-13 study): per-class
latencies, shared mul/FP units, register-file ports, 2/4/8 word or XOR-hashed banks, cache size,
fetch bandwidth, line-straddling bundles under each header encoding, creg/z if-conversion, a greedy
packetizer like the LLVM one, and software pipelining as a modulo-schedule bound (modulo_ii).
Tests: tests/test_vliw_model.py. Sweeps over all of it: dse.py.
"""
import bisect
import collections
import json
import math
import re
import subprocess
import sys

OBJDUMP = "riscv64-unknown-elf-objdump"
NM = "riscv64-unknown-elf-nm"

LOADS = {"lb", "lh", "lw", "lbu", "lhu", "flw", "fld", "c.lw", "c.lwsp", "c.flw", "c.flwsp",
         "c.fld", "c.fldsp", "c.lbu", "c.lhu", "c.lh"}
STORES = {"sb", "sh", "sw", "fsw", "fsd", "c.sw", "c.swsp", "c.fsw", "c.fswsp", "c.fsd",
          "c.fsdsp", "c.sb", "c.sh"}
BRANCHES = {"beq", "bne", "blt", "bge", "bltu", "bgeu", "c.beqz", "c.bnez"}
JUMPS = {"jal", "jalr", "c.j", "c.jal", "c.jr", "c.jalr"}
RD_IS_SRC = {"c.addi", "c.slli", "c.srli", "c.srai", "c.andi", "c.add", "c.sub", "c.xor",
             "c.or", "c.and", "c.addi16sp", "c.mul", "c.zext.b", "c.sext.b", "c.zext.h",
             "c.sext.h", "c.not"}
MUL = {"mul", "mulh", "mulhsu", "mulhu", "c.mul"}
DIV = {"div", "divu", "rem", "remu"}
FDIV = {"fdiv.s", "fsqrt.s", "fdiv.d", "fsqrt.d"}
REG = re.compile(r"\b([xf]\d+)\b")

LAT = {"alu": 1, "load": 2, "store": 1, "mul": 2, "div": 20, "fp": 2, "fcvti": 2, "fmvx": 1,
       "fdiv": 15, "branch": 1, "jump": 1, "csr": 1, "sys": 1, "nop": 1, "hint": 1}
WORKER = {"alu", "mul", "div", "fp", "fcvti", "fmvx", "fdiv", "nop"}  # what lanes 1.. can do


class Op:
    __slots__ = ("pc", "size", "mn", "ops", "cls", "dst", "src", "hint_n", "target")

    def __init__(self, pc, size, mn, ops):
        self.pc, self.size, self.mn, self.ops = pc, size, mn, ops
        self.hint_n, self.target = 0, None
        regs = [r for r in REG.findall(ops) if r != "x0"]
        allregs = REG.findall(ops)
        self.dst, self.src = [], []
        if mn in LOADS:
            self.cls, self.dst, self.src = "load", allregs[:1], allregs[1:]
        elif mn in STORES:
            self.cls, self.src = "store", allregs
        elif mn in BRANCHES:
            self.cls, self.src = "branch", allregs
        elif mn in JUMPS:
            self.cls = "jump"
            if mn in ("c.j",):
                pass
            elif mn == "c.jal":
                self.dst = ["x1"]
            elif mn == "c.jr":
                self.src = allregs
            elif mn == "c.jalr":
                self.src, self.dst = allregs, ["x1"]
            elif mn == "jal":
                self.dst = allregs[:1]
            else:  # jalr rd,off(rs1)
                self.dst, self.src = allregs[:1], allregs[1:]
            m = re.search(r"\b([0-9a-f]{6,})\b", ops)
            if m and mn in ("jal", "c.j", "c.jal"):
                self.target = int(m.group(1), 16)
        elif mn == "c.nop" or (mn == "addi" and ops.replace(" ", "").startswith("x0,x0,0")):
            self.cls = "nop"
        elif mn == "c.li" and allregs[:1] == ["x0"]:
            self.cls, self.hint_n = "hint", int(ops.split(",")[-1], 0)
        elif mn.startswith("csr"):
            self.cls, self.dst, self.src = "csr", allregs[:1], allregs[1:]
        elif mn in ("ecall", "ebreak", "c.ebreak", "fence", "fence.i", "wfi", "mret", "unimp", "c.unimp"):
            self.cls = "sys"
        elif mn in RD_IS_SRC:
            self.cls, self.dst, self.src = "alu", allregs[:1], allregs
            if mn == "c.mul":
                self.cls = "mul"
        else:
            self.dst, self.src = allregs[:1], allregs[1:]
            if mn in MUL:
                self.cls = "mul"
            elif mn in DIV:
                self.cls = "div"
            elif mn in FDIV:
                self.cls = "fdiv"
            elif mn.startswith("f") and not mn.startswith("fence"):
                if mn.startswith("fcvt.w"):
                    self.cls = "fcvti"
                elif self.dst and self.dst[0].startswith("x"):
                    self.cls = "fmvx"
                else:
                    self.cls = "fp"
            else:
                self.cls = "alu"
        self.dst = [r for r in self.dst if r != "x0"]
        self.src = [r for r in self.src if r != "x0"]

    @property
    def is_mem(self):
        return self.cls in ("load", "store")

    @property
    def is_ctrl(self):
        return self.cls in ("branch", "jump")


def disassemble(elf):
    out = subprocess.run([OBJDUMP, "-d", "-M", "no-aliases,numeric", elf],
                         capture_output=True, text=True, check=True).stdout
    ops = {}
    for line in out.splitlines():
        m = re.match(r"^\s*([0-9a-f]+):\s+([0-9a-f]{4}|[0-9a-f]{8})\s+(\S+)\s*(.*)$", line)
        if m:
            pc = int(m.group(1), 16)
            ops[pc] = Op(pc, len(m.group(2)) // 2, m.group(3), m.group(4).split("#")[0].strip())
    return ops


def symbols(elf):
    out = subprocess.run([NM, elf], capture_output=True, text=True, check=True).stdout
    syms = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    return syms


def read_window(trace_path, syms, ops):
    """Return (pcs, maddr, msize, warm) for instructions after start_trigger returns up to the
    call of stop_trigger. maddr[i] is the data address of instruction i (or -1). warm is the
    pre-window access stream [(kind, addr)] with kind 'i' (fetch), 'l' or 's', used to warm caches."""
    start, stop = syms["start_trigger"], syms["stop_trigger"]
    pcs, maddr, msize, warm = [], [], [], []
    state = 0  # 0: before start_trigger, 1: inside start_trigger, 2: in window
    last_line = -1
    with open(trace_path) as f:
        for line in f:
            c = line[0]
            if c == "p":
                pc = int(line[1:], 16)
                if state == 2:
                    if pc == stop:
                        break
                    pcs.append(pc)
                    maddr.append(-1)
                    msize.append(0)
                    continue
                if pc >> 6 != last_line:
                    warm.append(("i", pc))
                    last_line = pc >> 6
                if state == 0 and pc == start:
                    state = 1
                elif state == 1 and ops[pc].mn in ("jalr", "c.jr") and ops[pc].src == ["x1"]:
                    state = 2  # the return; the window starts with the next instruction
            elif c == "l" or c == "s":
                a, n = line[1:].split()
                if state == 2 and pcs:
                    maddr[-1] = int(a, 16)
                    msize[-1] = int(n)
                else:
                    warm.append((c, int(a, 16)))
    return pcs, maddr, msize, warm


class Cache:
    """Set-associative, LRU, write-back/write-allocate (Wally I$/D$: 4 ways x 4 KB, 64 B lines)."""

    def __init__(self, size=16384, ways=4, line=64):
        self.ways, self.shift = ways, line.bit_length() - 1
        self.nsets = size // (ways * line)
        self.sets = [[] for _ in range(self.nsets)]  # each: list of [tag, dirty], MRU last

    def access(self, addr, write=False):
        """Return (hit, dirty_eviction)."""
        ln = addr >> self.shift
        s = self.sets[ln % self.nsets]
        for k, e in enumerate(s):
            if e[0] == ln:
                s.append(s.pop(k))
                if write:
                    e[1] = True
                return True, False
        dirty = False
        if len(s) >= self.ways:
            dirty = s.pop(0)[1]
        s.append([ln, write])
        return False, dirty


class Predictor:
    """Wally rv32gc front end: gshare (2^10 two-bit counters, 10-bit history), 1 K-entry BTB,
    16-entry return-address stack. A wrong direction or target costs the branch penalty."""

    def __init__(self, bits=10):
        self.mask = (1 << bits) - 1
        self.pht = [1] * (1 << bits)
        self.ghr = 0
        self.btb = {}
        self.ras = []

    def mispredicted(self, op, taken, target):
        if op.cls == "branch":
            i = ((op.pc >> 1) ^ self.ghr) & self.mask
            pred = self.pht[i] >= 2 and self.btb.get(op.pc) is not None
            self.pht[i] = min(3, self.pht[i] + 1) if taken else max(0, self.pht[i] - 1)
            self.ghr = ((self.ghr << 1) | taken) & self.mask
            wrong = pred != taken or (taken and self.btb.get(op.pc) != target)
            if taken:
                self.btb[op.pc] = target
            return wrong
        # jumps
        if op.mn in ("jalr", "c.jr") and op.src == ["x1"] and not op.dst:  # return
            pred = self.ras.pop() if self.ras else None
            return pred != target
        if op.dst == ["x1"]:  # call
            self.ras.append(op.pc + op.size)
            self.ras = self.ras[-16:]
        wrong = self.btb.get(op.pc) != target
        self.btb[op.pc] = target
        return wrong


# ---------------------------------------------------------------- basic blocks
def basic_blocks(pcs, ops):
    """Static basic blocks over the pcs seen in the window. Returns (bb_of_leader, dyn), where
    dyn is a list of (leader_pc, start_index, taken) per dynamic block instance."""
    leaders = {pcs[0]}
    for i in range(len(pcs) - 1):
        op = ops[pcs[i]]
        if op.is_ctrl or pcs[i + 1] != pcs[i] + op.size:
            leaders.add(pcs[i + 1])
    blocks, dyn = {}, []
    i, n = 0, len(pcs)
    while i < n:
        lead = pcs[i]
        if lead not in blocks:
            seq, pc = [], lead
            while True:
                seq.append(pc)
                op = ops[pc]
                nxt = pc + op.size
                if op.is_ctrl or nxt in leaders or nxt not in ops:
                    break
                pc = nxt
            blocks[lead] = seq
        seq = blocks[lead]
        j = i + len(seq)
        last = ops[seq[-1]]
        nxt = pcs[j] if j < n else seq[-1] + last.size
        dyn.append((lead, i, nxt != seq[-1] + last.size, nxt))
        i = j
    return blocks, dyn


# ---------------------------------------------------------------- machine configurations
def make_cfg(name, width=4, mem_lanes=(0,), load_lanes=(), max_mem=1, max_stores=1,
             branch_lanes=(0,), banks=0, branch="none", zol=False, worker=WORKER, penalty=2,
             caches=True, miss=18, writeback=16, lat=None, mul_units=None, fp_units=None,
             max_reads=None, max_writes=None, bank_hash="word", icache_kb=16, dcache_kb=16,
             line=64, fetch_bytes=0, fetch_queue=0, cross="ideal", header="16-bit hint (SB1)",
             predicate=False, sched="list", swp=False):
    """mem_lanes: lanes that can load and store; load_lanes: extra load-only lanes;
    banks: 0 = ideal (no conflicts), 2/4/8 = interleaved banks;
    branch: none (every taken transfer pays `penalty`: the starbug config has no predictor),
            gshare (Wally rv32gc front end), btfn (static backward-taken/forward-not-taken);
    zol: zero-overhead loops (backward loop branches are free and take no slot);
    caches: model the I$/D$ (`icache_kb`/`dcache_kb`, 4-way, `line` bytes), blocking, `miss`
            cycles per line fill (+`writeback` if dirty).
    The remaining knobs default to "unlimited / as before", so SB1 and CONFIGS are unchanged:
    lat:         per-class latency overrides, e.g. {"load": 3}
    mul_units:   max mul/div ops per bundle (Wally's MDU is shared across lanes); None = no limit
    fp_units:    max FP ops per bundle; None = no limit
    max_reads:   max distinct registers read per bundle, per register file; None = no limit
    max_writes:  max registers written per bundle, per register file; None = no limit
    bank_hash:   "word" (bank = word address mod banks) or "xor" (bank = XOR-fold of the
                 word address, i.e. the scrambled mapping Rice proposed)
    fetch_bytes: I-fetch bytes per cycle into a queue of `fetch_queue` bytes (default
                 2 x fetch_bytes); a bundle waits until its bytes are in the queue; 0 = unlimited
    cross:       a bundle (with `header` bytes) that straddles a `line`-byte I$ line:
                 "ideal" = no cost, "stall" = +1 cycle, "scalar" = its ops issue one per cycle
                 (SB1 fetch rule)
    header:      key of HEADERS, used by `cross` and `fetch_bytes`
    predicate:   if-convert short forward hammocks (creg/z predication): no branch penalty,
                 and a skipped body still takes its issue slots
    sched:       "list" = latency-aware critical-path list scheduler (default);
                 "packetizer" = greedy in-order packet filler that ignores latency, like the
                 current LLVM StarbugVLIWPacketizer
    swp:         software-pipeline single-block loops: steady-state iterations cost the
                 modulo-schedule lower bound MII = max(ResMII, RecMII) instead of the block."""
    L = dict(LAT)
    L.update(lat or {})
    return dict(name=name, width=width, mem_lanes=tuple(mem_lanes), load_lanes=tuple(load_lanes),
                max_mem=max_mem, max_stores=max_stores, branch_lanes=tuple(branch_lanes),
                banks=banks, branch=branch, zol=zol, worker=set(worker), penalty=penalty,
                caches=caches, miss=miss, writeback=writeback, lat=L, mul_units=mul_units,
                fp_units=fp_units, max_reads=max_reads, max_writes=max_writes,
                bank_hash=bank_hash, icache_kb=icache_kb, dcache_kb=dcache_kb, line=line,
                fetch_bytes=fetch_bytes, fetch_queue=fetch_queue or 2 * fetch_bytes, cross=cross,
                header=header, predicate=predicate, sched=sched, swp=swp)


SCHED_KEYS = ("width", "mem_lanes", "load_lanes", "max_mem", "max_stores", "branch_lanes", "zol",
              "mul_units", "fp_units", "max_reads", "max_writes", "sched")


def sched_key(cfg):
    """Everything schedule_block() depends on, so equal keys can share one schedule."""
    return tuple(cfg[k] for k in SCHED_KEYS) + (tuple(sorted(cfg["worker"])),
                                                 tuple(sorted(cfg["lat"].items())))


MUL_CLS = ("mul", "div")
FP_CLS = ("fp", "fdiv", "fcvti", "fmvx")


def lanes_for(op, cfg):
    w = cfg["width"]
    if op.cls == "load":
        return set(cfg["mem_lanes"]) | set(cfg["load_lanes"])
    if op.cls == "store":
        return set(cfg["mem_lanes"])
    if op.is_ctrl:
        return set(cfg["branch_lanes"])
    if op.cls in ("csr", "sys", "hint"):
        return {0}
    return {0} | ({l for l in range(1, w)} if op.cls in cfg["worker"] else set())


def fits(bundle_ops, cfg):
    if len(bundle_ops) > cfg["width"]:
        return False
    nmem = sum(o.is_mem for o in bundle_ops)
    if nmem > cfg["max_mem"] or sum(o.cls == "store" for o in bundle_ops) > cfg["max_stores"]:
        return False
    if sum(o.is_ctrl for o in bundle_ops) > 1:
        return False
    if cfg.get("mul_units") is not None and sum(o.cls in MUL_CLS for o in bundle_ops) > cfg["mul_units"]:
        return False
    if cfg.get("fp_units") is not None and sum(o.cls in FP_CLS for o in bundle_ops) > cfg["fp_units"]:
        return False
    if cfg.get("max_reads") is not None or cfg.get("max_writes") is not None:
        for f in "xf":
            rd = {r for o in bundle_ops for r in o.src if r[0] == f}
            wr = [r for o in bundle_ops for r in o.dst if r[0] == f]
            if cfg.get("max_reads") is not None and len(rd) > cfg["max_reads"]:
                return False
            if cfg.get("max_writes") is not None and len(wr) > cfg["max_writes"]:
                return False
    caps = sorted((lanes_for(o, cfg) for o in bundle_ops), key=len)
    used = {}

    def assign(k, seen):  # bipartite matching, Kuhn's algorithm
        for l in caps[k]:
            if l in seen:
                continue
            seen.add(l)
            if l not in used or assign(used[l], seen):
                used[l] = k
                return True
        return False

    return all(assign(k, set()) for k in range(len(caps)))


# ---------------------------------------------------------------- list scheduler
def independent_pairs(seq, ops, dyn_instances, maddr, msize):
    """Oracle memory disambiguation from the trace: a (store, mem) pair inside a block is
    independent if their byte ranges never overlap in any dynamic instance of the block."""
    mem_idx = [k for k, pc in enumerate(seq) if ops[pc].is_mem]
    pairs = [(a, b) for x, a in enumerate(mem_idx) for b in mem_idx[x + 1:]
             if ops[seq[a]].cls == "store" or ops[seq[b]].cls == "store"]
    dep = set()
    for (a, b) in pairs:
        for start in dyn_instances:
            pa, pb = maddr[start + a], maddr[start + b]
            if pa < pb + msize[start + b] and pb < pa + msize[start + a]:
                dep.add((a, b))
                break
    return {p for p in pairs if p not in dep}


def schedule_block(seq, ops, cfg, indep):
    """Pack one basic block into bundles. sched="list": each cycle, fill the bundle with ready
    ops (all predecessors' latencies elapsed) in critical-path order. sched="packetizer": an op
    may join once its producers sit in an earlier bundle, whatever their latency, preferring a
    memory op as the packet anchor and then program order within a 64-op window -- the policy
    of the LLVM StarbugVLIWPacketizer. Its latency stalls then show up at run time."""
    LATC = cfg.get("lat", LAT)
    greedy = cfg.get("sched", "list") == "packetizer"
    body = [k for k, pc in enumerate(seq) if ops[pc].cls != "nop"]
    if cfg["zol"]:
        last = ops[seq[-1]]
        if last.cls == "branch" and last.mn not in ("jal",) and _is_backward(last):
            body = body[:-1]  # the loop counter hardware takes the branch's place
    n = len(body)
    O = [ops[seq[k]] for k in body]
    preds = [[] for _ in range(n)]
    last_w, readers = {}, collections.defaultdict(list)
    for j, o in enumerate(O):
        for r in o.src:
            if r in last_w:
                i = last_w[r]
                preds[j].append((i, LATC[O[i].cls]))
        for r in o.dst:
            if r in last_w:
                preds[j].append((last_w[r], 1))
            for i in readers[r]:
                if i != j:
                    preds[j].append((i, 0))
        for r in o.src:
            readers[r].append(j)
        for r in o.dst:
            last_w[r] = j
            readers[r] = []
        if o.is_mem:
            for i in range(j):
                p = O[i]
                if p.is_mem and (p.cls == "store" or o.cls == "store") and (body[i], body[j]) not in indep:
                    preds[j].append((i, 1 if p.cls == "store" else 0))
        if o.cls in ("csr", "sys"):
            preds[j] += [(i, 1) for i in range(j)]
        elif any(O[i].cls in ("csr", "sys") for i in range(j)):
            preds[j] += [(i, 1) for i in range(j) if O[i].cls in ("csr", "sys")]
    if n and O[-1].is_ctrl:
        preds[n - 1] += [(i, 0) for i in range(n - 1)]
    succs = [[] for _ in range(n)]
    for j in range(n):
        for (i, d) in preds[j]:
            succs[i].append((j, d))
    height = [0] * n
    for i in range(n - 1, -1, -1):
        height[i] = max([LATC[O[i].cls]] + [d + height[j] for (j, d) in succs[i]])
    cyc = [None] * n
    remaining = set(range(n))
    bundles, cycle = [], 0
    while remaining:
        chosen = []
        progress = True
        while progress:
            progress = False
            if not remaining:
                break
            if greedy:
                lo = min(remaining)
                cand = [j for j in remaining if j < lo + 64 and all(
                    cyc[i] is not None and cyc[i] + min(d, 1) <= cycle for (i, d) in preds[j])]
                cand.sort(key=lambda j: (not O[j].is_mem, j))
            else:
                cand = [j for j in remaining if all(cyc[i] is not None and cyc[i] + d <= cycle
                                                    for (i, d) in preds[j])]
                cand.sort(key=lambda j: (-height[j], j))
            for j in cand:
                if fits([O[k] for k in chosen] + [O[j]], cfg):
                    chosen.append(j)
                    cyc[j] = cycle
                    remaining.discard(j)
                    progress = True
                    break
        if chosen:
            bundles.append([body[k] for k in sorted(chosen)])
        cycle += 1
        if cycle > 100000:
            raise RuntimeError("scheduler did not converge")
    return bundles


def _is_backward(op):
    m = re.search(r"\b([0-9a-f]{6,})\b", op.ops)
    return bool(m) and int(m.group(1), 16) <= op.pc


def as_written_blocks(blocks, ops, honor_hints=True, line=64):
    """Bundles as laid out in the binary (STARBUG 1 hint semantics). A header whose bundle
    leaves its 64-byte line, or has N > 4, executes as a nop and its members as scalars.
    With honor_hints=False (a stock RV32GC core) every instruction, hint included, issues alone."""
    out, fallback = {}, 0
    for lead, seq in blocks.items():
        bl, k = [], 0
        while k < len(seq):
            o = ops[seq[k]]
            if honor_hints and o.cls == "hint" and 1 <= o.hint_n <= 4 and k + o.hint_n < len(seq):
                members = list(range(k + 1, k + 1 + o.hint_n))
                end = seq[members[-1]] + ops[seq[members[-1]]].size
                if (o.pc // line) == ((end - 1) // line):
                    bl.append(members)
                    k += 1 + o.hint_n
                    continue
                fallback += 1
            bl.append([k])
            k += 1
        out[lead] = bl
    return out, fallback


# ---------------------------------------------------------------- dynamic timing
def bank_of(word, banks, how="word"):
    """Bank of a word address. "word": low bits (classic interleaving). "xor": XOR-fold all
    log2(banks)-bit groups of the word address, so power-of-two strides spread across banks."""
    if how == "word":
        return word % banks
    k, b = banks.bit_length() - 1, 0
    while word:
        b ^= word & (banks - 1)
        word >>= k
    return b


def bundle_layout(blocks, sched, ops, header, line=64):
    """Lay the scheduled blocks out contiguously in address order (as line_crossings does) and
    return {lead: [(bytes, straddles_a_line), ...]} per bundle."""
    hdr = HEADERS[header] if isinstance(header, str) else header
    addr, out = 0x80000000, {}
    for lead in sorted(sched):
        seq = blocks[lead]
        rows = []
        for b in sched[lead]:
            nbytes = sum(ops[seq[k]].size for k in b) + (hdr(len(b)) if len(b) > 1 else 0)
            rows.append((nbytes, len(b) > 1 and addr // line != (addr + nbytes - 1) // line))
            addr += nbytes
        out[lead] = rows
    return out


def simulate(blocks, dyn, sched, ops, maddr, cfg, warm=(), hammocks=None, ii=None):
    """Time the dynamic block sequence `dyn` with the bundles in `sched`.
    hammocks: {lead: skipped op count} for cfg["predicate"]; ii: {lead: MII} for cfg["swp"]."""
    ready = {}
    t, earliest = -1, 0
    nbundles = conflicts = penalties = imiss = dmiss = crossings = fetch_stalls = 0
    banks, bhash = cfg["banks"], cfg.get("bank_hash", "word")
    P = cfg["penalty"]
    LATC = cfg.get("lat", LAT)
    W = cfg["width"]
    pred = Predictor() if cfg["branch"] == "gshare" else None
    ic = dc = None
    lb = cfg.get("line", 64).bit_length() - 1
    if cfg["caches"]:
        ic = Cache(cfg.get("icache_kb", 16) * 1024, 4, 1 << lb)
        dc = Cache(cfg.get("dcache_kb", 16) * 1024, 4, 1 << lb)
        for kind, a in warm:
            (ic if kind == "i" else dc).access(a, kind == "s")
    MISS, WB = cfg["miss"], cfg["writeback"]
    cross = cfg.get("cross", "ideal")
    FB = cfg.get("fetch_bytes", 0)
    FQ = cfg.get("fetch_queue", 0) or 2 * FB
    lay = None
    if cross != "ideal" or FB:
        lay = bundle_layout(blocks, sched, ops, cfg.get("header", "16-bit hint (SB1)"), 1 << lb)
    fq_level, fq_t = FQ, 0
    predicate = bool(cfg.get("predicate")) and hammocks is not None
    swp = bool(cfg.get("swp")) and ii is not None
    last_line = -1
    # precompute per block: per bundle (src regs, [(dst, lat)], mem op offsets, fetch lines,
    # bytes, extra issue cycles from a line straddle)
    pre = {}
    for lead, bl in sched.items():
        seq = blocks[lead]
        rows = []
        for n, b in enumerate(bl):
            src = set()
            dst = []
            mem = []
            for k in b:
                o = ops[seq[k]]
                src.update(o.src)
                dst += [(r, LATC[o.cls], o.is_mem) for r in o.dst]
                if o.is_mem:
                    mem.append((k, o.cls == "store"))
            lines = tuple(sorted({seq[k] >> lb for k in b}))
            nbytes, xc = lay[lead][n] if lay is not None else (0, 0)
            if not xc or cross == "ideal":
                xc = 0
            elif cross == "stall":
                xc = 1
            else:  # "scalar": the fetch unit declines the bundle and its ops issue one by one
                xc = len(b) - 1
            rows.append((tuple(src), tuple(dst), tuple(mem), lines, nbytes, xc))
        last = ops[seq[-1]]
        pre[lead] = (rows, last)
    prev_lead = None
    for (lead, start, taken, nxt) in dyn:
        rows, last = pre[lead]
        if swp and lead == prev_lead and lead in ii:
            # Steady-state iteration of a software-pipelined loop: it issues MII cycles after
            # the previous one (never worse than the plain schedule). Caches and bank
            # conflicts still apply; values are taken as ready when the iteration ends.
            ti = t + 1
            if earliest > ti:
                ti = earliest
            extra = 0
            for (src, dst, mem, lines, nbytes, xc) in rows:
                if ic is not None:
                    for ln in lines:
                        if ln != last_line:
                            if not ic.access(ln << lb)[0]:
                                extra += MISS
                                imiss += 1
                            last_line = ln
                if dc is not None:
                    for (k, st) in mem:
                        a = maddr[start + k]
                        if a >= 0x80000000:
                            hit, dirty = dc.access(a, st)
                            if not hit:
                                extra += MISS + (WB if dirty else 0)
                                dmiss += 1
                if banks and len(mem) > 1:
                    bc = _bank_conflicts(mem, maddr, start, banks, bhash)
                    conflicts += bc
                    extra += bc
            cost = min(ii[lead], len(rows))
            t = ti + cost - 1 + extra
            for (src, dst, mem, lines, nbytes, xc) in rows:
                for (r, lat, is_mem) in dst:
                    ready[r] = t + 1
            nbundles += cost
        else:
            for (src, dst, mem, lines, nbytes, xc) in rows:
                ti = t + 1
                if earliest > ti:
                    ti = earliest
                if ic is not None:
                    for ln in lines:
                        if ln != last_line:
                            if not ic.access(ln << lb)[0]:
                                ti += MISS
                                imiss += 1
                            last_line = ln
                for r in src:
                    v = ready.get(r, 0)
                    if v > ti:
                        ti = v
                if FB:
                    fq_level = min(FQ, fq_level + FB * (ti - fq_t))
                    if fq_level < nbytes:
                        w = -(-(nbytes - fq_level) // FB)
                        ti += w
                        fetch_stalls += w
                        fq_level += w * FB
                    fq_level -= nbytes
                    fq_t = ti
                extra = 0
                if dc is not None:
                    for (k, st) in mem:
                        a = maddr[start + k]
                        if a >= 0x80000000:
                            hit, dirty = dc.access(a, st)
                            if not hit:
                                extra += MISS + (WB if dirty else 0)
                                dmiss += 1
                if banks and len(mem) > 1:
                    bc = _bank_conflicts(mem, maddr, start, banks, bhash)
                    conflicts += bc
                    extra += bc
                if xc:
                    crossings += 1
                    extra += xc
                for (r, lat, is_mem) in dst:
                    ready[r] = ti + lat + (extra if is_mem else 0)
                t = ti + extra
                nbundles += 1
        prev_lead = lead
        if last.is_ctrl:
            if cfg["zol"] and last.cls == "branch" and _is_backward(last):
                pay = False
            elif predicate and last.cls == "branch" and lead in hammocks:
                pay = False  # if-converted: the condition becomes a predicate register
                if taken:  # the predicated-off body still occupies its issue slots
                    t += -(-hammocks[lead] // W)
            elif pred is not None:
                pay = pred.mispredicted(last, taken, nxt)
            elif last.cls == "jump":
                pay = True
            elif cfg["branch"] == "btfn":
                pay = taken != _is_backward(last)
            else:
                pay = taken
            if pay:
                earliest = t + 1 + P
                penalties += 1
                if FB:  # the fetch queue is flushed and refills during the penalty
                    fq_level, fq_t = 0, t + 1
    r = dict(cycles=t + 1, bundles=nbundles, bank_conflicts=conflicts, branch_penalties=penalties,
             icache_misses=imiss, dcache_misses=dmiss)
    if lay is not None:
        r.update(line_crossing_bundles=crossings, fetch_stall_cycles=fetch_stalls)
    return r


def _bank_conflicts(mem, maddr, start, banks, how):
    """Extra cycles for one bundle's memory ops: each op that finds its bank busy with a
    different word waits one cycle (the same word is broadcast)."""
    seen, bc = {}, 0
    for (k, _) in mem:
        w = maddr[start + k] >> 2
        bk = bank_of(w, banks, how)
        if bk in seen and seen[bk] != w:
            bc += 1
        seen.setdefault(bk, w)
    return bc


def code_bytes(blocks, sched, ops, header):
    """Static code size of the scheduled blocks. header: bytes per bundle of >= 2 ops, as a
    function of the bundle's op count (e.g. 16-bit hint: 2; 32-bit header: 4 per 4 ops)."""
    total = 0
    for lead, bl in sched.items():
        seq = blocks[lead]
        for b in bl:
            total += sum(ops[seq[k]].size for k in b) + (header(len(b)) if len(b) > 1 else 0)
    return total


def line_crossings(blocks, dyn, sched, ops, header, line=64):
    """Lay the scheduled blocks out contiguously in address order and count dynamic bundles
    that straddle an I$ line (STARBUG 1 hardware falls back to scalar for these)."""
    addr, cross = 0x80000000, {}
    for lead in sorted(sched):
        seq = blocks[lead]
        flags = []
        for b in sched[lead]:
            nbytes = sum(ops[seq[k]].size for k in b) + (header(len(b)) if len(b) > 1 else 0)
            flags.append(len(b) > 1 and addr // line != (addr + nbytes - 1) // line)
            addr += nbytes
        cross[lead] = sum(flags)
    return sum(cross[d[0]] for d in dyn)


RV32GC = make_cfg("RV32GC baseline: 1-wide, gshare", width=1, worker=set(), branch="gshare")
SB1 = make_cfg("SB1 today: 4-wide, 1 mem op (lane 0), no predictor")

# One change at a time on top of SB1 today, then the 8-wide family and a combined design.
_2B = dict(banks=2)
CONFIGS = [
    RV32GC,
    SB1,
    make_cfg("+ gshare predictor (as RV32GC)", branch="gshare"),
    make_cfg("+ static BTFN prediction", branch="btfn"),
    make_cfg("+ zero-overhead loops", zol=True),
    make_cfg("+ 1 LS + 1 load-only, 2 interleaved banks", load_lanes=(1,), max_mem=2, **_2B),
    make_cfg("+ 1 LS + 1 load-only, conflict-free", load_lanes=(1,), max_mem=2),
    make_cfg("+ 2 LS, 2 interleaved banks", mem_lanes=(0, 1), max_mem=2, max_stores=2, **_2B),
    make_cfg("+ 2 LS, conflict-free", mem_lanes=(0, 1), max_mem=2, max_stores=2),
    make_cfg("8-wide, 1 mem op", width=8),
    make_cfg("8-wide, 1 LS + 1 load-only, 2 banks", width=8, load_lanes=(1,), max_mem=2, **_2B),
    make_cfg("8-wide, 2 LS, 2 banks", width=8, mem_lanes=(0, 1), max_mem=2, max_stores=2, **_2B),
    make_cfg("4-wide, 1 LS + 1 load-only, 2 banks, gshare, ZOL", load_lanes=(1,), max_mem=2, branch="gshare", zol=True, **_2B),
    make_cfg("8-wide, 2 LS, 2 banks, gshare, ZOL", width=8, mem_lanes=(0, 1), max_mem=2, max_stores=2, branch="gshare", zol=True, **_2B),
    # Not a proposal: how much parallelism the scheduled code holds when resources stop binding.
    make_cfg("ceiling: 16-wide, 4 mem ops any lane, conflict-free, gshare, ZOL", width=16,
             mem_lanes=tuple(range(16)), max_mem=4, max_stores=4, branch_lanes=tuple(range(16)),
             branch="gshare", zol=True, worker=WORKER | {"load", "store"}),
]


def hammock_blocks(ops, blocks, max_skip=8):
    """Short forward hammocks that creg/z predication could if-convert: {lead: k} for a block
    ending in a conditional branch whose target lies at most `max_skip` straight-line
    instructions ahead (k of them), with no control transfer in between."""
    convertible = {}
    for lead, seq in blocks.items():
        br = ops[seq[-1]]
        if br.cls != "branch":
            continue
        m = re.search(r"\b([0-9a-f]{6,})\b", br.ops)
        tgt = int(m.group(1), 16) if m else None
        if tgt is None or tgt <= br.pc:
            continue
        pc, k, ok = br.pc + br.size, 0, True
        while pc < tgt:
            o = ops.get(pc)
            if o is None or o.is_ctrl or k >= max_skip:
                ok = False
                break
            pc += o.size
            k += 1
        if ok:
            convertible[lead] = k
    return convertible


def predication_opportunity(ops, blocks, dyn, max_skip=8):
    """Counts dynamic instances of hammock_blocks() and how often the branch was taken (on SB1
    today every taken branch pays the penalty)."""
    convertible = hammock_blocks(ops, blocks, max_skip)
    n = taken = 0
    for d in dyn:
        if d[0] in convertible:
            n += 1
            taken += d[2]
    return dict(static_hammocks=len(convertible), dynamic=n, dynamic_taken=taken)


# ---------------------------------------------------------------- software pipelining bound
def is_self_loop(seq, ops):
    br = ops[seq[-1]]
    if br.cls != "branch":
        return False
    m = re.search(r"\b([0-9a-f]{6,})\b", br.ops)
    return bool(m) and int(m.group(1), 16) == seq[0]


def carried_memory_deps(seq, ops, starts, maddr, msize, max_pairs=32):
    """Loop-carried memory dependences of a self-loop block, from the trace: (a, b, latency)
    for a store a in iteration t overlapping op b in iteration t+1 (and load a / store b)."""
    n, found, pairs = len(seq), {}, 0
    mem = [k for k, pc in enumerate(seq) if ops[pc].is_mem]
    for s1, s2 in zip(starts, starts[1:]):
        if s2 != s1 + n:
            continue
        pairs += 1
        for a in mem:
            sa = ops[seq[a]].cls == "store"
            for b in mem:
                if (a, b) in found or not (sa or ops[seq[b]].cls == "store"):
                    continue
                pa, pb = maddr[s1 + a], maddr[s2 + b]
                if pa < pb + msize[s2 + b] and pb < pa + msize[s1 + a]:
                    found[(a, b)] = 1 if sa else 0
        if pairs >= max_pairs:
            break
    return {(a, b, d) for (a, b), d in found.items()}


def modulo_ii(seq, ops, cfg, indep, carried_mem=()):
    """Lower bound on the initiation interval of a software-pipelined single-block loop:
    MII = max(ResMII, RecMII). ResMII: issue width, memory ports, mul/FP units, one branch per
    bundle, and Hall's condition over the lane-capability sets. RecMII: the smallest II with no
    positive dependence cycle under latency - II x distance, over true register dependences
    and memory dependences (modulo variable expansion removes anti/output dependences)."""
    LATC = cfg.get("lat", LAT)
    body = [k for k, pc in enumerate(seq) if ops[pc].cls != "nop"]
    if cfg["zol"]:
        last = ops[seq[-1]]
        if last.cls == "branch" and _is_backward(last):
            body = body[:-1]
    O = [ops[seq[k]] for k in body]
    n = len(O)
    if not n:
        return 1
    W = cfg["width"]
    res = -(-n // W)
    nmem = sum(o.is_mem for o in O)
    res = max(res, -(-nmem // cfg["max_mem"]), -(-sum(o.cls == "store" for o in O) // cfg["max_stores"]),
              sum(o.is_ctrl for o in O))
    if cfg.get("mul_units"):
        res = max(res, -(-sum(o.cls in MUL_CLS for o in O) // cfg["mul_units"]))
    if cfg.get("fp_units"):
        res = max(res, -(-sum(o.cls in FP_CLS for o in O) // cfg["fp_units"]))
    caps = [frozenset(lanes_for(o, cfg)) for o in O]
    sets = set(caps)
    for x in list(sets):
        for y in list(sets):
            sets.add(x | y)
    for S in sets:
        if S:
            res = max(res, -(-sum(c <= S for c in caps) // len(S)))
    # dependence graph: (u, v, latency, iteration distance)
    pos = {k: j for j, k in enumerate(body)}
    edges = []
    last_w, exposed = {}, collections.defaultdict(list)
    for j, o in enumerate(O):
        for r in o.src:
            if r in last_w:
                edges.append((last_w[r], j, LATC[O[last_w[r]].cls], 0))
            else:
                exposed[r].append(j)
        for r in o.dst:
            last_w[r] = j
        if o.is_mem:
            for i in range(j):
                p = O[i]
                if p.is_mem and (p.cls == "store" or o.cls == "store") and (body[i], body[j]) not in indep:
                    edges.append((i, j, 1 if p.cls == "store" else 0, 0))
    carried = [(last_w[r], j, LATC[O[last_w[r]].cls], 1) for r, js in exposed.items() if r in last_w for j in js]
    carried += [(pos[a], pos[b], d, 1) for (a, b, d) in carried_mem if a in pos and b in pos]
    if not carried:
        return max(res, 1)
    edges += carried

    def feasible(II):
        """No positive cycle under weights lat - II*distance (Bellman-Ford from a virtual
        source; edges are in program order, so each pass settles one loop-carried hop)."""
        dist = [0] * n
        for _ in range(n + 1):
            changed = False
            for (u, v, w, d) in edges:
                x = dist[u] + w - II * d
                if x > dist[v]:
                    dist[v] = x
                    changed = True
            if not changed:
                return True
        return False

    lo = max(res, 1)
    if feasible(lo):
        return lo
    hi = lo + sum(w for (_, _, w, _) in edges) + 1
    while hi - lo > 1:  # invariant: lo infeasible, hi feasible
        mid = (lo + hi) // 2
        if feasible(mid):
            hi = mid
        else:
            lo = mid
    return hi


HEADERS = {
    "16-bit hint (SB1)": lambda n: 2,
    "32-bit header, 4 ops/header (P-bit chaining)": lambda n: 4 * ((n + 3) // 4),
    "32-bit header + 2nd word above 4 ops": lambda n: 4 if n <= 4 else 8,
}


def load(elf, trace):
    ops = disassemble(elf)
    syms = symbols(elf)
    pcs, maddr, msize, warm = read_window(trace, syms, ops)
    blocks, dyn = basic_blocks(pcs, ops)
    return ops, pcs, maddr, msize, blocks, dyn, warm


def run_as_written(elf, trace, cfg, honor_hints=True):
    ops, pcs, maddr, msize, blocks, dyn, warm = load(elf, trace)
    sched, fallback = as_written_blocks(blocks, ops, honor_hints)
    r = simulate(blocks, dyn, sched, ops, maddr, cfg, warm)
    r.update(instructions=len(pcs), static_fallback_bundles=fallback)
    return r


def run_scheduled(elf, trace, cfgs=CONFIGS, alias="oracle"):
    ops, pcs, maddr, msize, blocks, dyn, warm = load(elf, trace)
    inst = collections.defaultdict(list)
    for d in dyn:
        inst[d[0]].append(d[1])
    indep = {lead: (independent_pairs(seq, ops, inst[lead], maddr, msize) if alias == "oracle" else set())
             for lead, seq in blocks.items()}
    hammocks = hammock_blocks(ops, blocks)
    loops = [lead for lead, seq in blocks.items() if is_self_loop(seq, ops)] if any(c.get("swp") for c in cfgs) else []
    cmem = {lead: carried_memory_deps(blocks[lead], ops, inst[lead], maddr, msize) if alias == "oracle"
            else {(a, b, 1 if ops[pa].cls == "store" else 0)
                  for a, pa in enumerate(blocks[lead]) for b, pb in enumerate(blocks[lead])
                  if ops[pa].is_mem and ops[pb].is_mem and "store" in (ops[pa].cls, ops[pb].cls)}
            for lead in loops}
    res = {"instructions": len(pcs), "blocks": len(blocks), "configs": {},
           "predication": predication_opportunity(ops, blocks, dyn)}
    cache, iicache = {}, {}
    for cfg in cfgs:
        key = sched_key(cfg)
        if key not in cache:
            cache[key] = {lead: schedule_block(seq, ops, cfg, indep[lead]) for lead, seq in blocks.items()}
        sched = cache[key]
        ii = None
        if cfg.get("swp"):
            if key not in iicache:
                iicache[key] = {lead: modulo_ii(blocks[lead], ops, cfg, indep[lead], cmem[lead]) for lead in loops}
            ii = iicache[key]
        r = simulate(blocks, dyn, sched, ops, maddr, cfg, warm, hammocks=hammocks, ii=ii)
        if cfg["width"] > 1:
            r["code_bytes"] = {h: code_bytes(blocks, sched, ops, f) for h, f in HEADERS.items()}
            r["line_crossings"] = {h: line_crossings(blocks, dyn, sched, ops, f) for h, f in HEADERS.items()}
            r["ops_per_bundle"] = round(sum(len(b) for d in dyn for b in sched[d[0]]) / max(1, r["bundles"]), 3)
        res["configs"][cfg["name"]] = r
    return res


if __name__ == "__main__":
    mode, elf, trace = sys.argv[1:4]
    if mode == "written":
        print(json.dumps({"rv32gc": run_as_written(elf, trace, RV32GC, honor_hints=False),
                          "starbug": run_as_written(elf, trace, SB1)}, indent=1))
    else:
        print(json.dumps(run_scheduled(elf, trace), indent=1))
