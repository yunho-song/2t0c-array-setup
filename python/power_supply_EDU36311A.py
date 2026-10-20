"""
power_supply_EDU36311A.py
─────────────────────────
Minimal pyvisa driver for the Keysight EDU36311A triple-output power supply.

Interface is kept compatible with the one used by FPGA_6T1C_room_31_305.py
(PowerSupplyMacro): connect / disconnect / enable_outputs / disable_outputs /
zero_voltages / set_voltages / set_compliance_currents / read_setpoints.

Channels : CH1 = 6 V / 5 A,  CH2 = 30 V / 1 A,  CH3 = 30 V / 1 A  (all isolated).
A negative rail is made by wiring the channel's "+" terminal to the common ground
and taking the "-" terminal as the output; the driver always programs |V|.

SCPI used (Keysight E36300 series):
    *IDN?   *RST   *CLS   SYST:ERR?
    VOLT <v>,(@n)   CURR <i>,(@n)   VOLT? (@n)   CURR? (@n)
    OUTP ON|OFF,(@n)   MEAS:VOLT? (@n)   MEAS:CURR? (@n)
"""
from __future__ import annotations

__version__ = "0.1.0"

import time
from typing import Dict, Iterable, Tuple

try:
    import pyvisa
except ImportError:  # allow import for dry-run / simulation without VISA
    pyvisa = None

CH_MAX_V = {1: 6.0, 2: 30.0, 3: 30.0}
CH_MAX_I = {1: 5.0, 2: 1.0, 3: 1.0}


class PowerSupplyEDU36311A:
    def __init__(self, resource_manager, resource: str, timeout: int = 3000,
                 settle_delay: float = 0.3, verify_tol: float = 0.02):
        """
        resource : VISA resource string (e.g. 'USB0::0x2A8D::0x3302::CN12345678::INSTR')
        """
        self.rm = resource_manager
        self.resource = resource
        self.timeout = timeout
        self.device = None
        self._settle_delay = settle_delay
        self._verify_tol = verify_tol

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
        if "36311" not in idn and "E363" not in idn:
            print(f"[WARN] unexpected instrument: {idn}")
        return idn

    def disconnect(self) -> None:
        if self.device is not None:
            try:
                self.device.close()
            finally:
                self.device = None

    def _w(self, cmd: str) -> None:
        self.device.write(cmd)

    def _q(self, cmd: str) -> str:
        return self.device.query(cmd).strip()

    def check_error(self) -> str:
        return self._q("SYST:ERR?")

    # ── outputs ───────────────────────────────────────────────────────
    def enable_outputs(self, channels: Iterable[int] = (1, 2, 3)) -> None:
        for ch in channels:
            self._w(f"OUTP ON,(@{ch})")

    def disable_outputs(self, channels: Iterable[int] = (1, 2, 3)) -> None:
        for ch in channels:
            self._w(f"OUTP OFF,(@{ch})")

    def zero_voltages(self, channels: Iterable[int] = (1, 2, 3)) -> None:
        for ch in channels:
            self._w(f"VOLT 0,(@{ch})")
        time.sleep(self._settle_delay)

    # ── setpoints ─────────────────────────────────────────────────────
    def set_voltage(self, channel: int, volts: float) -> None:
        v = abs(float(volts))
        if v > CH_MAX_V[channel]:
            raise ValueError(f"CH{channel}: {v} V exceeds {CH_MAX_V[channel]} V")
        self._w(f"VOLT {v:.4f},(@{channel})")

    def set_voltages(self, channel_voltage_map: Dict[int, float], *, verify: bool = True) -> None:
        for ch, v in channel_voltage_map.items():
            self.set_voltage(ch, v)
        time.sleep(self._settle_delay)
        if verify:
            for ch, v in channel_voltage_map.items():
                got = float(self._q(f"VOLT? (@{ch})"))
                if abs(got - abs(v)) > self._verify_tol:
                    raise RuntimeError(f"CH{ch} setpoint verify failed: want {abs(v)} got {got}")

    def set_compliance_currents(self, channel_current_map: Dict[int, float], *, verify: bool = True) -> None:
        for ch, i in channel_current_map.items():
            i = float(i)
            if i > CH_MAX_I[ch]:
                raise ValueError(f"CH{ch}: {i} A exceeds {CH_MAX_I[ch]} A")
            self._w(f"CURR {i:.4f},(@{ch})")
        time.sleep(self._settle_delay)
        if verify:
            for ch, i in channel_current_map.items():
                got = float(self._q(f"CURR? (@{ch})"))
                if abs(got - i) > 1e-3:
                    raise RuntimeError(f"CH{ch} current verify failed: want {i} got {got}")

    def read_setpoints(self, channel: int) -> Tuple[float, float]:
        v = float(self._q(f"VOLT? (@{channel})"))
        i = float(self._q(f"CURR? (@{channel})"))
        return v, i

    def measure(self, channel: int) -> Tuple[float, float]:
        v = float(self._q(f"MEAS:VOLT? (@{channel})"))
        i = float(self._q(f"MEAS:CURR? (@{channel})"))
        return v, i


class DummyPowerSupply(PowerSupplyEDU36311A):
    """Drop-in replacement that only prints (for dry runs without hardware)."""

    def connect(self) -> str:
        self.device = object()
        self._set = {1: 0.0, 2: 0.0, 3: 0.0}
        self._cur = {1: 0.0, 2: 0.0, 3: 0.0}
        print(f"[DUMMY PSU {self.resource}] connected")
        return "DUMMY,EDU36311A"

    def disconnect(self) -> None:
        self.device = None

    def _w(self, cmd: str) -> None:
        print(f"[DUMMY PSU {self.resource}] {cmd}")
        if cmd.startswith("VOLT "):
            v, ch = cmd[5:].split(",(@")
            self._set[int(ch.rstrip(")"))] = float(v)
        if cmd.startswith("CURR "):
            i, ch = cmd[5:].split(",(@")
            self._cur[int(ch.rstrip(")"))] = float(i)

    def _q(self, cmd: str) -> str:
        ch = int(cmd.split("(@")[1].rstrip(")")) if "(@" in cmd else 1
        if cmd.startswith("VOLT?") or cmd.startswith("MEAS:VOLT?"):
            return str(self._set[ch])
        if cmd.startswith("CURR?") or cmd.startswith("MEAS:CURR?"):
            return str(self._cur[ch])
        return "0,\"No error\""
