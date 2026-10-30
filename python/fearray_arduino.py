"""
fearray_arduino.py
──────────────────
Serial interface to FeArray_2T0C_Rev2.ino.

    ard = FeArrayArduino("COM5")
    ard.connect()
    for rec in ard.run(ScenarioParams(scenario="PD", rows=[0], cols=[0], ...)):
        print(rec)            # ReadRecord(set, phase, pulse, row, adc[5])
    ard.close()
"""
from __future__ import annotations

__version__ = "0.1.0"

import time
from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, List, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

BAUD = 115200
NUM_CH = 5

# ── current back-calculation (same constants as fefet_measure.py) ─────
CAP_F = 6000e-12          # integrator cap per column (measured 6 nF, 2026-10-29; was 3 nF in older GUI)
ADC_VREF = 3.3
ADC_FULL = 4095
ADC_DIV = 4              # default scale: firmware divides the 12-bit sample by adc_div (4 -> 0..1023, 1 -> 0..4095)
RWL_PERIOD_US = 100


def adc_to_current_uA(adc_val: float, read_time: int, adc_div: int = ADC_DIV) -> float:
    if read_time <= 0:
        return 0.0
    v_cap = (adc_val * adc_div / ADC_FULL) * ADC_VREF
    t_int = read_time * RWL_PERIOD_US * 1e-6
    return (CAP_F * v_cap / t_int) * 1e6


def mask(indices: Iterable[int]) -> int:
    m = 0
    for i in indices:
        if not 0 <= int(i) <= 4:
            raise ValueError(f"row/col index out of range: {i}")
        m |= 1 << int(i)
    return m


