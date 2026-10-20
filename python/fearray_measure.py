#!/usr/bin/env python3
"""
fearray_measure.py
──────────────────
2T0C FeFET array measurement orchestrator.

    PSU (2x EDU36311A) ──set rails──┐
                                    ├──► experiment list (YAML) ──► CSV + PNG per experiment
    Arduino Due (FeArray_2T0C_Rev2) ─┘

Usage
    python fearray_measure.py config.yaml                # real hardware
    python fearray_measure.py config.yaml --sim          # no hardware: simulated array + dummy PSU
    python fearray_measure.py config.yaml --no-psu       # Arduino only, rails set manually
    python fearray_measure.py config.yaml --only cell00_PD

Output (per experiment, in output_dir/<timestamp>_<name>/)
    data.csv      long format: read_idx,t_s,set,phase,pulse,row,col,selected,adc,I_uA
    map_*.csv     5x5 ADC maps: initial, final, delta
    meta.json     scenario params + PSU rails + constants
    plot.png      selected vs unselected cells + delta heatmap

Requires:  pip install pyserial pyvisa pyvisa-py pyyaml numpy matplotlib
"""
from __future__ import annotations

__version__ = "0.1.0"

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, fields
from datetime import datetime
from typing import Dict, List

import yaml

from fearray_arduino import (FeArrayArduino, ScenarioParams, ReadRecord,
                             adc_to_current_uA, NUM_CH)

HERE = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────── experiment expansion ────────────────────
def build_params(base: Dict, exp: Dict) -> ScenarioParams:
    allowed = {f.name for f in fields(ScenarioParams)}
    merged = {k: v for k, v in base.items() if k in allowed}
    merged.update({k: v for k, v in exp.items() if k in allowed})
    return ScenarioParams(**merged)


def expand_experiments(cfg: Dict) -> List[Dict]:
    """Expand `sweep` entries into individual experiments."""
    base = cfg.get("defaults", {})
    out = []
    for exp in cfg.get("experiments", []):
        sweep = exp.get("sweep")
        if not sweep:
            out.append(exp)
            continue
        key, values = sweep["key"], sweep["values"]
        for v in values:
            e = dict(exp)
            e.pop("sweep")
            e["name"] = f"{exp['name']}_{key}{v}"
            if key in cfg.get("voltages", {}) or key in cfg.get("psu", {}).get("rails", {}):
                e["voltages"] = dict(exp.get("voltages", {}), **{key: v})
            else:
                e[key] = v
            out.append(e)
    return out


