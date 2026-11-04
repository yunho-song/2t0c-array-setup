"""
psu_macro_2T0C.py
─────────────────
Rail-level control of the 2T0C FeFET array bias using 2-3 triple-output supplies.
Supported instruments: Keysight EDU36311A and Keithley 2230G (auto-detected from *IDN?;
force with psu.models: {1: "2230G"}).  Any mix of the two is fine, rails just name psu/ch.

Rail names (see firmware header):
    WWL_ON    N1 pin HIGH  -> WTR gate on   (e.g. +3 V)
    WWL_OFF   N1 pin LOW   -> WTR gate off  (e.g. -2 V, wired as negative rail)
    WBL_PROG  N2 pin HIGH  -> program voltage on WBL
    WBL_OFF   N2 pin LOW   -> erase / read-write level on WBL (often 0 V)
    VREAD     RWL read bias (below integrator virtual ground -> negative, rail wired inverted)
    VDD/VSS   board +/- supplies (integrator / level shifter)

YAML example (psu section):
    psu:
      resources:
        1: "USB0::0x2A8D::0x3302::CN00000001::INSTR"
        2: "USB0::0x2A8D::0x3302::CN00000002::INSTR"
      rails:                         # name: {psu, ch, negative, current_limit}
        WWL_ON:   {psu: 1, ch: 2, negative: false, current_limit: 0.05}
        WWL_OFF:  {psu: 1, ch: 3, negative: true,  current_limit: 0.05}
        VREAD:    {psu: 2, ch: 1, negative: true,  current_limit: 0.05}
        WBL_PROG: {psu: 2, ch: 2, negative: false, current_limit: 0.05}
        WBL_OFF:  {psu: 2, ch: 3, negative: true,  current_limit: 0.05}
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
      VREAD: -1.0
"""
from __future__ import annotations

__version__ = "0.1.0"

import time
from typing import Dict, Iterable, List, Mapping

from power_supply_EDU36311A import PowerSupplyEDU36311A, DummyPowerSupply
from power_supply_2230G import PowerSupply2230G, DummyPowerSupply2230G

# Supported instruments. psu.models in yaml / GUI may force one per PSU id; default "auto" = *IDN? detection.
MODEL_CLASSES = {
    "EDU36311A": (PowerSupplyEDU36311A, DummyPowerSupply),
    "2230G":     (PowerSupply2230G, DummyPowerSupply2230G),
}


def detect_model(rm, resource: str, timeout: int = 3000) -> str:
    """Open the VISA resource, read *IDN?, close it, and return the MODEL_CLASSES key."""
    dev = rm.open_resource(resource)
    try:
        dev.timeout = timeout
        dev.write_termination = "\n"; dev.read_termination = "\n"
        idn = dev.query("*IDN?").strip()
    finally:
        dev.close()
    return model_from_idn(idn)


def model_from_idn(idn: str) -> str:
    u = idn.upper()
    if "2230" in u or "KEITHLEY" in u:
        return "2230G"
    return "EDU36311A"

# Wiring as found on the bench (2026-10-26): 3 x EDU36311A (NI MAX aliases POWER_SUPPLY_1/2/3)
DEFAULT_RAILS = {
    "WBL_OFF":  {"psu": 1, "ch": 1, "negative": True,  "current_limit": 0.05},  # N2 off  (6 V ch, 0..-V)
    "VREAD":    {"psu": 1, "ch": 3, "negative": True,  "current_limit": 0.05},  # RWL read bias (below VINP)
    "WBL_PROG": {"psu": 2, "ch": 1, "negative": False, "current_limit": 0.05},  # N2 on   (6 V ch max)
    "WWL_OFF":  {"psu": 2, "ch": 3, "negative": True,  "current_limit": 0.05},  # N1 off
    "VDD":      {"psu": 3, "ch": 1, "negative": False, "current_limit": 0.5},   # board +supply (6 V ch)
    "VSS":      {"psu": 3, "ch": 2, "negative": True,  "current_limit": 0.5},   # board -supply
    "WWL_ON":   {"psu": 3, "ch": 3, "negative": False, "current_limit": 0.05},  # N1 on
}
# Alternative bench maps (GUI 'rail 맵' dropdown / yaml psu.rails). Rails that share a supply share its
# ground return, so putting both WBL rails on one instrument keeps the P-phase 0 V (WBL_OFF) and the
# D-phase 0 V (WBL_PROG, TOPINV/TOPRD) referenced to the same return lead.
RAIL_PRESETS = {
    "EDU bench (default)": DEFAULT_RAILS,
    "WBL on one PSU": {
        "WBL_PROG": {"psu": 1, "ch": 1, "negative": False, "current_limit": 0.05},  # PSU1 CH1 (+)
        "WBL_OFF":  {"psu": 1, "ch": 2, "negative": True,  "current_limit": 0.05},  # PSU1 CH2 (-, inverted)
        "VREAD":    {"psu": 1, "ch": 3, "negative": True,  "current_limit": 0.05},  # PSU1 CH3 (-)
        "WWL_ON":   {"psu": 2, "ch": 1, "negative": False, "current_limit": 0.05},  # PSU2 CH1 (+)
        "WWL_OFF":  {"psu": 2, "ch": 3, "negative": True,  "current_limit": 0.05},  # PSU2 CH3 (-)
        "VDD":      {"psu": 3, "ch": 1, "negative": False, "current_limit": 0.5},   # PSU3 CH1 (+)
        "VSS":      {"psu": 3, "ch": 2, "negative": True,  "current_limit": 0.5},   # PSU3 CH2 (-)
    },
}
# Optional bottom-path rails (4T0C cells only; dep_path=1). Add to yaml psu.rails if used:
#   WBL_DEP:   {psu: 2, ch: 2, negative: true}   BLBTM_OFF: {psu: 1, ch: 2, negative: false}
DEFAULT_ON_ORDER = ["VDD", "VSS", "WWL_OFF", "WBL_OFF", "BLBTM_OFF", "VREAD", "WWL_ON", "WBL_PROG", "WBL_DEP"]  # rails absent from cfg are skipped
DEFAULT_OFF_ORDER = list(reversed(DEFAULT_ON_ORDER))