@dataclass
class ScenarioParams:
    scenario: str = "PD"            # P D PD DP R DBGP DBGD DBGR INIT LOOPP LOOPD LOOPR
    rows: List[int] = field(default_factory=lambda: [0])
    cols: List[int] = field(default_factory=lambda: [0])
    set_num: int = 1
    pot_num: int = 10
    dep_num: int = 10
    pot_pw: int = 5000              # us
    dep_pw: int = 5000              # us
    pre_en: int = 20                # us
    post_en: int = 20               # us
    zero_t: int = 20                # us
    read_time: int = 1000           # x100 us
    read_set_time: int = 20         # us
    read_delay: int = 20            # x10 us
    read_interval: int = 1
    read_mode: str = "N1OFF"        # N1OFF | N1ALL | N1SEL
    avg_count: int = 10
    wwl_hold: int = 0
    read_wait_ms: int = 100
    r_interval_s: float = 0.0       # R scenario
    r_points: int = 1               # R scenario
    phase_gap_ms: int = 0           # PD/DP: wait between P and D phase [ms]
    dep_path: int = 0               # 0 = TOP (WWL pulse, WBL at WBL_OFF DC)  1 = BOT (4T0C UD/UDEN path)
                                    # 2 = TOPINV (WBL pulsed: N2 rests HIGH = WBL_PROG rail (0 V in D phase),
                                    #     pulsed LOW = WBL_OFF (-V) only while WWL is open; needs split voltages)
                                    # 3 = TOPRD (WBL rests at WBL_OFF = -V like TOP; each read ties the gates (N1ALL/N1SEL)
                                    #     and raises N2 so reads happen at WBL_PROG = 0 V; lowest WTR stress)
    rwl_assist: int = 0             # 1 = during PROGRAM pulses latch the selected rows' RWL to VREAD (negative):
                                    #     FeFET Vgs = V_SN - VREAD, no extra WTR stress
    adc_div: int = 4                # ADC output scale: 4 = 0..1023 (default), 1 = full 12-bit 0..4095

    def to_command(self) -> str:
        f = [self.scenario, mask(self.rows), mask(self.cols),
             self.set_num, self.pot_num, self.dep_num,
             self.pot_pw, self.dep_pw, self.pre_en, self.post_en, self.zero_t,
             self.read_time, self.read_set_time, self.read_delay, self.read_interval,
             self.read_mode.upper(),
             self.avg_count, self.wwl_hold, self.read_wait_ms,
             self.r_interval_s, self.r_points, self.phase_gap_ms, self.dep_path, self.rwl_assist, self.adc_div]
        return ",".join(str(x) for x in f) + "|"

    def expected_reads(self) -> int:
        """Number of map reads (5 lines each) the firmware will emit."""
        ri = max(1, self.read_interval)
        if self.scenario in ("P", "D"):
            n = (self.pot_num if self.scenario == "P" else self.dep_num) // ri
            return 1 + n
        if self.scenario in ("PD", "DP"):
            return 1 + self.set_num * (self.pot_num // ri + self.dep_num // ri)
        if self.scenario == "R":
            return max(1, self.r_points)
        if self.scenario in ("DBGR", "INIT"):
            return 1
        return 0


@dataclass
class ReadRecord:
    set: int
    phase: str
    pulse: int
    row: int
    adc: List[int]
    t: float                      # time since scenario start [s]


class FeArrayArduino:
    def __init__(self, port: str, timeout: float = 1.0):
        self.port = port
        self.timeout = timeout
        self.ser = None
        self.aborted = False

    @staticmethod
    def list_ports() -> List[str]:
        if serial is None:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, reset_wait: float = 2.0) -> None:
        if serial is None:
            raise RuntimeError("pyserial not installed (pip install pyserial)")
        self.ser = serial.Serial(self.port, BAUD, timeout=self.timeout)
        time.sleep(reset_wait)          # Due native/programming port reset
        self.ser.reset_input_buffer()

    def close(self) -> None:
        if self.ser:
            self.ser.close()
            self.ser = None

    def abort(self) -> None:
        """Ask the firmware to stop the running scenario (it finishes with ABORT> EOD>)."""
        if self.ser:
            self.ser.write(b"!")
            self.ser.flush()
        self.aborted = True

    def send(self, cmd: str) -> None:
        try:
            self.ser.reset_input_buffer()
            self.ser.write(cmd.encode("ascii"))
            self.ser.flush()
        except (serial.SerialException, OSError) as e:
            # Windows: "WriteFile failed ... PermissionError(13, ...)" = the COM handle went stale
            # (board re-enumerated after a re-plug / IDE upload / sleep). Re-open once and retry.
            print(f"[serial] write failed ({e}); re-opening {self.port} and retrying once")
            try:
                self.ser.close()
            except Exception:
                pass
            self.connect()
            self.ser.reset_input_buffer()
            self.ser.write(cmd.encode("ascii"))
            self.ser.flush()

    def run(self, p: ScenarioParams, overall_timeout: Optional[float] = None,
            verbose: bool = False) -> Iterator[ReadRecord]:
        """Send the scenario and yield ReadRecords until EOD>."""
        cmd = p.to_command()
        if verbose:
            print("TX:", cmd)
        self.send(cmd)
        self.aborted = False
        t0 = time.time()
        while True:
            raw = self.ser.readline()
            if not raw:
                if overall_timeout and (time.time() - t0) > overall_timeout:
                    raise TimeoutError("no EOD from Arduino")
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if verbose:
                print("RX:", line)
            if line.startswith("EOD"):
                return
            if line.startswith("LOOP"):
                continue
            if line.startswith("ABORT"):
                self.aborted = True
                continue
            if line.startswith("ERR"):
                raise RuntimeError(f"Arduino: {line}")
            rec = self.parse_line(line, time.time() - t0)
            if rec is not None:
                yield rec

    @staticmethod
    def parse_line(line: str, t: float = 0.0) -> Optional[ReadRecord]:
        parts = line.rstrip(">").split(",")
        if len(parts) != 4 + NUM_CH:
            return None
        try:
            return ReadRecord(set=int(parts[0]), phase=parts[1].strip(), pulse=int(parts[2]),
                              row=int(parts[3]), adc=[int(x) for x in parts[4:]], t=t)
        except ValueError:
            return None
