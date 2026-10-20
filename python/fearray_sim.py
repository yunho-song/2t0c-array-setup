"""
fearray_sim.py — software stand-in for FeArrayArduino (no hardware needed).

Emulates the firmware's output for the P/D/PD/DP/R/INIT scenarios with a toy
FeFET model: selected cells move up on program pulses and down on erase pulses,
half-selected cells (same WWL column or same WBL row) drift slightly, others
only show ADC noise.  Used by `fearray_measure.py --sim` and the unit test.
"""
from __future__ import annotations

import random
import time
from typing import Iterator, List

from fearray_arduino import ScenarioParams, ReadRecord, NUM_CH


class FeArraySim:
    def __init__(self, port: str = "SIM", seed: int = 0, noise: float = 2.0,
                 disturb: float = 0.3, fast: bool = True):
        self.port = port
        self.rng = random.Random(seed)
        self.noise = noise
        self.disturb = disturb
        self.fast = fast
        self.state = [[300.0 for _ in range(5)] for _ in range(5)]   # ADC-ish "cell state"

    def connect(self, reset_wait: float = 0.0) -> None:
        pass

    def close(self) -> None:
        pass

    # ── model ─────────────────────────────────────────────────────────
    def _pulse(self, p: ScenarioParams, program: bool) -> None:
        for r in range(5):
            for c in range(5):
                sel_r, sel_c = r in p.rows, c in p.cols
                s = self.state[r][c]
                if sel_r and sel_c:
                    if program:
                        s += (900 - s) * 0.12 * (p.pot_pw / 5000) ** 0.3
                    else:
                        s -= (s - 150) * 0.12 * (p.dep_pw / 5000) ** 0.3
                elif sel_c and not program:
                    # erase pulse = WWL pulse on the whole column -> half-selected erase disturb
                    s -= (s - 150) * 0.12 * self.disturb
                elif sel_c and program and not p.wwl_hold:
                    s += 0.0
                elif sel_r and program:
                    s += (900 - s) * 0.01 * self.disturb   # WBL high on the row
                self.state[r][c] = s

    def _read(self, p: ScenarioParams, set_: int, phase: str, pulse: int, t0: float) -> List[ReadRecord]:
        if not self.fast:
            time.sleep(0.02)
        out = []
        for r in range(5):
            adc = [int(max(0, min(1023, self.state[r][c] + self.rng.gauss(0, self.noise / max(1, p.avg_count) ** 0.5))))
                   for c in range(NUM_CH)]
            out.append(ReadRecord(set_, phase, pulse, r, adc, time.time() - t0))
        return out

    # ── same interface as FeArrayArduino.run ─────────────────────────
    def run(self, p: ScenarioParams, overall_timeout=None, verbose: bool = False) -> Iterator[ReadRecord]:
        t0 = time.time()
        ri = max(1, p.read_interval)
        s = p.scenario

        def phase_P(set_):
            for k in range(p.pot_num):
                self._pulse(p, True)
                if p.read_interval > 0 and (k + 1) % ri == 0:
                    yield from self._read(p, set_, "P", k + 1, t0)

        def phase_D(set_):
            for k in range(p.dep_num):
                self._pulse(p, False)
                if p.read_interval > 0 and (k + 1) % ri == 0:
                    yield from self._read(p, set_, "D", k + 1, t0)

        if s in ("P", "D", "PD", "DP", "INIT"):
            yield from self._read(p, 0, "I", 0, t0)
        if s == "P":
            yield from phase_P(1)
        elif s == "D":
            yield from phase_D(1)
        elif s == "PD":
            for i in range(1, p.set_num + 1):
                yield from phase_P(i)
                yield from phase_D(i)
        elif s == "DP":
            for i in range(1, p.set_num + 1):
                yield from phase_D(i)
                yield from phase_P(i)
        elif s in ("R", "DBGR"):
            for i in range(max(1, p.r_points) if s == "R" else 1):
                if i and p.r_interval_s and not self.fast:
                    time.sleep(p.r_interval_s)
                yield from self._read(p, i, "R", 0, t0)
        elif s == "DBGP":
            for _ in range(p.pot_num):
                self._pulse(p, True)
        elif s == "DBGD":
            for _ in range(p.dep_num):
                self._pulse(p, False)
        elif s == "INIT":
            pass
        else:
            raise RuntimeError(f"Arduino: ERR,unknown scenario {s}>")
