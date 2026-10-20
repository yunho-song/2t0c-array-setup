"""
psu_macro_2T0C.py
─────────────────
Rail-level control of the 2T0C FeFET array bias using two EDU36311A supplies
(6 channels).  A third supply for board power is NOT touched here.

Rail names (see firmware header):
    WWL_ON    N1 pin HIGH  -> WTR gate on   (e.g. +3 V)
    WWL_OFF   N1 pin LOW   -> WTR gate off  (e.g. -2 V, wired as negative rail)
    WBL_PROG  N2 pin HIGH  -> program voltage on WBL
    WBL_OFF   N2 pin LOW   -> erase / read-write level on WBL (often 0 V)
    VREAD     RWL / read drain bias
    VDD       analog front-end (integrator / level shifter) supply

YAML example (psu section):
    psu:
      resources:
        1: "USB0::0x2A8D::0x3302::CN00000001::INSTR"
        2: "USB0::0x2A8D::0x3302::CN00000002::INSTR"
      rails:                         # name: {psu, ch, negative, current_limit}
        WWL_ON:   {psu: 1, ch: 2, negative: false, current_limit: 0.05}
        WWL_OFF:  {psu: 1, ch: 3, negative: true,  current_limit: 0.05}
        WBL_PROG: {psu: 2, ch: 2, negative: false, current_limit: 0.05}
        WBL_OFF:  {psu: 2, ch: 1, negative: false, current_limit: 0.05}
        VREAD:    {psu: 2, ch: 3, negative: false, current_limit: 0.05}
        VDD:      {psu: 1, ch: 1, negative: false, current_limit: 0.50}
      on_order:  [VDD, WWL_OFF, WBL_OFF, VREAD, WWL_ON, WBL_PROG]
      off_order: [WBL_PROG, WWL_ON, VREAD, WBL_OFF, WWL_OFF, VDD]
      ramp_steps: 3
    voltages:
      VDD: 5.0
      WWL_ON: 3.0
      WWL_OFF: -2.0
      WBL_PROG: 4.0
      WBL_OFF: 0.0
      VREAD: 1.0
"""
from __future__ import annotations

__version__ = "0.1.0"

import time
from typing import Dict, Iterable, List, Mapping

from power_supply_EDU36311A import PowerSupplyEDU36311A, DummyPowerSupply

DEFAULT_RAILS = {
    "VDD":      {"psu": 1, "ch": 1, "negative": False, "current_limit": 0.5},
    "WWL_ON":   {"psu": 1, "ch": 2, "negative": False, "current_limit": 0.05},
    "WWL_OFF":  {"psu": 1, "ch": 3, "negative": True,  "current_limit": 0.05},
    "WBL_OFF":  {"psu": 2, "ch": 1, "negative": False, "current_limit": 0.05},
    "WBL_PROG": {"psu": 2, "ch": 2, "negative": False, "current_limit": 0.05},
    "VREAD":    {"psu": 2, "ch": 3, "negative": False, "current_limit": 0.05},
}
DEFAULT_ON_ORDER = ["VDD", "WWL_OFF", "WBL_OFF", "VREAD", "WWL_ON", "WBL_PROG"]
DEFAULT_OFF_ORDER = list(reversed(DEFAULT_ON_ORDER))


class PowerSupplyMacro2T0C:
    def __init__(self, resource_manager, psu_cfg: Mapping, dummy: bool = False,
                 settle_delay: float = 0.5):
        self.rm = resource_manager
        self.resources: Dict[int, str] = {int(k): v for k, v in psu_cfg.get("resources", {}).items()}
        self.rails: Dict[str, dict] = dict(DEFAULT_RAILS)
        self.rails.update(psu_cfg.get("rails", {}))
        self.on_order: List[str] = list(psu_cfg.get("on_order", DEFAULT_ON_ORDER))
        self.off_order: List[str] = list(psu_cfg.get("off_order", DEFAULT_OFF_ORDER))
        self.ramp_steps: int = int(psu_cfg.get("ramp_steps", 3))
        self.dummy = dummy
        self.settle_delay = settle_delay
        self.psu: Dict[int, PowerSupplyEDU36311A] = {}
        self.current: Dict[str, float] = {}

        self._check_rails()

    def _check_rails(self) -> None:
        used = {}
        for name, r in self.rails.items():
            key = (int(r["psu"]), int(r["ch"]))
            if key in used:
                raise ValueError(f"rail {name} and {used[key]} share PSU{key[0]} CH{key[1]}")
            used[key] = name

    # ── lifecycle ─────────────────────────────────────────────────────
    def connect(self) -> None:
        ids = sorted({int(r["psu"]) for r in self.rails.values()})
        for pid in ids:
            res = self.resources.get(pid, f"PSU{pid}")
            cls = DummyPowerSupply if self.dummy else PowerSupplyEDU36311A
            p = cls(self.rm, res)
            idn = p.connect()
            print(f"PSU{pid}: {idn}")
            self.psu[pid] = p
        # all channels to 0 V, current limits, outputs ON (at 0 V)
        for name, r in self.rails.items():
            p = self.psu[int(r["psu"])]
            p.set_voltages({int(r["ch"]): 0.0}, verify=False)
            p.set_compliance_currents({int(r["ch"]): float(r.get("current_limit", 0.05))}, verify=False)
            self.current[name] = 0.0
        for p in self.psu.values():
            p.enable_outputs()

    def disconnect(self, ramp_down: bool = True) -> None:
        if ramp_down and self.psu:
            self.ramp_all_down()
        for p in self.psu.values():
            try:
                p.disable_outputs()
                p.disconnect()
            except Exception:
                pass
        self.psu.clear()

    # ── voltage programming ───────────────────────────────────────────
    def _apply_rail(self, name: str, volts: float, verify: bool = True) -> None:
        r = self.rails[name]
        p = self.psu[int(r["psu"])]
        mag = abs(volts)
        if r["negative"] and volts > 0:
            raise ValueError(f"{name} is wired as a NEGATIVE rail but {volts} V requested")
        if (not r["negative"]) and volts < 0:
            raise ValueError(f"{name} is wired as a POSITIVE rail but {volts} V requested "
                             f"(set rails.{name}.negative: true and swap the wiring)")
        p.set_voltages({int(r["ch"]): mag}, verify=verify)
        self.current[name] = volts

    def apply(self, voltages: Mapping[str, float], verify: bool = True) -> None:
        """Program all rails in on_order (rails absent from `voltages` are left as they are)."""
        order = [n for n in self.on_order if n in voltages] + \
                [n for n in voltages if n not in self.on_order]
        for name in order:
            if name not in self.rails:
                raise KeyError(f"unknown rail '{name}' (known: {list(self.rails)})")
            self._apply_rail(name, float(voltages[name]), verify=verify)
            time.sleep(self.settle_delay)
        print("PSU rails:", {k: self.current[k] for k in order})

    def set_rail(self, name: str, volts: float) -> None:
        self._apply_rail(name, volts)
        time.sleep(self.settle_delay)

    def ramp_all_down(self) -> None:
        steps = max(1, self.ramp_steps)
        for name in self.off_order:
            if name not in self.rails:
                continue
            v0 = self.current.get(name, 0.0)
            for k in range(1, steps + 1):
                self._apply_rail(name, v0 * (1 - k / steps), verify=False)
                time.sleep(0.2)
        print("PSU rails ramped to 0 V")

    def readback(self) -> Dict[str, tuple]:
        out = {}
        for name, r in self.rails.items():
            v, i = self.psu[int(r["psu"])].measure(int(r["ch"]))
            out[name] = (-v if r["negative"] else v, i)
        return out
