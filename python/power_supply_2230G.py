"""
power_supply_2230G.py
─────────────────────
Minimal pyvisa driver for the Keithley 2230G (2230G-30-1 / 2230-30-1) triple-output
power supply, with the SAME interface as power_supply_EDU36311A.PowerSupplyEDU36311A:
    connect / disconnect / enable_outputs / disable_outputs / zero_voltages /
    set_voltages / set_compliance_currents / read_setpoints / measure / check_error
so PowerSupplyMacro2T0C can use either instrument (model is auto-detected from *IDN?).

Channels : CH1 = 30 V / 1.5 A,  CH2 = 30 V / 1.5 A,  CH3 = 6 V / 5 A   (2230G-30-1)
           (2230G-30-3: CH1/CH2 30 V / 3 A).  Adjust CH_MAX_* below if your unit differs.
           CH1/CH2 share a common "-" terminal on the 2230 series; CH3 is isolated.
           -> a negative rail (channel "+" wired to GND) is fine on any channel as long as
              rails that share a "-" terminal are either both negative or both positive.
              Check the rear/front terminal layout before wiring the bench rail map.

SCPI used (Keithley 2230 series, channel selected with INST:SEL):
    *IDN?   *CLS   SYST:ERR?
    INST:SEL CHn            select channel (cached, sent only on change)
    SOUR:VOLT <v>           SOUR:CURR <i>          SOUR:VOLT?   SOUR:CURR?
    OUTP ON|OFF             (selected channel)      -- same usage as the lab's src.configs driver
    MEAS:VOLT?  MEAS:CURR?  (selected channel)
"""
from __future__ import annotations

__version__ = "0.1.0"

import time
from typing import Dict, Iterable, Optional, Tuple

try:
    import pyvisa
except ImportError:  # allow import for dry-run / simulation without VISA
    pyvisa = None

CH_MAX_V = {1: 30.0, 2: 30.0, 3: 6.0}
CH_MAX_I = {1: 1.5, 2: 1.5, 3: 5.0}


class PowerSupply2230G:
    def __init__(self, resource_manager, resource: str, timeout: int = 3000,
                 settle_delay: float = 0.3, verify_tol: float = 0.02):
        """
        resource : VISA resource string or NI MAX alias (e.g. 'USB0::0x05E6::0x2230::...::INSTR')
        """
        self.rm = resource_manager
        self.resource = resource
        self.timeout = timeout
        self.device = None
        self._settle_delay = settle_delay
        self._verify_tol = verify_tol
        self._sel: Optional[int] = None          # cached INST:SEL channel

    # ── connection ────────────────────────────────────────────────────
    def connect(self) -> str:
        if pyvisa is None:
            raise RuntimeError("pyvisa not installed (pip install pyvisa pyvisa-py)")
        self.device = self.rm.open_resource(self.resource)
        self.device.timeout = self.timeout
        self.device.write_termination = "\n"
        self.device.read_termination = "\n"
        self.device.write("*CLS")
        idn = self.device.query("*IDN?").strip()
        if "2230" not in idn:
            print(f"[WARN] unexpected instrument: {idn}")
        self._sel = None
        return idn

    def disconnect(self) -> None:
        if self.device is not None:
            try:
                self.device.close()
            finally:
                self.device = None
                self._sel = None

    def _w(self, cmd: str) -> None:
        self.device.write(cmd)

    def _q(self, cmd: str) -> str:
        return self.device.query(cmd).strip()

    def check_error(self) -> str:
        return self._q("SYST:ERR?")

    def _select(self, ch: int) -> None:
        if ch not in (1, 2, 3):
            raise ValueError("channel must be 1, 2 or 3")
        if self._sel != ch:
            self._w(f"INST:SEL CH{ch}")
            self._sel = ch

    # ── outputs ───────────────────────────────────────────────────────
    def enable_outputs(self, channels: Iterable[int] = (1, 2, 3)) -> None:
        for ch in channels:
            self._select(ch)
            self._w("OUTP ON")

    def disable_outputs(self, channels: Iterable[int] = (1, 2, 3)) -> None:
        for ch in channels:
            self._select(ch)
            self._w("OUTP OFF")

    def zero_voltages(self, channels: Iterable[int] = (1, 2, 3)) -> None:
        for ch in channels:
            self._select(ch)
            self._w("SOUR:VOLT 0")
        time.sleep(self._settle_delay)

    # ── setpoints ─────────────────────────────────────────────────────
    def set_voltage(self, channel: int, volts: float) -> None:
        v = abs(float(volts))
        if v > CH_MAX_V[channel]:
            raise ValueError(f"CH{channel}: {v} V exceeds {CH_MAX_V[channel]} V")
        self._select(channel)
        self._w(f"SOUR:VOLT {v:.4f}")

    def set_voltages(self, channel_voltage_map: Dict[int, float], *, verify: bool = True) -> None:
        for ch, v in channel_voltage_map.items():
            self.set_voltage(ch, v)
        time.sleep(self._settle_delay)
        if verify:
            for ch, v in channel_voltage_map.items():
                self._select(ch)
                got = float(self._q("SOUR:VOLT?"))
                if abs(got - abs(v)) > self._verify_tol:
                    raise RuntimeError(f"CH{ch} setpoint verify failed: want {abs(v)} got {got}")

    def set_compliance_currents(self, channel_current_map: Dict[int, float], *, verify: bool = True) -> None:
        for ch, i in channel_current_map.items():
            i = float(i)
            if i > CH_MAX_I[ch]:
                raise ValueError(f"CH{ch}: {i} A exceeds {CH_MAX_I[ch]} A")
            self._select(ch)
            self._w(f"SOUR:CURR {i:.4f}")
        time.sleep(self._settle_delay)
        if verify:
            for ch, i in channel_current_map.items():
                self._select(ch)
                got = float(self._q("SOUR:CURR?"))
                if abs(got - i) > 1e-3:
                    raise RuntimeError(f"CH{ch} current verify failed: want {i} got {got}")

    def read_setpoints(self, channel: int) -> Tuple[float, float]:
        self._select(channel)
        v = float(self._q("SOUR:VOLT?"))
        i = float(self._q("SOUR:CURR?"))
        return v, i

    def measure(self, channel: int) -> Tuple[float, float]:
        self._select(channel)
        v = float(self._q("MEAS:VOLT?"))
        i = float(self._q("MEAS:CURR?"))
        return v, i


class DummyPowerSupply2230G(PowerSupply2230G):
    """Drop-in replacement that only prints (for dry runs without hardware)."""

    def connect(self) -> str:
        self.device = object()
        self._set = {1: 0.0, 2: 0.0, 3: 0.0}
        self._cur = {1: 0.0, 2: 0.0, 3: 0.0}
        self._sel = None
        print(f"[DUMMY 2230G {self.resource}] connected")
        return "DUMMY,2230G-30-1"

    def disconnect(self) -> None:
        self.device = None

    def _w(self, cmd: str) -> None:
        print(f"[DUMMY 2230G {self.resource}] {cmd}")
        if cmd.startswith("SOUR:VOLT "):
            self._set[self._sel] = float(cmd.split()[1])
        if cmd.startswith("SOUR:CURR "):
            self._cur[self._sel] = float(cmd.split()[1])

    def _q(self, cmd: str) -> str:
        if cmd.startswith("SOUR:VOLT?") or cmd.startswith("MEAS:VOLT?"):
            return str(self._set[self._sel])
        if cmd.startswith("SOUR:CURR?") or cmd.startswith("MEAS:CURR?"):
            return str(self._cur[self._sel])
        return "0,\"No error\""