# ─────────────────────────── storage ─────────────────────────────────
class ExperimentWriter:
    def __init__(self, out_dir: str, name: str, p: ScenarioParams, rails: Dict, voltages: Dict):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(out_dir, f"{ts}_{name}")
        os.makedirs(self.dir, exist_ok=True)
        self.p = p
        self.sel = {(r, c) for r in p.rows for c in p.cols}
        self.rows: List[ReadRecord] = []
        self.read_idx = -1
        self._f = open(os.path.join(self.dir, "data.csv"), "w", newline="")
        self._w = csv.writer(self._f)
        self._w.writerow(["read_idx", "t_s", "set", "phase", "pulse", "row", "col",
                          "selected", "adc", "I_uA"])
        meta = {"name": name, "params": asdict(p), "voltages": voltages, "psu_rails": rails,
                "constants": {"CAP_pF": 3000, "RWL_PERIOD_US": 100, "ADC_VREF": 3.3},
                "started": ts}
        with open(os.path.join(self.dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def add(self, rec: ReadRecord) -> None:
        if rec.row == 0:
            self.read_idx += 1
        self.rows.append(rec)
        for c, adc in enumerate(rec.adc):
            self._w.writerow([self.read_idx, f"{rec.t:.3f}", rec.set, rec.phase, rec.pulse,
                              rec.row, c, int((rec.row, c) in self.sel), adc,
                              f"{adc_to_current_uA(adc, self.p.read_time):.5f}"])
        self._f.flush()

    def maps(self):
        """Return list of 5x5 maps (one per read point) and labels."""
        import numpy as np
        n = self.read_idx + 1
        maps = np.full((n, 5, 5), np.nan)
        labels = [None] * n
        idx = -1
        for rec in self.rows:
            if rec.row == 0:
                idx += 1
                labels[idx] = (rec.set, rec.phase, rec.pulse)
            maps[idx, rec.row, :] = rec.adc
        return maps, labels

    def close(self) -> None:
        self._f.close()
        try:
            self._save_maps_and_plot()
        except Exception as e:  # plotting must never kill a measurement
            print(f"[WARN] plot/map failed: {e}")

    def _save_maps_and_plot(self) -> None:
        import numpy as np
        maps, labels = self.maps()
        if len(maps) == 0:
            return
        np.savetxt(os.path.join(self.dir, "map_initial.csv"), maps[0], fmt="%d", delimiter=",")
        np.savetxt(os.path.join(self.dir, "map_final.csv"), maps[-1], fmt="%d", delimiter=",")
        np.savetxt(os.path.join(self.dir, "map_delta.csv"), maps[-1] - maps[0], fmt="%.1f", delimiter=",")

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        x = np.arange(len(maps))
        fig, axs = plt.subplots(1, 3, figsize=(15, 4.2), gridspec_kw={"width_ratios": [2.2, 1, 1]})
        ax = axs[0]
        for r in range(5):
            for c in range(5):
                y = maps[:, r, c]
                if (r, c) in self.sel:
                    ax.plot(x, y, "o-", ms=3, lw=1.5, label=f"sel ({r},{c})")
                else:
                    ax.plot(x, y, "-", color="0.6", lw=0.8, alpha=0.7)
        ax.plot([], [], "-", color="0.6", label="unselected (24)")
        # phase shading
        start = 0
        for i in range(1, len(labels) + 1):
            if i == len(labels) or labels[i][1] != labels[start][1] or labels[i][0] != labels[start][0]:
                ph = labels[start][1]
                if ph in ("P", "D"):
                    ax.axvspan(start - 0.5, i - 0.5, color=("#ffe8d6" if ph == "P" else "#dbe9ff"), alpha=0.5, lw=0)
                start = i
        ax.set_xlabel("read index (orange = Program, blue = Erase)")
        ax.set_ylabel("ADC [counts]")
        ax.set_title(f"{self.p.scenario}: rows {self.p.rows} cols {self.p.cols}")
        ax.legend(fontsize=7, loc="best")
        ax.grid(alpha=0.3)

        for ax, m, title in ((axs[1], maps[-1], "final ADC"), (axs[2], maps[-1] - maps[0], "final − initial")):
            im = ax.imshow(m, cmap="viridis" if title.startswith("final ADC") else "coolwarm",
                           vmin=None if title.startswith("final ADC") else -np.nanmax(np.abs(m) + 1e-9),
                           vmax=None if title.startswith("final ADC") else np.nanmax(np.abs(m) + 1e-9))
            for r in range(5):
                for c in range(5):
                    ax.text(c, r, f"{m[r, c]:.0f}", ha="center", va="center", fontsize=7,
                            color="w" if title.startswith("final ADC") else "k")
                    if (r, c) in self.sel:
                        ax.add_patch(plt.Rectangle((c - 0.5, r - 0.5), 1, 1, fill=False, ec="red", lw=2))
            ax.set_xlabel("col (ADC ch)"); ax.set_ylabel("row (RWL)"); ax.set_title(title)
            ax.set_xticks(range(5)); ax.set_yticks(range(5))
            fig.colorbar(im, ax=ax, fraction=0.046)
        fig.tight_layout()
        fig.savefig(os.path.join(self.dir, "plot.png"), dpi=130)
        plt.close(fig)

        # disturb summary
        d = maps[-1] - maps[0]
        sel = np.array([d[r, c] for (r, c) in self.sel])
        uns = np.array([d[r, c] for r in range(5) for c in range(5) if (r, c) not in self.sel])
        print(f"  Δ selected  : mean {sel.mean():+.1f}  (n={len(sel)})")
        if len(uns):
            print(f"  Δ unselected: mean {uns.mean():+.1f}  max|Δ| {np.abs(uns).max():.1f}  (n={len(uns)})")


# ─────────────────────────── main ────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--sim", action="store_true", help="simulate Arduino and PSU")
    ap.add_argument("--no-psu", action="store_true", help="do not touch the power supplies")
    ap.add_argument("--only", nargs="*", help="run only experiments with these names")
    ap.add_argument("--port", help="override Arduino port")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    out_dir = cfg.get("output_dir", "data")
    os.makedirs(out_dir, exist_ok=True)
    experiments = expand_experiments(cfg)
    if args.only:
        experiments = [e for e in experiments if e["name"] in args.only]
    if not experiments:
        print("no experiments to run"); return 1

    # ── Arduino ──
    if args.sim:
        from fearray_sim import FeArraySim
        ard = FeArraySim()
    else:
        port = args.port or cfg.get("arduino", {}).get("port")
        if not port:
            print("Arduino port not given. Available:", FeArrayArduino.list_ports()); return 1
        ard = FeArrayArduino(port, timeout=cfg.get("arduino", {}).get("timeout", 1.0))
    ard.connect()

    # ── PSU ──
    psu = None
    if not args.no_psu:
        from psu_macro_2T0C import PowerSupplyMacro2T0C
        rm = None
        if not args.sim:
            import pyvisa
            rm = pyvisa.ResourceManager()
        psu = PowerSupplyMacro2T0C(rm, cfg.get("psu", {}), dummy=args.sim)
        psu.connect()

    base_voltages = dict(cfg.get("voltages", {}))
    try:
        for exp in experiments:
            name = exp["name"]
            p = build_params(cfg.get("defaults", {}), exp)
            volts = dict(base_voltages, **exp.get("voltages", {}))
            print(f"\n=== {name}: {p.scenario} rows={p.rows} cols={p.cols} "
                  f"P={p.pot_num}x{p.pot_pw}us D={p.dep_num}x{p.dep_pw}us sets={p.set_num} ===")
            if psu is not None:
                psu.apply(volts)
                time.sleep(float(cfg.get("psu", {}).get("settle_after_apply_s", 1.0)))
            elif volts:
                print("  (PSU not controlled) expected rails:", volts)

            w = ExperimentWriter(out_dir, name, p, psu.rails if psu else {}, volts)
            n_expected = p.expected_reads()
            t0 = time.time()
            sel_vals: List[int] = []
            try:
                for rec in ard.run(p, overall_timeout=exp.get("timeout_s"), verbose=args.verbose):
                    w.add(rec)
                    if rec.row == 0:
                        sel_vals = []
                    if rec.row in p.rows:
                        sel_vals += [rec.adc[c] for c in p.cols]
                    if rec.row == 4:
                        print(f"\r  read {w.read_idx + 1}/{n_expected}  set {rec.set} {rec.phase}{rec.pulse:>4}"
                              f"  t={rec.t:6.1f}s  selected={sel_vals}", end="", flush=True)
            finally:
                print(f"\n  done in {time.time() - t0:.1f}s -> {w.dir}")
                w.close()
            time.sleep(float(exp.get("pause_after_s", 0)))
    finally:
        ard.close()
        if psu is not None:
            psu.disconnect(ramp_down=cfg.get("psu", {}).get("ramp_down_at_end", True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