class PowerSupplyMacro2T0C:
    def __init__(self, resource_manager, psu_cfg: Mapping, dummy: bool = False,
                 settle_delay: float = 0.1, final_settle: float = 0.3):
        self.rm = resource_manager
        self.resources: Dict[int, str] = {int(k): v for k, v in psu_cfg.get("resources", {}).items()}
        self.models: Dict[int, str] = {int(k): str(v) for k, v in psu_cfg.get("models", {}).items()}  # "auto" | "EDU36311A" | "2230G"
        self.model: Dict[int, str] = {}        # resolved per PSU id after connect()
        self.rails: Dict[str, dict] = dict(DEFAULT_RAILS)
        self.rails.update(psu_cfg.get("rails", {}))
        self.on_order: List[str] = list(psu_cfg.get("on_order", DEFAULT_ON_ORDER))
        self.off_order: List[str] = list(psu_cfg.get("off_order", DEFAULT_OFF_ORDER))
        self.ramp_steps: int = int(psu_cfg.get("ramp_steps", 3))
        self.dummy = dummy
        self.settle_delay = settle_delay      # after each rail write
        self.final_settle = final_settle      # once after a batch of changes (apply)
        self.psu: Dict[int, PowerSupplyEDU36311A] = {}
        self.idn: Dict[int, str] = {}
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
            model = self.models.get(pid, "auto")
            if model not in MODEL_CLASSES:
                model = "EDU36311A" if self.dummy else detect_model(self.rm, res)
            cls = MODEL_CLASSES[model][1 if self.dummy else 0]
            p = cls(self.rm, res, settle_delay=settle_delay_for_driver(self.settle_delay))
            idn = p.connect()
            self.idn[pid] = idn
            self.model[pid] = model
            print(f"PSU{pid} [{model}]: {idn}")
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

    def changed(self, voltages: Mapping[str, float], tol: float = 1e-6) -> List[str]:
        """Rails whose requested value differs from what is currently programmed."""
        return [n for n, v in voltages.items() if abs(self.current.get(n, float("nan")) - float(v)) > tol
                or n not in self.current]

    def apply(self, voltages: Mapping[str, float], verify: bool = True, only_changed: bool = True) -> List[str]:
        """Program rails in on_order (rails absent from `voltages` are left as they are).
        only_changed=True skips rails already at the requested value, so a P<->D switch that
        changes two rails takes ~0.5 s instead of re-writing all seven.  Returns the rails written."""
        order = [n for n in self.on_order if n in voltages] + \
                [n for n in voltages if n not in self.on_order]
        for name in order:
            if name not in self.rails:
                raise KeyError(f"unknown rail '{name}' (known: {list(self.rails)})")
        todo = set(self.changed(voltages)) if only_changed else set(order)
        written = []
        for name in order:
            if name not in todo:
                continue
            self._apply_rail(name, float(voltages[name]), verify=verify)
            time.sleep(self.settle_delay)
            written.append(name)
        if written:
            time.sleep(self.final_settle)
        print("PSU rails:", {k: self.current[k] for k in order}, "written:", written)
        return written

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

    def readback(self, names: Iterable[str] = None) -> Dict[str, tuple]:
        """Measured (V, I) per rail (sign restored for negative rails). `names` limits the query."""
        out = {}
        for name in (list(names) if names is not None else list(self.rails)):
            r = self.rails[name]
            v, i = self.psu[int(r["psu"])].measure(int(r["ch"]))
            out[name] = (-v if r["negative"] else v, i)
        return out


def settle_delay_for_driver(macro_delay: float) -> float:
    """Driver-level wait after a VOLT write (the macro adds its own settle_delay on top)."""
    return max(0.05, min(0.3, macro_delay))
