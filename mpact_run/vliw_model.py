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
"""
import bisect
import collections
import json
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
             caches=True, miss=18, writeback=16):
    """mem_lanes: lanes that can load and store; load_lanes: extra load-only lanes;
    banks: 0 = ideal (no conflicts), 2 = word-interleaved 2-bank memory;
    branch: none (every taken transfer pays `penalty`: the starbug config has no predictor),
            gshare (Wally rv32gc front end), btfn (static backward-taken/forward-not-taken);
    zol: zero-overhead loops (backward loop branches are free and take no slot);
    caches: model the 16 KB I$/D$, blocking, `miss` cycles per line fill (+`writeback` if dirty)."""
    return dict(name=name, width=width, mem_lanes=tuple(mem_lanes), load_lanes=tuple(load_lanes),
                max_mem=max_mem, max_stores=max_stores, branch_lanes=tuple(branch_lanes),
                banks=banks, branch=branch, zol=zol, worker=set(worker), penalty=penalty,
                caches=caches, miss=miss, writeback=writeback)


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
                preds[j].append((i, LAT[O[i].cls]))
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
        height[i] = max([LAT[O[i].cls]] + [d + height[j] for (j, d) in succs[i]])
    cyc = [None] * n
    remaining = set(range(n))
    bundles, cycle = [], 0
    while remaining:
        chosen = []
        progress = True
        while progress:
            progress = False
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
def simulate(blocks, dyn, sched, ops, maddr, cfg, warm=()):
    ready = {}
    t, earliest = -1, 0
    nbundles = conflicts = penalties = imiss = dmiss = 0
    banks = cfg["banks"]
    P = cfg["penalty"]
    pred = Predictor() if cfg["branch"] == "gshare" else None
    ic = dc = None
    if cfg["caches"]:
        ic, dc = Cache(), Cache()
        for kind, a in warm:
            (ic if kind == "i" else dc).access(a, kind == "s")
    MISS, WB = cfg["miss"], cfg["writeback"]
    last_line = -1
    # precompute per block: per bundle (src regs, [(dst, lat)], mem op offsets, fetch lines)
    pre = {}
    for lead, bl in sched.items():
        seq = blocks[lead]
        rows = []
        for b in bl:
            src = set()
            dst = []
            mem = []
            for k in b:
                o = ops[seq[k]]
                src.update(o.src)
                dst += [(r, LAT[o.cls], o.is_mem) for r in o.dst]
                if o.is_mem:
                    mem.append((k, o.cls == "store"))
            lines = tuple(sorted({seq[k] >> 6 for k in b}))
            rows.append((tuple(src), tuple(dst), tuple(mem), lines))
        last = ops[seq[-1]]
        pre[lead] = (rows, last)
    for (lead, start, taken, nxt) in dyn:
        rows, last = pre[lead]
        for (src, dst, mem, lines) in rows:
            ti = t + 1
            if earliest > ti:
                ti = earliest
            if ic is not None:
                for ln in lines:
                    if ln != last_line:
                        if not ic.access(ln << 6)[0]:
                            ti += MISS
                            imiss += 1
                        last_line = ln
            for r in src:
                v = ready.get(r, 0)
                if v > ti:
                    ti = v
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
                seen, bc = {}, 0
                for (k, _) in mem:
                    w = maddr[start + k] >> 2
                    bk = w % banks
                    if bk in seen and seen[bk] != w:
                        bc += 1
                    seen.setdefault(bk, w)
                conflicts += bc
                extra += bc
            for (r, lat, is_mem) in dst:
                ready[r] = ti + lat + (extra if is_mem else 0)
            t = ti + extra
            nbundles += 1
        if last.is_ctrl:
            if cfg["zol"] and last.cls == "branch" and _is_backward(last):
                pay = False
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
    return dict(cycles=t + 1, bundles=nbundles, bank_conflicts=conflicts, branch_penalties=penalties,
                icache_misses=imiss, dcache_misses=dmiss)


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


def predication_opportunity(ops, blocks, dyn, max_skip=8):
    """Short forward hammocks that creg/z predication could if-convert: a conditional branch
    whose target lies at most `max_skip` straight-line instructions ahead, with no control
    transfer in between. Counts dynamic instances and how often the branch was taken (on SB1
    today every taken branch pays the penalty)."""
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
    n = taken = 0
    for d in dyn:
        if d[0] in convertible:
            n += 1
            taken += d[2]
    return dict(static_hammocks=len(convertible), dynamic=n, dynamic_taken=taken)

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
    res = {"instructions": len(pcs), "blocks": len(blocks), "configs": {},
           "predication": predication_opportunity(ops, blocks, dyn)}
    for cfg in cfgs:
        sched = {lead: schedule_block(seq, ops, cfg, indep[lead]) for lead, seq in blocks.items()}
        r = simulate(blocks, dyn, sched, ops, maddr, cfg, warm)
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
