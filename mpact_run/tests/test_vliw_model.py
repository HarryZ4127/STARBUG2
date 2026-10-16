#!/usr/bin/env python3
"""Unit tests for vliw_model.py: every machine knob on small hand-built programs, plus a
regression check of the full sweep against results/raw/ when the traces are unpacked.

Run from mpact_run/:  python3 -m unittest discover -s tests -v
Only the standard library is needed; the regression test also needs $STARBUG_HOME (see README).
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import vliw_model as vm  # noqa: E402

BASE = 0x80000000
mk = vm.make_cfg


def prog(lines, base=BASE):
    """[(mnemonic, operands[, size])] -> (ops by pc, [pc]). Write branch targets as "@k" for
    the k-th instruction; they become absolute addresses as objdump prints them."""
    pcs, pc = [], base
    for ln in lines:
        pcs.append(pc)
        pc += ln[2] if len(ln) > 2 else 4
    ops = {}
    for pc, ln in zip(pcs, lines):
        operands = ln[1]
        if "@" in operands:
            head, k = operands.rsplit("@", 1)
            operands = f"{head}{pcs[int(k)]:x}"
        ops[pc] = vm.Op(pc, ln[2] if len(ln) > 2 else 4, ln[0], operands)
    return ops, pcs


def run(ops, trace, cfg, maddr=None, msize=None, alias="oracle", warm=()):
    """Schedule and time a dynamic pc sequence the way run_scheduled() does, without an ELF."""
    maddr = maddr or [-1] * len(trace)
    msize = msize or [4 if a >= 0 else 0 for a in maddr]
    blocks, dyn = vm.basic_blocks(trace, ops)
    inst = {}
    for d in dyn:
        inst.setdefault(d[0], []).append(d[1])
    indep = {lead: vm.independent_pairs(seq, ops, inst[lead], maddr, msize) if alias == "oracle" else set()
             for lead, seq in blocks.items()}
    sched = {lead: vm.schedule_block(seq, ops, cfg, indep[lead]) for lead, seq in blocks.items()}
    ii = None
    if cfg.get("swp"):
        ii = {lead: vm.modulo_ii(seq, ops, cfg, indep[lead],
                                 vm.carried_memory_deps(seq, ops, inst[lead], maddr, msize))
              for lead, seq in blocks.items() if vm.is_self_loop(seq, ops)}
    r = vm.simulate(blocks, dyn, sched, ops, maddr, cfg, warm, hammocks=vm.hammock_blocks(ops, blocks), ii=ii)
    return r, sched, blocks


NOCACHE = dict(caches=False)


class OpClassification(unittest.TestCase):
    def test_classes(self):
        cases = {("lw", "x10,0(x11)"): "load", ("c.flw", "f8,4(x9)"): "load",
                 ("sw", "x10,0(x11)"): "store", ("fsw", "f1,0(x2)"): "store",
                 ("beq", "x10,x11,80000000"): "branch", ("jal", "x1,80000010"): "jump",
                 ("c.li", "x0,3"): "hint", ("c.nop", ""): "nop", ("addi", "x0,x0,0"): "nop",
                 ("mul", "x1,x2,x3"): "mul", ("divu", "x1,x2,x3"): "div",
                 ("fmadd.s", "f1,f2,f3,f4,rne"): "fp", ("fdiv.s", "f1,f2,f3,rne"): "fdiv",
                 ("fcvt.w.s", "x5,f1,rtz"): "fcvti", ("fmv.x.w", "x5,f1"): "fmvx",
                 ("csrrs", "x5,0xb00,x0"): "csr", ("xor", "x1,x2,x3"): "alu"}
        for (mn, opnds), cls in cases.items():
            self.assertEqual(vm.Op(BASE, 4, mn, opnds).cls, cls, mn)

    def test_registers(self):
        o = vm.Op(BASE, 4, "sw", "x10,8(x11)")
        self.assertEqual((o.src, o.dst), (["x10", "x11"], []))
        o = vm.Op(BASE, 2, "c.addi", "x10,4")
        self.assertEqual((o.src, o.dst), (["x10"], ["x10"]))
        o = vm.Op(BASE, 4, "add", "x0,x1,x2")
        self.assertEqual(o.dst, [])  # writes to x0 are not dependences
        self.assertEqual(vm.Op(BASE, 2, "c.li", "x0,4").hint_n, 4)


def ops_(*specs):
    return [vm.Op(BASE + 4 * i, 4, mn, o) for i, (mn, o) in enumerate(specs)]


ALU = ("add", "x1,x2,x3")
LW = ("lw", "x5,0(x6)")
SW = ("sw", "x5,0(x6)")
FP = ("fadd.s", "f1,f2,f3,rne")


class BundleLegality(unittest.TestCase):
    def test_width(self):
        cfg = mk("w4")
        self.assertTrue(vm.fits(ops_(*[ALU] * 4), cfg))
        self.assertFalse(vm.fits(ops_(*[ALU] * 5), cfg))
        self.assertTrue(vm.fits(ops_(*[ALU] * 8), mk("w8", width=8)))

    def test_one_memory_op_in_lane0(self):
        sb1 = mk("sb1")
        self.assertTrue(vm.fits(ops_(LW, ALU, ALU, ALU), sb1))
        self.assertFalse(vm.fits(ops_(LW, LW), sb1))
        self.assertFalse(vm.fits(ops_(LW, SW), sb1))

    def test_load_only_lane(self):
        lslo = mk("lslo", load_lanes=(1,), max_mem=2)
        self.assertTrue(vm.fits(ops_(LW, LW, ALU, ALU), lslo))
        self.assertTrue(vm.fits(ops_(SW, LW), lslo))
        self.assertFalse(vm.fits(ops_(SW, SW), lslo))  # max_stores = 1 and only lane 0 stores
        self.assertFalse(vm.fits(ops_(LW, LW, LW), lslo))

    def test_two_ls(self):
        two = mk("2ls", mem_lanes=(0, 1), max_mem=2, max_stores=2)
        self.assertTrue(vm.fits(ops_(SW, SW), two))
        self.assertFalse(vm.fits(ops_(SW, SW, LW), two))

    def test_control_and_csr_lane0(self):
        sb1 = mk("sb1")
        br = ("beq", "x1,x2,80000000")
        self.assertTrue(vm.fits(ops_(br, ALU, ALU, ALU), sb1))
        self.assertFalse(vm.fits(ops_(br, LW), sb1))  # both need lane 0
        self.assertFalse(vm.fits(ops_(br, br), sb1))
        self.assertFalse(vm.fits(ops_(("csrrs", "x5,0xb00,x0"), LW), sb1))

    def test_fp_in_worker_lanes(self):
        self.assertTrue(vm.fits(ops_(FP, FP, FP, FP), mk("sb1")))
        no_fp = mk("fp lane 0 only", worker=vm.WORKER - {"fp"})
        self.assertFalse(vm.fits(ops_(FP, FP), no_fp))

    def test_unit_and_port_limits(self):
        mul = ("mul", "x1,x2,x3")
        self.assertTrue(vm.fits(ops_(mul, mul), mk("m")))
        self.assertFalse(vm.fits(ops_(mul, mul), mk("m1", mul_units=1)))
        self.assertFalse(vm.fits(ops_(FP, FP), mk("f1", fp_units=1)))
        a, b = ("add", "x1,x2,x3"), ("add", "x4,x5,x6")
        self.assertTrue(vm.fits(ops_(a, b), mk("r4", max_reads=4)))
        self.assertFalse(vm.fits(ops_(a, b), mk("r3", max_reads=3)))
        self.assertFalse(vm.fits(ops_(a, b), mk("w1", max_writes=1)))
        # read ports are counted per register file, and a shared source is read once
        self.assertTrue(vm.fits(ops_(("add", "x1,x2,x2"), ("add", "x4,x2,x2"), FP), mk("r2", max_reads=3)))


class Scheduling(unittest.TestCase):
    def test_raw_chain_is_serial(self):
        ops, pcs = prog([("addi", "x1,x1,1"), ("addi", "x1,x1,1"), ("addi", "x1,x1,1")])
        r, sched, _ = run(ops, pcs, mk("sb1", **NOCACHE))
        self.assertEqual(r["cycles"], 3)
        self.assertEqual(len(sched[pcs[0]]), 3)

    def test_independent_ops_pack(self):
        ops, pcs = prog([("addi", f"x{i},x{i},1") for i in range(1, 9)])
        r, _, _ = run(ops, pcs, mk("sb1", **NOCACHE))
        self.assertEqual(r["cycles"], 2)
        r, _, _ = run(ops, pcs, mk("w8", width=8, **NOCACHE))
        self.assertEqual(r["cycles"], 1)

    def test_war_may_share_a_bundle_waw_may_not(self):
        ops, pcs = prog([("add", "x3,x1,x2"), ("addi", "x1,x0,5")])  # WAR on x1
        r, _, _ = run(ops, pcs, mk("sb1", **NOCACHE))
        self.assertEqual(r["cycles"], 1)
        ops, pcs = prog([("addi", "x1,x0,4"), ("addi", "x1,x0,5")])  # WAW on x1
        r, _, _ = run(ops, pcs, mk("sb1", **NOCACHE))
        self.assertEqual(r["cycles"], 2)

    def test_load_use_latency(self):
        ops, pcs = prog([("lw", "x5,0(x6)"), ("addi", "x7,x5,1")])
        maddr = [BASE + 0x1000, -1]
        for load_lat, cycles in ((1, 2), (2, 3), (3, 4)):
            r, _, _ = run(ops, pcs, mk("l", lat={"load": load_lat}, **NOCACHE), maddr)
            self.assertEqual(r["cycles"], cycles, load_lat)

    def test_memory_disambiguation(self):
        # a store and a load to different addresses are independent, so with 2 LS ports they
        # share a bundle; to the same address, or with no alias information, they may not.
        ops, pcs = prog([("sw", "x1,0(x2)"), ("lw", "x3,0(x4)")])
        two = mk("2ls", mem_lanes=(0, 1), max_mem=2, max_stores=2, **NOCACHE)
        r, _, _ = run(ops, pcs, two, [BASE + 0x100, BASE + 0x200])
        self.assertEqual(r["cycles"], 1)
        r, _, _ = run(ops, pcs, two, [BASE + 0x100, BASE + 0x100])  # same address: ordered
        self.assertEqual(r["cycles"], 2)
        r, _, _ = run(ops, pcs, two, [BASE + 0x100, BASE + 0x200], alias="none")
        self.assertEqual(r["cycles"], 2)

    def test_packetizer_ignores_latency(self):
        # load, its consumer, and independent work: the list scheduler fills the load shadow
        # with the independent ops; the greedy packetizer puts the consumer right after the
        # load and stalls.
        lines = [("lw", "x5,0(x6)"), ("addi", "x7,x5,1"), ("lw", "x8,4(x6)"), ("addi", "x9,x8,1")]
        ops, pcs = prog(lines)
        maddr = [BASE + 0x100, -1, BASE + 0x104, -1]
        lst, _, _ = run(ops, pcs, mk("list", **NOCACHE), maddr)
        pk, _, _ = run(ops, pcs, mk("pk", sched="packetizer", **NOCACHE), maddr)
        self.assertEqual(lst["cycles"], 4)  # lw | lw | - | addi,addi
        self.assertGreaterEqual(pk["cycles"], lst["cycles"])


def loop(body, n, base=BASE):
    """A counted single-block loop: body..., addi x31,x31,-1, bnez x31 -> top. Returns
    (ops, dynamic trace of n iterations, index of the first instruction after the loop)."""
    lines = list(body) + [("addi", "x31,x31,-1"), ("bne", "x31,x0,@0"), ("addi", "x30,x30,0")]
    ops, pcs = prog(lines, base)
    nb = len(body) + 2
    trace = pcs[:nb] * n + [pcs[nb]]
    return ops, pcs, trace


class ControlFlow(unittest.TestCase):
    def test_taken_branch_penalty_and_zol(self):
        ops, pcs, trace = loop([("addi", "x1,x1,1")], 10)
        none, _, _ = run(ops, trace, mk("none", **NOCACHE))
        zol, _, _ = run(ops, trace, mk("zol", zol=True, **NOCACHE))
        btfn, _, _ = run(ops, trace, mk("btfn", branch="btfn", **NOCACHE))
        self.assertEqual(none["branch_penalties"], 9)  # the final not-taken exit is free
        self.assertEqual(zol["branch_penalties"], 0)
        self.assertEqual(btfn["branch_penalties"], 1)  # backward-taken right 9x, wrong at exit
        # ZOL saves the 9 penalties and, per iteration, the bundle the bne needed after addi x31
        self.assertEqual(none["cycles"] - zol["cycles"], 9 * 2 + 10)

    def test_gshare_learns_a_loop(self):
        ops, pcs, trace = loop([("addi", "x1,x1,1")], 200)
        g, _, _ = run(ops, trace, mk("g", branch="gshare", **NOCACHE))
        self.assertLess(g["branch_penalties"], 20)

    def test_penalty_size(self):
        ops, pcs, trace = loop([("addi", "x1,x1,1")], 10)
        p2, _, _ = run(ops, trace, mk("p2", **NOCACHE))
        p4, _, _ = run(ops, trace, mk("p4", penalty=4, **NOCACHE))
        self.assertEqual(p4["cycles"] - p2["cycles"], 9 * 2)

    def test_predication_removes_hammock_penalty(self):
        # beq skips 2 instructions; trace takes the branch every time
        lines = [("addi", "x1,x1,1"), ("beq", "x1,x2,@4"), ("addi", "x3,x3,1"), ("addi", "x4,x4,1"),
                 ("addi", "x5,x5,1")]
        ops, pcs = prog(lines)
        trace = [pcs[0], pcs[1], pcs[4]]
        base, _, _ = run(ops, trace, mk("b", **NOCACHE))
        pred, _, _ = run(ops, trace, mk("p", predicate=True, **NOCACHE))
        self.assertEqual(base["branch_penalties"], 1)
        self.assertEqual(pred["branch_penalties"], 0)
        # 2 cycles of penalty saved, 1 bundle spent on the 2 nullified ops (4-wide)
        self.assertEqual(base["cycles"] - pred["cycles"], 2 - 1)


class Memory(unittest.TestCase):
    def test_bank_of(self):
        self.assertEqual([vm.bank_of(w, 2) for w in range(4)], [0, 1, 0, 1])
        self.assertEqual([vm.bank_of(w, 4) for w in range(5)], [0, 1, 2, 3, 0])
        # stride-2 words all land in bank 0 with word interleaving, but spread with XOR folding
        self.assertEqual({vm.bank_of(w, 2) for w in range(0, 64, 2)}, {0})
        self.assertEqual({vm.bank_of(w, 2, "xor") for w in range(0, 64, 2)}, {0, 1})

    def _pair(self, a0, a1, **kw):
        ops, pcs = prog([("lw", "x5,0(x6)"), ("lw", "x7,0(x8)")])
        cfg = mk("lslo", load_lanes=(1,), max_mem=2, **NOCACHE, **kw)
        r, _, _ = run(ops, pcs, cfg, [a0, a1])
        return r

    def test_bank_conflicts(self):
        self.assertEqual(self._pair(BASE, BASE + 4, banks=2)["bank_conflicts"], 0)
        self.assertEqual(self._pair(BASE, BASE + 8, banks=2)["bank_conflicts"], 1)
        self.assertEqual(self._pair(BASE, BASE + 8, banks=4)["bank_conflicts"], 0)
        self.assertEqual(self._pair(BASE, BASE, banks=2)["bank_conflicts"], 0)  # same word
        self.assertEqual(self._pair(BASE, BASE + 8, banks=0)["bank_conflicts"], 0)  # ideal
        c = self._pair(BASE, BASE + 8, banks=2)["cycles"] - self._pair(BASE, BASE + 4, banks=2)["cycles"]
        self.assertEqual(c, 1)

    def test_dcache_size_and_miss_penalty(self):
        # stream over 32 KB twice: misses in a 16 KB cache, hits the second time in a 64 KB one
        n = 2 * (32768 // 64)
        lines = [("lw", "x5,0(x6)")]
        ops, pcs = prog(lines)
        trace = pcs * n
        maddr = [BASE + 0x10000 + 64 * (i % (n // 2)) for i in range(n)]
        small, _, _ = run(ops, trace, mk("16k", dcache_kb=16), maddr)
        big, _, _ = run(ops, trace, mk("64k", dcache_kb=64), maddr)
        self.assertEqual(small["dcache_misses"], n)
        self.assertEqual(big["dcache_misses"], n // 2)
        fast, _, _ = run(ops, trace, mk("16k fast", dcache_kb=16, miss=9), maddr)
        # every miss, the one cold I$ miss included, costs 9 cycles less
        self.assertEqual(small["cycles"] - fast["cycles"], 9 * (small["dcache_misses"] + small["icache_misses"]))


class FrontEnd(unittest.TestCase):
    def _straddle(self, cross, header="16-bit hint (SB1)"):
        # 30 compressed nops-equivalents (2 B each) then a 4-op bundle of 4 B ops: the layout
        # puts the bundle at offset 60 + hint, across the 64 B line.
        lines = [("c.addi", f"x{8 + i % 8},1", 2) for i in range(30)]
        lines += [("addi", f"x{16 + i},x0,1") for i in range(4)]
        ops, pcs = prog(lines)
        return run(ops, pcs, mk("x", cross=cross, header=header, **NOCACHE))[0]

    def test_line_crossing_policies(self):
        ideal, stall, scalar = (self._straddle(c) for c in ("ideal", "stall", "scalar"))
        self.assertGreater(stall["line_crossing_bundles"], 0)
        self.assertEqual(stall["cycles"] - ideal["cycles"], stall["line_crossing_bundles"])
        self.assertGreater(scalar["cycles"], stall["cycles"])

    def test_fetch_bandwidth(self):
        ops, pcs = prog([("addi", f"x{i},x{i},1") for i in range(1, 17)])  # 4 bundles of 16 B
        wide, _, _ = run(ops, pcs, mk("unl", **NOCACHE))
        f8, _, _ = run(ops, pcs, mk("f8", fetch_bytes=8, **NOCACHE))
        f32, _, _ = run(ops, pcs, mk("f32", fetch_bytes=32, **NOCACHE))
        self.assertEqual(wide["cycles"], 4)
        self.assertEqual(f32["cycles"], 4)  # 18 B per bundle with the hint; 32 B/cycle keeps up
        self.assertGreater(f8["cycles"], 6)  # 72 B at 8 B/cycle, minus the initially full queue


class SoftwarePipelining(unittest.TestCase):
    def test_recurrence_bound(self):
        # an FP accumulator chain: fadd f1 += f2 every iteration (latency 2) -> RecMII 2
        ops, pcs, trace = loop([("fadd.s", "f1,f1,f2,rne")], 4)
        seq = pcs[:3]
        self.assertTrue(vm.is_self_loop(seq, ops))
        self.assertEqual(vm.modulo_ii(seq, ops, mk("s"), set()), 2)
        self.assertEqual(vm.modulo_ii(seq, ops, mk("s3", lat={"fp": 3}), set()), 3)

    def test_resource_bound(self):
        # 3 loads + counter + branch. On SB1 the loads and the branch all need lane 0: ResMII 4;
        # with zero-overhead loops the branch goes away: 3; a load-only lane 1 halves it: 2.
        body = [("lw", f"x{5 + i},{4 * i}(x6)") for i in range(3)]
        ops, pcs, trace = loop(body, 4)
        seq = pcs[:5]
        self.assertEqual(vm.modulo_ii(seq, ops, mk("s"), set()), 4)
        self.assertEqual(vm.modulo_ii(seq, ops, mk("z", zol=True), set()), 3)
        self.assertEqual(vm.modulo_ii(seq, ops, mk("2", load_lanes=(1,), max_mem=2), set()), 2)

    def test_carried_memory_recurrence(self):
        # x[i+1] = x[i] + 1 through memory: lw; addi; sw to the address the next lw reads
        body = [("lw", "x5,0(x6)"), ("addi", "x5,x5,1"), ("sw", "x5,0(x6)")]
        ops, pcs, trace = loop(body, 6)
        seq = pcs[:5]
        maddr = []
        for t in range(len(trace)):
            k = pcs.index(trace[t])
            maddr.append(BASE + 0x100 if k in (0, 2) else -1)
        msize = [4 if a >= 0 else 0 for a in maddr]
        starts = [i for i, p in enumerate(trace) if p == pcs[0]]
        cm = vm.carried_memory_deps(seq, ops, starts, maddr, msize)
        self.assertIn((2, 0, 1), cm)  # store feeds next iteration's load
        # lw(2) -> addi(1) -> sw(1 to the load) = 4
        self.assertEqual(vm.modulo_ii(seq, ops, mk("s"), set(), cm), 4)

    def test_swp_beats_list_schedule_on_a_latency_bound_loop(self):
        body = [("lw", "x5,0(x6)"), ("fadd.s", "f1,f2,f3,rne"), ("addi", "x7,x5,1"), ("addi", "x6,x6,4")]
        ops, pcs, trace = loop(body, 50)
        maddr = [BASE + 0x100 + 4 * (i // 7) if pcs.index(p) == 0 else -1 for i, p in enumerate(trace)]
        # 8-wide with ZOL: 5 ops, one load, 1-cycle recurrences -> II 1
        base, _, _ = run(ops, trace, mk("z", width=8, zol=True, **NOCACHE), maddr)
        swp, _, _ = run(ops, trace, mk("zs", width=8, zol=True, swp=True, **NOCACHE), maddr)
        self.assertLess(swp["cycles"], base["cycles"])
        self.assertLessEqual(swp["cycles"], 50 + 10)
        # 4-wide: 5 ops need 2 bundles -> II 2
        swp4, _, _ = run(ops, trace, mk("zs4", zol=True, swp=True, **NOCACHE), maddr)
        self.assertLessEqual(swp4["cycles"], 2 * 50 + 10)


class Defaults(unittest.TestCase):
    def test_new_knobs_default_to_old_behaviour(self):
        c = mk("x")
        for k, v in dict(mul_units=None, fp_units=None, max_reads=None, max_writes=None,
                         bank_hash="word", icache_kb=16, dcache_kb=16, line=64, fetch_bytes=0,
                         cross="ideal", predicate=False, sched="list", swp=False).items():
            self.assertEqual(c[k], v, k)
        self.assertEqual(c["lat"], vm.LAT)

    def test_sched_key_shares_schedules(self):
        self.assertEqual(vm.sched_key(mk("a")), vm.sched_key(mk("b", branch="gshare", banks=2)))
        self.assertNotEqual(vm.sched_key(mk("a")), vm.sched_key(mk("b", zol=True)))
        self.assertNotEqual(vm.sched_key(mk("a")), vm.sched_key(mk("b", lat={"load": 3})))


H = os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug"))
RAW = os.path.join(os.path.dirname(HERE), "results", "raw", "isa_sweep.json")


@unittest.skipUnless(os.path.isdir(os.path.join(H, "traces")), "traces not unpacked in $STARBUG_HOME")
class Regression(unittest.TestCase):
    """The extended model must reproduce the 2026-10-13 results exactly."""

    def test_isa_sweep_unchanged(self):
        sys.path.insert(0, os.path.dirname(HERE))
        from sweep_isa import one
        with open(RAW) as f:
            ref = json.load(f)
        for b in ("fir_q15", "crc32"):
            self.assertEqual(json.loads(json.dumps(one(b)[1])), ref[b], b)


if __name__ == "__main__":
    unittest.main()
