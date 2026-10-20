#!/usr/bin/env python3
"""
fearray_gui.py — 2T0C FeFET array measurement GUI
──────────────────────────────────────────────────
Same engine as fearray_measure.py (PSU macro, Arduino protocol, ExperimentWriter),
but everything is entered in a window instead of a YAML file:

  ┌ 연결 ─────────────┐ ┌ 셀 선택 (5×5) ┐ ┌ 실시간 그래프 ────────────────┐
  │ Arduino port      │ │ click cells   │ │ selected cells vs unselected  │
  │ PSU1/PSU2 VISA    │ └───────────────┘ │ + current 5×5 map             │
  │ [SIM] checkbox    │ ┌ 전압 (6 rail) ┐ │                               │
  ├ 시나리오/타이밍 ──┤ │ base / P / D  │ │                               │
  │ ...               │ └───────────────┘ └───────────────────────────────┘
  │ [▶ 시작] [■ 중지] │  로그
  └───────────────────┘

Run:   python fearray_gui.py            (pip install -r requirements.txt first)
       python fearray_gui.py --sim      start with simulation mode ticked
       python fearray_gui.py --fonts    list Tk fonts and whether they render Hangul
       python fearray_gui.py --font "NanumGothic"   force a UI font   /   --en  force English labels
Settings can be saved/loaded as the same YAML that fearray_measure.py uses.
"""
from __future__ import annotations

__version__ = "0.1.0"

import os
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import asdict
from datetime import datetime
from tkinter import ttk, messagebox, filedialog
from typing import Dict, List, Optional

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fearray_arduino import FeArrayArduino, ScenarioParams, ReadRecord, NUM_CH   # noqa: E402
from fearray_measure import ExperimentWriter                                       # noqa: E402
from psu_macro_2T0C import DEFAULT_RAILS, DEFAULT_ON_ORDER                          # noqa: E402


# ───────────────────────── UI language / fonts ─────────────────────────
# Tk shows "\uXXXX" when no installed font has Hangul glyphs (common on conda/WSL Tk).
# We pick a Korean-capable font if one exists, otherwise fall back to English labels.
KO_FONTS = ("Malgun Gothic", "맑은 고딕", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "AppleGothic", "UnDotum", "Baekmuk Gulim")
_UI_KO = True
_EN = {}


def t(s: str) -> str:
    """translate a Korean UI string to English when Korean fonts are unavailable"""
    return s if _UI_KO else _EN.get(s, s)


def _font_renders_hangul(root, family: str) -> bool:
    """True if Tk can actually draw Hangul with this family (not as \\uXXXX escapes)."""
    import tkinter.font as tkfont
    try:
        f = tkfont.Font(root=root, family=family, size=10)
        return f.measure("한글") < 3.5 * f.measure("ab") and f.measure("한") > 0
    except Exception:
        return False


def list_fonts(root):
    import tkinter.font as tkfont
    fams = sorted(set(tkfont.families(root)))
    return [(f, _font_renders_hangul(root, f)) for f in fams]


def setup_fonts(root) -> bool:
    global _UI_KO
    import tkinter.font as tkfont
    chosen = None
    if "--en" not in sys.argv:
        # explicit --font NAME
        if "--font" in sys.argv:
            try:
                chosen = sys.argv[sys.argv.index("--font") + 1]
            except IndexError:
                chosen = None
        if chosen is None:
            fams = set(tkfont.families(root))
            cands = [f for f in KO_FONTS if f in fams]
            cands += [f for f in sorted(fams) if any(k in f for k in ("Nanum", "나눔", "Gothic", "고딕", "CJK", "Gulim", "굴림", "Batang", "바탕", "Dotum", "돋움"))]
            cands += list(KO_FONTS)            # try by name even if not listed (fontconfig may still resolve it)
            seen = set()
            for f in cands:
                if f in seen:
                    continue
                seen.add(f)
                if _font_renders_hangul(root, f):
                    chosen = f
                    break
    if chosen:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont", "TkTooltipFont", "TkFixedFont"):
            try:
                tkfont.nametofont(name).configure(family=chosen, size=9)
            except Exception:
                pass
        try:
            st = ttk.Style()
            for w in (".", "TLabel", "TButton", "TCheckbutton", "TLabelframe.Label", "TNotebook.Tab", "TEntry", "TCombobox"):
                st.configure(w, font=(chosen, 9))
        except Exception:
            pass
        _UI_KO = True
        print(f"[fearray_gui] Korean UI font: {chosen}")
    else:
        _UI_KO = False
        print("[fearray_gui] no font renders Hangul -> English labels (install fonts-nanum, or run with --font NAME; --fonts lists candidates)")
    return _UI_KO


_EN.update({
    '보드 아날로그 전원': 'board analog supply',
    'WTR gate OFF (N1 LOW, 음수)': 'WTR gate OFF (N1 LOW, negative)',
    'Program 레벨 (N2 HIGH)': 'Program level (N2 HIGH)',
    'Erase / read-write 레벨 (N2 LOW)': 'Erase / read-write level (N2 LOW)',
    'Read 바이어스': 'Read bias',
    'Set 반복': 'Sets',
    'P→D 반복 횟수 (PD/DP)': 'P→D repeat count (PD/DP)',
    'P 펄스 수': 'P pulses',
    'set당 Program 펄스': 'program pulses per set',
    'D 펄스 수': 'D pulses',
    'set당 Erase 펄스': 'erase pulses per set',
    'P 폭 [µs]': 'P width [µs]',
    'Program 펄스 폭': 'program pulse width',
    'D 폭 [µs]': 'D width [µs]',
    'Erase 펄스 폭': 'erase pulse width',
    '라인 ON 후 펄스까지': 'line ON → pulse',
    '펄스 후 대기': 'wait after pulse',
    '게이트 복귀 대기': 'gate settle',
    'Read 간격': 'Read every',
    'N 펄스마다 맵 read (0 = read 없음)': 'map read every N pulses (0 = none)',
    'RWL 펄스 수 ×100 µs': 'RWL pulses ×100 µs',
    'N1ALL/N1SEL: WWL ON 후 안정화': 'N1ALL/N1SEL: settle after WWL ON',
    'Read 전 대기 [ms]': 'Pre-read wait [ms]',
    'ADC 평균': 'ADC avg',
    'cap 1회 충전, ADC n회 평균': 'one cap charge, n ADC samples',
    'P→D 대기 [ms]': 'P→D gap [ms]',
    '펌웨어 PD에서 phase 사이': 'between phases (firmware PD)',
    'R: 횟수': 'R: points',
    'R 시나리오 맵 read 횟수': 'R scenario map reads',
    'R: 간격 [s]': 'R: interval [s]',
    'R 시나리오 read 간격': 'R scenario read interval',
    ' 연결 ': ' Connection ',
    '시뮬레이션 모드 (하드웨어 없음)': 'Simulation mode (no hardware)',
    'PSU 제어 사용 (해제 시 전압은 수동)': 'Control PSU (off = set voltages manually)',
    '연결': 'Connect',
    '미연결': 'not connected',
    ' 시나리오 / 타이밍 ': ' Scenario / timing ',
    '실험 이름': 'Name',
    '시나리오': 'Scenario',
    'Read 중 WWL': 'WWL in read',
    'P 중 WWL 유지': 'hold WWL in P',
    '저장 폴더': 'Output dir',
    '▶  측정 시작': '▶  Start',
    '■  중지': '■  Stop',
    'YAML 불러오기': 'Load YAML',
    'YAML 저장': 'Save YAML',
    ' 셀 선택 (클릭)  row=RWL/WBL, col=WWL/ADC ': ' Cell select (click)  row=RWL/WBL, col=WWL/ADC ',
    '전체': 'all',
    '해제': 'none',
    '선택: 없음': 'selected: none',
    ' PSU 전압 [V] ': ' PSU voltages [V] ',
    'P / D phase에 다른 전압 사용': 'Different voltages for P / D phase',
    '기본': 'base',
    'P/D 칸이 비면 기본값 사용. 음수 rail은 PSU 채널을 반전 배선(negative) 해야 함.': 'Empty P/D cell = base value. Negative rails need the PSU channel wired inverted (negative: true).',
    '전압 적용 (기본)': 'Apply (base)',
    '측정값 읽기': 'Read back',
    '0 V로': 'All 0 V',
    '  실시간 데이터  ': '  Live data  ',
    '  파형 미리보기 (현재 파라미터)  ': '  Waveform preview (current parameters)  ',
    'VISA 목록 실패: {e}': 'VISA list failed: {e}',
    '선택: 없음 (INIT / R / DBGR 는 선택 없이 가능)': 'selected: none (INIT / R / DBGR need no selection)',
    '{rail} 전압이 비어 있음': '{rail} voltage is empty',
    '먼저 연결하세요 (PSU 제어 사용 체크).': "Connect first (with 'Control PSU' checked).",
    '적용: ': 'applied: ',
    'PSU 적용 {v}': 'PSU applied {v}',
    '측정: ': 'measured: ',
    'readback 실패: {e}': 'readback failed: {e}',
    '모든 rail 0 V': 'all rails 0 V',
    'Arduino 포트를 선택하세요': 'Select the Arduino port',
    'PSU1/PSU2 VISA 주소를 선택하세요 (↺)': 'Select PSU1/PSU2 VISA addresses (↺)',
    '연결 실패': 'Connection failed',
    '연결 해제': 'Disconnect',
    '연결됨': 'connected',
    ' (시뮬레이션)': ' (simulation)',
    'PSU 해제 오류: {e}': 'PSU disconnect error: {e}',
    '입력 오류': 'Input error',
    '셀을 선택하세요': 'Select at least one cell',
    '중지됨': 'stopped',
    '완료': 'done',
    '오류: {e}': 'error: {e}',
    '중지 요청 (펌웨어가 현재 read를 마치면 멈춤)': 'stop requested (firmware stops after the current read)',
    '저장 오류': 'Save error',
    'YAML 불러옴: {fn}': 'YAML loaded: {fn}',
    'rows {rows} × cols {cols} → {n}셀': 'rows {rows} × cols {cols} → {n} cells',
    '\n주의: 펌웨어는 row×col 교차곱을 선택하므로 {extra} 도 함께 업데이트됨 (+ 표시)': '\nNote: firmware selects the row×col cross product, so {extra} are updated too (+)',
    '시작: {name} {cmd}': 'start: {name} {cmd}',
    '저장: {d}': 'saved: {d}',
    'YAML 저장: {fn}  (fearray_measure.py {b} 로 실행 가능)': 'YAML saved: {fn}  (run with fearray_measure.py {b})',
    'YAML에 실험 {n}개 — GUI는 첫 번째만 불러옴 (전체 실행은 fearray_measure.py)': 'YAML has {n} experiments — GUI loads only the first (use fearray_measure.py for all)',
})

RAILS = ["VDD", "WWL_ON", "WWL_OFF", "WBL_PROG", "WBL_OFF", "VREAD"]
RAIL_HELP = {  # noqa
    "VDD": "보드 아날로그 전원",
    "WWL_ON": "WTR gate ON  (N1 HIGH)",
    "WWL_OFF": "WTR gate OFF (N1 LOW, 음수)",
    "WBL_PROG": "Program 레벨 (N2 HIGH)",
    "WBL_OFF": "Erase / read-write 레벨 (N2 LOW)",
    "VREAD": "Read 바이어스",
}
DEFAULT_V = {"VDD": 5.0, "WWL_ON": 3.0, "WWL_OFF": -2.0, "WBL_PROG": 4.0, "WBL_OFF": 0.0, "VREAD": 1.0}
SCENARIOS = ["PD", "DP", "P", "D", "R", "INIT", "DBGR", "DBGP", "DBGD"]
PARAM_FIELDS = [  # (key, label, default, tooltip)
    ("set_num", "Set 반복", 1, "P→D 반복 횟수 (PD/DP)"),
    ("pot_num", "P 펄스 수", 20, "set당 Program 펄스"),
    ("dep_num", "D 펄스 수", 20, "set당 Erase 펄스"),
    ("pot_pw", "P 폭 [µs]", 5000, "Program 펄스 폭"),
    ("dep_pw", "D 폭 [µs]", 5000, "Erase 펄스 폭"),
    ("pre_en", "pre [µs]", 20, "라인 ON 후 펄스까지"),
    ("post_en", "post [µs]", 20, "펄스 후 대기"),
    ("zero_t", "zero [µs]", 20, "게이트 복귀 대기"),
    ("read_interval", "Read 간격", 1, "N 펄스마다 맵 read (0 = read 없음)"),
    ("read_time", "Read time", 1000, "RWL 펄스 수 ×100 µs"),
    ("read_delay", "Read delay", 20, "×10 µs settle"),
    ("read_set_time", "Read set [µs]", 20, "N1ALL/N1SEL: WWL ON 후 안정화"),
    ("read_wait_ms", "Read 전 대기 [ms]", 100, ""),
    ("avg_count", "ADC 평균", 10, "cap 1회 충전, ADC n회 평균"),
    ("phase_gap_ms", "P→D 대기 [ms]", 0, "펌웨어 PD에서 phase 사이"),
    ("r_points", "R: 횟수", 10, "R 시나리오 맵 read 횟수"),
    ("r_interval_s", "R: 간격 [s]", 60, "R 시나리오 read 간격"),
]


class FeArrayGUI:
    def __init__(self, root: tk.Tk, sim_default: bool = False):
        self.root = root
        setup_fonts(root)
        root.title("2T0C FeFET Array Measurement")
        root.minsize(1360, 760)

        self.ard = None
        self.psu = None
        self.worker: Optional[threading.Thread] = None
        self.stop_evt = threading.Event()
        self.q: queue.Queue = queue.Queue()
        self.writer: Optional[ExperimentWriter] = None
        self.maps: List = []          # list of 5x5 lists
        self.sel_cells = set()

        self.sim_var = tk.BooleanVar(value=sim_default)
        self._build()
        self._refresh_ports()
        self.root.after(200, self._poll)

    # ══════════════════════════ UI BUILD ══════════════════════════
    def _build(self):
        self.root.columnconfigure(2, weight=1, minsize=620)
        self.root.rowconfigure(0, weight=1)

        left = ttk.Frame(self.root, padding=8)
        left.grid(row=0, column=0, sticky="ns")
        mid = ttk.Frame(self.root, padding=8)
        mid.grid(row=0, column=1, sticky="ns")
        right = ttk.Frame(self.root, padding=8)
        right.grid(row=0, column=2, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_connection(left)
        self._build_params(left)
        self._build_buttons(left)
        self._build_cells(mid)
        self._build_voltages(mid)
        self._build_plot(right)
        self._build_log(right)

    def _build_connection(self, parent):
        f = ttk.LabelFrame(parent, text=t(" 연결 "), padding=6)
        f.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(f, text=t("시뮬레이션 모드 (하드웨어 없음)"), variable=self.sim_var,
                        command=self._sim_toggled).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(f, text="Arduino").grid(row=1, column=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(f, textvariable=self.port_var, width=22)
        self.port_cb.grid(row=1, column=1, sticky="w", padx=3)
        ttk.Button(f, text="↺", width=2, command=self._refresh_ports).grid(row=1, column=2)

        self.psu_vars = {}
        for i, pid in enumerate((1, 2)):
            ttk.Label(f, text=f"PSU{pid} VISA").grid(row=2 + i, column=0, sticky="w")
            v = tk.StringVar()
            self.psu_vars[pid] = v
            cb = ttk.Combobox(f, textvariable=v, width=22)
            cb.grid(row=2 + i, column=1, sticky="w", padx=3)
            setattr(self, f"psu_cb{pid}", cb)
        ttk.Button(f, text="↺", width=2, command=self._refresh_visa).grid(row=2, column=2)

        self.use_psu_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text=t("PSU 제어 사용 (해제 시 전압은 수동)"), variable=self.use_psu_var).grid(
            row=4, column=0, columnspan=3, sticky="w")

        self.conn_btn = ttk.Button(f, text=t("연결"), command=self._toggle_connect)
        self.conn_btn.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.conn_lbl = ttk.Label(f, text=t("미연결"), foreground="gray")
        self.conn_lbl.grid(row=6, column=0, columnspan=3, sticky="w")

    def _build_params(self, parent):
        f = ttk.LabelFrame(parent, text=t(" 시나리오 / 타이밍 "), padding=6)
        f.pack(fill="x", pady=(0, 6))
        ttk.Label(f, text=t("실험 이름")).grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value="cell_PD")
        ttk.Entry(f, textvariable=self.name_var, width=14).grid(row=0, column=1, sticky="w")
        ttk.Label(f, text=t("시나리오")).grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.scen_var = tk.StringVar(value="PD")
        ttk.Combobox(f, textvariable=self.scen_var, values=SCENARIOS, width=6, state="readonly").grid(row=0, column=3)

        self.pvars: Dict[str, tk.StringVar] = {}
        for i, (key, label, default, tip) in enumerate(PARAM_FIELDS):
            r, c = 1 + i // 2, (i % 2) * 2
            ttk.Label(f, text=t(label)).grid(row=r, column=c, sticky="w", padx=(0 if c == 0 else 8, 0))
            v = tk.StringVar(value=str(default))
            self.pvars[key] = v
            e = ttk.Entry(f, textvariable=v, width=8)
            e.grid(row=r, column=c + 1, sticky="w", pady=1)
            if tip:
                e.bind("<Enter>", lambda _e, tip_=tip: self._status(t(tip_)))

        r = 1 + (len(PARAM_FIELDS) + 1) // 2
        ttk.Label(f, text=t("Read 중 WWL")).grid(row=r, column=0, sticky="w")
        self.rmode_var = tk.StringVar(value="N1OFF")
        ttk.Combobox(f, textvariable=self.rmode_var, values=["N1OFF", "N1ALL", "N1SEL"], width=7,
                     state="readonly").grid(row=r, column=1, sticky="w")
        self.hold_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text=t("P 중 WWL 유지"), variable=self.hold_var).grid(row=r, column=2, columnspan=2, sticky="w", padx=(8, 0))

        ttk.Label(f, text=t("저장 폴더")).grid(row=r + 1, column=0, sticky="w")
        self.out_var = tk.StringVar(value=os.path.join(HERE, "data"))
        ttk.Entry(f, textvariable=self.out_var, width=22).grid(row=r + 1, column=1, columnspan=2, sticky="w")
        ttk.Button(f, text="…", width=2, command=self._pick_dir).grid(row=r + 1, column=3, sticky="w")

    def _build_buttons(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill="x")
        self.start_btn = ttk.Button(f, text=t("▶  측정 시작"), command=self._start, state="disabled")
        self.start_btn.pack(fill="x", pady=2)
        self.stop_btn = ttk.Button(f, text=t("■  중지"), command=self._stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)
        g = ttk.Frame(f)
        g.pack(fill="x", pady=2)
        ttk.Button(g, text=t("YAML 불러오기"), command=self._load_yaml).pack(side="left", expand=True, fill="x")
        ttk.Button(g, text=t("YAML 저장"), command=self._save_yaml).pack(side="left", expand=True, fill="x")
        self.status_lbl = ttk.Label(parent, text="", foreground="gray", wraplength=300)
        self.status_lbl.pack(fill="x", pady=(4, 0))

    def _build_cells(self, parent):
        f = ttk.LabelFrame(parent, text=t(" 셀 선택 (클릭)  row=RWL/WBL, col=WWL/ADC "), padding=6)
        f.pack(fill="x", pady=(0, 6))
        self.cell_btns = {}
        self.cell_state = {}
        ttk.Label(f, text="").grid(row=0, column=0)
        for c in range(5):
            ttk.Label(f, text=f"col {c}").grid(row=0, column=c + 1)
        for r in range(5):
            ttk.Label(f, text=f"row {r}").grid(row=r + 1, column=0, padx=(0, 4))
            for c in range(5):
                b = tk.Button(f, text="", width=3, height=1, relief="raised", bg="#e8e8e8",
                              command=lambda r=r, c=c: self._toggle_cell(r, c))
                b.grid(row=r + 1, column=c + 1, padx=1, pady=1)
                self.cell_btns[(r, c)] = b
                self.cell_state[(r, c)] = False
        g = ttk.Frame(f)
        g.grid(row=6, column=0, columnspan=6, sticky="ew", pady=(4, 0))
        ttk.Button(g, text=t("전체"), width=5, command=lambda: self._set_cells(all_=True)).pack(side="left")
        ttk.Button(g, text=t("해제"), width=6, command=lambda: self._set_cells(all_=False)).pack(side="left", padx=2)
        ttk.Button(g, text="(0,0)", width=6, command=lambda: self._set_cells(cells={(0, 0)})).pack(side="left", padx=2)
        ttk.Button(g, text="row 0", width=6, command=lambda: self._set_cells(cells={(0, c) for c in range(5)})).pack(side="left", padx=2)
        ttk.Button(g, text="col 0", width=6, command=lambda: self._set_cells(cells={(r, 0) for r in range(5)})).pack(side="left", padx=2)
        self.sel_lbl = ttk.Label(f, text=t("선택: 없음"), wraplength=330, foreground="#0F6E8C")
        self.sel_lbl.grid(row=7, column=0, columnspan=6, sticky="w", pady=(4, 0))
        self._update_sel_label()

    def _build_voltages(self, parent):
        f = ttk.LabelFrame(parent, text=t(" PSU 전압 [V] "), padding=6)
        f.pack(fill="x")
        self.split_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text=t("P / D phase에 다른 전압 사용"), variable=self.split_var,
                        command=self._split_toggled).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(f, text="rail").grid(row=1, column=0, sticky="w")
        ttk.Label(f, text=t("기본")).grid(row=1, column=1)
        self.vP_hdr = ttk.Label(f, text="P phase"); self.vP_hdr.grid(row=1, column=2)
        self.vD_hdr = ttk.Label(f, text="D phase"); self.vD_hdr.grid(row=1, column=3)
        self.vvars: Dict[str, Dict[str, tk.StringVar]] = {"base": {}, "P": {}, "D": {}}
        self.v_entries: Dict[str, List[ttk.Entry]] = {"P": [], "D": []}
        for i, rail in enumerate(RAILS):
            ttk.Label(f, text=rail).grid(row=2 + i, column=0, sticky="w")
            for j, ph in enumerate(("base", "P", "D")):
                v = tk.StringVar(value=str(DEFAULT_V[rail]) if ph == "base" else "")
                self.vvars[ph][rail] = v
                e = ttk.Entry(f, textvariable=v, width=7)
                e.grid(row=2 + i, column=1 + j, padx=2, pady=1)
                e.bind("<Enter>", lambda _e, h=RAIL_HELP[rail]: self._status(t(h)))
                if ph != "base":
                    self.v_entries[ph].append(e)
        ttk.Label(f, text=t("P/D 칸이 비면 기본값 사용. 음수 rail은 PSU 채널을 반전 배선(negative) 해야 함."),
                  foreground="gray", wraplength=330).grid(row=8, column=0, columnspan=4, sticky="w", pady=(4, 0))
        g = ttk.Frame(f)
        g.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Button(g, text=t("전압 적용 (기본)"), command=lambda: self._apply_v("base")).pack(side="left", expand=True, fill="x")
        ttk.Button(g, text=t("측정값 읽기"), command=self._readback).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(g, text=t("0 V로"), command=self._zero_v).pack(side="left", expand=True, fill="x")
        self.rail_lbl = ttk.Label(f, text="", foreground="gray", wraplength=330, justify="left")
        self.rail_lbl.grid(row=10, column=0, columnspan=4, sticky="w")
        self._split_toggled()

    def _build_plot(self, parent):
        import matplotlib
        matplotlib.use("TkAgg")
        from matplotlib import font_manager
        avail = {f.name for f in font_manager.fontManager.ttflist}
        for fam in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"):
            if fam in avail:
                matplotlib.rcParams["font.family"] = fam
                break
        matplotlib.rcParams["axes.unicode_minus"] = False
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        self.nb = ttk.Notebook(parent)
        self.nb.grid(row=0, column=0, sticky="nsew")
        tab1 = ttk.Frame(self.nb); tab2 = ttk.Frame(self.nb)
        self.nb.add(tab1, text=t("  실시간 데이터  "))
        self.nb.add(tab2, text=t("  파형 미리보기 (현재 파라미터)  "))

        # ── live data ──
        self.fig = Figure(figsize=(7.5, 5.0))
        gs = self.fig.add_gridspec(1, 2, width_ratios=[2.2, 1], wspace=0.3, left=0.1, right=0.97, top=0.92, bottom=0.12)
        self.ax = self.fig.add_subplot(gs[0, 0])
        self.ax_map = self.fig.add_subplot(gs[0, 1])
        self.ax.set_xlabel("read index"); self.ax.set_ylabel("ADC [counts]"); self.ax.grid(alpha=0.3)
        self.ax_map.set_title("current map (ADC)")
        self.im = None
        self.canvas = FigureCanvasTkAgg(self.fig, master=tab1)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.draw()

        # ── waveform preview ──
        self.wfig = Figure(figsize=(7.5, 5.0))
        self.wcanvas = FigureCanvasTkAgg(self.wfig, master=tab2)
        self.wcanvas.get_tk_widget().pack(fill="both", expand=True)
        self._wave_job = None
        for v in list(self.pvars.values()) + [self.rmode_var, self.scen_var] + list(self.vvars["base"].values()):
            v.trace_add("write", lambda *_: self._schedule_wave())
        self.hold_var.trace_add("write", lambda *_: self._schedule_wave())
        self._draw_waveforms()

    def _schedule_wave(self):
        if self._wave_job:
            self.root.after_cancel(self._wave_job)
        self._wave_job = self.root.after(300, self._draw_waveforms)

    @staticmethod
    def _wlen(us: float) -> float:
        """display width: compressed so 20 µs and 5000 µs are both visible"""
        import math
        return 0.6 + math.log10(1 + max(0.0, us))

    def _draw_waveforms(self):
        self._wave_job = None
        try:
            g = lambda k: float(self.pvars[k].get())
            pre, post, zero = g("pre_en"), g("post_en"), g("zero_t")
            ppw, dpw = g("pot_pw"), g("dep_pw")
            rt, rd, rst, rw = g("read_time"), g("read_delay"), g("read_set_time"), g("read_wait_ms")
            v = {k: float(self.vvars["base"][k].get() or 0) for k in RAILS}
        except ValueError:
            return
        hold = self.hold_var.get()
        rmode = self.rmode_var.get()
        L = {k: t(lab) for k, lab, *_ in PARAM_FIELDS}       # GUI field label for each firmware key
        fig = self.wfig
        fig.clear()
        axes = fig.subplots(3, 1, gridspec_kw={"hspace": 1.15, "left": 0.1, "right": 0.98, "top": 0.93, "bottom": 0.07})
        C = {"N1": "#0F6E8C", "N2": "#E8912D", "SN": "#2A9D8F", "CON": "#0F6E8C", "CR": "#E8912D", "RWL": "#2A9D8F", "Vcap": "#5B4B8A"}
        NAME = {"N1": "N1 (WWL)", "N2": "N2 (WBL)", "SN": "SN", "CON": "CON", "CR": "CR", "RWL": "RWL", "Vcap": "Vcap"}
        MAP_W = (f"N1=WWL: LOW→WWL_OFF {v['WWL_OFF']:g} V / HIGH→WWL_ON {v['WWL_ON']:g} V     "
                 f"N2=WBL: LOW→WBL_OFF {v['WBL_OFF']:g} V / HIGH→WBL_PROG {v['WBL_PROG']:g} V     SN = FeFET gate")
        MAP_R = (f"CON LOW = cell↔integrator    CR HIGH = cap open    RWL HIGH = VREAD {v['VREAD']:g} V    Vcap → ADC")

        def draw(ax, title, tracks, segs_by_track, seg_labels):
            """tracks: list of names (top→bottom); segs: {name: [(len, level)]}; seg_labels: [(len, text)]"""
            n = len(tracks)
            xs = [0.0]
            for L, _ in seg_labels:
                xs.append(xs[-1] + L)
            total = xs[-1]
            for ti, name in enumerate(tracks):
                base = (n - 1 - ti) * 1.6
                x = 0.0
                segs = segs_by_track[name]
                px, py = [], []
                for (L, lv) in segs:
                    if isinstance(lv, tuple):            # ramp (from, to)
                        px += [x, x + L]; py += [base + lv[0], base + lv[1]]
                    else:
                        px += [x, x + L]; py += [base + lv, base + lv]
                    x += L
                ax.plot(px, py, color=C[name], lw=2)
                ax.text(-0.02 * total, base + 0.5, NAME.get(name, name), ha="right", va="center", fontsize=8.5, fontweight="bold", color=C[name])
            for i, (L, text) in enumerate(seg_labels):
                if text:
                    ax.axvspan(xs[i], xs[i + 1], color="k", alpha=0.03 if i % 2 else 0.0, lw=0)
                    ax.text((xs[i] + xs[i + 1]) / 2, n * 1.6 - 0.25 + (0.0 if i % 2 == 0 else 0.55), text, ha="center", va="bottom", fontsize=7.5, color="#333")
                    ax.axvline(xs[i + 1], color="#999", lw=0.5, ls=":")
            ax.set_xlim(-0.14 * total, total); ax.set_ylim(-0.3, n * 1.6 + 1.2)
            ax.set_xticks([]); ax.set_yticks([])
            for sp in ax.spines.values():
                sp.set_visible(False)
            ax.set_title(title, fontsize=9, fontweight="bold", loc="left", pad=18)

        W = self._wlen
        # ── Program ──
        wl = [W(pre), W(ppw), W(zero), W(post)]
        gap = 0.4
        labs = [(gap, ""), (wl[0], f"{L['pre_en']}\n= {pre:g}"), (wl[1], f"{L['pot_pw']}\n= {ppw:g}"), (wl[2], f"{L['zero_t']}\n= {zero:g}"), (wl[3], f"{L['post_en']}\n= {post:g}"), (gap, "")]
        n1 = [(gap, 1 if hold else 0), (wl[0], 1), (wl[1], 1), (wl[2], 1), (wl[3], 1 if hold else 0), (gap, 1 if hold else 0)]
        n2 = [(gap, 0), (wl[0], 0), (wl[1], 1), (wl[2], 0), (wl[3], 0), (gap, 0)]
        sn = [(gap, 0.3 if hold else 0.15), (wl[0], 0.3), (wl[1], 1), (wl[2], 0.3), (wl[3], 0.3 if hold else 0.15), (gap, 0.3 if hold else 0.15)]
        draw(axes[0], f"PROGRAM pulse ×1  (scenario P / PD)   segment labels = GUI fields [µs]" + (f"    [{t('P 중 WWL 유지')}]" if hold else ""),
             ["N1", "N2", "SN"], {"N1": n1, "N2": n2, "SN": sn}, labs)
        axes[0].text(0.0, -0.04, MAP_W, transform=axes[0].transAxes, fontsize=7.5, va="top", ha="left", color="#333")
        # ── Erase ──
        wl = [W(pre), W(dpw), W(post), W(zero)]
        labs = [(gap, "N2 OFF"), (wl[0], f"{L['pre_en']}\n= {pre:g}"), (wl[1], f"{L['dep_pw']}\n= {dpw:g}"), (wl[2], f"{L['post_en']}\n= {post:g}"), (wl[3], f"{L['zero_t']}\n= {zero:g}"), (gap, "")]
        n2 = [(gap, 0), (wl[0], 0), (wl[1], 0), (wl[2], 0), (wl[3], 0), (gap, 0)]
        n1 = [(gap, 0), (wl[0], 0), (wl[1], 1), (wl[2], 0), (wl[3], 0), (gap, 0)]
        sn = [(gap, 0.6), (wl[0], 0.6), (wl[1], 0.05), (wl[2], 0.15), (wl[3], 0.15), (gap, 0.15)]
        draw(axes[1], f"ERASE pulse ×1  (scenario D / PD)   N2 stays LOW → WBL = WBL_OFF {v['WBL_OFF']:g} V, N1 pulse pulls SN to it",
             ["N2", "N1", "SN"], {"N1": n1, "N2": n2, "SN": sn}, labs)
        axes[1].text(0.0, -0.04, MAP_W, transform=axes[1].transAxes, fontsize=7.5, va="top", ha="left", color="#333")
        # ── Read (one row) ──
        tint = rt * 100.0; tdel = rd * 10.0
        wl = [W(rw * 1000), W(rst) if rmode != "N1OFF" else 0.3, W(tint), W(tdel), 0.9, W(3000)]
        labs = [(wl[0], f"{L['read_wait_ms']}\n= {rw:g}"), (wl[1], f"{L['read_set_time']}\n= {rst:g}" if rmode != "N1OFF" else ""), (wl[2], f"{L['read_time']} = {rt:g}\n×100 µs = {tint/1000:g} ms"), (wl[3], f"{L['read_delay']}={rd:g}\n×10µs={tdel:g}µs"), (1.3, f"ADC\n×{int(g('avg_count'))}"), (wl[5], "CR clear\n3 ms")]
        con = [(wl[0], 1), (wl[1], 0), (wl[2], 0), (wl[3], 0), (1.3, 1), (wl[5], 1)]
        cr = [(wl[0], 0), (wl[1], 1), (wl[2], 1), (wl[3], 1), (1.3, 1), (wl[5], 0)]
        rwl = [(wl[0], 0), (wl[1], 0), (wl[2], 1), (wl[3], 0), (1.3, 0), (wl[5], 0)]
        vc = [(wl[0], 0), (wl[1], 0), (wl[2], (0, 0.8)), (wl[3], 0.8), (1.3, 0.8), (wl[5], (0.8, 0))]
        draw(axes[2], f"READ one row (5 cols at once)   {t('Read 중 WWL')} = {rmode}   1 ADC count ≈ {3000e-12*4/4095*3.3/(tint*1e-6)*1e9:.2f} nA",
             ["CON", "CR", "RWL", "Vcap"], {"CON": con, "CR": cr, "RWL": rwl, "Vcap": vc}, labs)
        axes[2].text(0.0, -0.04, MAP_R, transform=axes[2].transAxes, fontsize=7.5, va="top", ha="left", color="#333")
        axes[2].text(0.0, -0.3, "Vcap slope ∝ I_RBL (FeFET state).   x-axis is log-compressed (not to scale).  Segment labels = GUI field names.",
                     transform=axes[2].transAxes, fontsize=7.5, color="gray")
        self.wcanvas.draw_idle()

    def _build_log(self, parent):
        f = ttk.Frame(parent)
        f.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        f.columnconfigure(0, weight=1)
        self.prog = ttk.Progressbar(f, mode="determinate")
        self.prog.grid(row=0, column=0, sticky="ew")
        self.log = tk.Text(f, height=8, font=("Consolas", 9), state="disabled", bg="#f7f7f7")
        self.log.grid(row=1, column=0, sticky="ew", pady=(4, 0))

    # ══════════════════════════ helpers ══════════════════════════
    def _status(self, msg):
        self.status_lbl.config(text=msg)

    def _logln(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)

    def _refresh_ports(self):
        ports = FeArrayArduino.list_ports()
        self.port_cb["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _refresh_visa(self):
        try:
            import pyvisa
            res = list(pyvisa.ResourceManager().list_resources())
        except Exception as e:
            res = []
            self._logln(t("VISA 목록 실패: {e}").format(e=e))
        for pid in (1, 2):
            getattr(self, f"psu_cb{pid}")["values"] = res
        if len(res) >= 2 and not self.psu_vars[1].get():
            self.psu_vars[1].set(res[0]); self.psu_vars[2].set(res[1])

    def _sim_toggled(self):
        pass

    def _split_toggled(self):
        st = "normal" if self.split_var.get() else "disabled"
        for ph in ("P", "D"):
            for e in self.v_entries[ph]:
                e.config(state=st)

    # ── cells ──
    def _toggle_cell(self, r, c):
        self.cell_state[(r, c)] = not self.cell_state[(r, c)]
        self._update_sel_label()

    def _set_cells(self, all_=None, cells=None):
        for k in self.cell_state:
            self.cell_state[k] = bool(all_) if cells is None else (k in cells)
        self._update_sel_label()

    def _selection(self):
        clicked = {k for k, v in self.cell_state.items() if v}
        rows = sorted({r for r, _ in clicked})
        cols = sorted({c for _, c in clicked})
        actual = {(r, c) for r in rows for c in cols}
        return clicked, rows, cols, actual

    def _update_sel_label(self):
        clicked, rows, cols, actual = self._selection()
        for k, b in self.cell_btns.items():
            if k in clicked:
                b.config(bg="#E8912D", text="●")
            elif k in actual:
                b.config(bg="#f7d8b0", text="+")   # cross-product extra
            else:
                b.config(bg="#e8e8e8", text="")
        self.sel_cells = actual
        if not clicked:
            self.sel_lbl.config(text=t("선택: 없음 (INIT / R / DBGR 는 선택 없이 가능)"))
        else:
            extra = actual - clicked
            msg = t("rows {rows} × cols {cols} → {n}셀").format(rows=rows, cols=cols, n=len(actual))
            if extra:
                msg += t("\n주의: 펌웨어는 row×col 교차곱을 선택하므로 {extra} 도 함께 업데이트됨 (+ 표시)").format(extra=sorted(extra))
            self.sel_lbl.config(text=msg)

    # ── voltages ──
    def _volts(self, phase="base") -> Dict[str, float]:
        out = {}
        for rail in RAILS:
            s = self.vvars[phase][rail].get().strip()
            if s == "":
                if phase == "base":
                    raise ValueError(t("{rail} 전압이 비어 있음").format(rail=rail))
                continue
            out[rail] = float(s)
        return out

    def _psu_cfg(self):
        return {"resources": {1: self.psu_vars[1].get(), 2: self.psu_vars[2].get()}, "rails": DEFAULT_RAILS}

    def _apply_v(self, phase):
        if self.psu is None:
            messagebox.showinfo("PSU", t("먼저 연결하세요 (PSU 제어 사용 체크).")); return
        try:
            v = dict(self._volts("base"), **(self._volts(phase) if phase != "base" else {}))
            self.psu.apply(v)
            self.rail_lbl.config(text=t("적용: ") + ", ".join(f"{k}={v[k]:g}" for k in v))
            self._logln(t("PSU 적용 {v}").format(v=v))
        except Exception as e:
            messagebox.showerror("PSU", str(e))

    def _readback(self):
        if self.psu is None:
            return
        try:
            rb = self.psu.readback()
            self.rail_lbl.config(text=t("측정: ") + ", ".join(f"{k}={v:.3f}V/{i*1e3:.1f}mA" for k, (v, i) in rb.items()))
        except Exception as e:
            self._logln(t("readback 실패: {e}").format(e=e))

    def _zero_v(self):
        if self.psu is not None:
            self.psu.ramp_all_down()
            self.rail_lbl.config(text=t("모든 rail 0 V"))

    # ── connection ──
    def _toggle_connect(self):
        if self.ard is not None:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        sim = self.sim_var.get()
        try:
            if sim:
                from fearray_sim import FeArraySim
                self.ard = FeArraySim(fast=False)
                self.ard.connect()
            else:
                port = self.port_var.get()
                if not port:
                    raise RuntimeError(t("Arduino 포트를 선택하세요"))
                self.ard = FeArrayArduino(port)
                self.ard.connect()
            if self.use_psu_var.get():
                from psu_macro_2T0C import PowerSupplyMacro2T0C
                rm = None
                if not sim:
                    import pyvisa
                    rm = pyvisa.ResourceManager()
                    if not (self.psu_vars[1].get() and self.psu_vars[2].get()):
                        raise RuntimeError(t("PSU1/PSU2 VISA 주소를 선택하세요 (↺)"))
                self.psu = PowerSupplyMacro2T0C(rm, self._psu_cfg(), dummy=sim)
                self.psu.connect()
        except Exception as e:
            self.ard = None; self.psu = None
            messagebox.showerror(t("연결 실패"), str(e)); return
        self.conn_btn.config(text=t("연결 해제"))
        self.conn_lbl.config(text=("SIM" if sim else self.port_var.get()) + (" + PSU" if self.psu else ""), foreground="green")
        self.start_btn.config(state="normal")
        self._logln(t("연결됨") + (t(" (시뮬레이션)") if sim else ""))

    def _disconnect(self):
        self._stop()
        if self.psu is not None:
            try:
                self.psu.disconnect(ramp_down=True)
            except Exception as e:
                self._logln(t("PSU 해제 오류: {e}").format(e=e))
            self.psu = None
        if self.ard is not None:
            self.ard.close(); self.ard = None
        self.conn_btn.config(text=t("연결"))
        self.conn_lbl.config(text=t("미연결"), foreground="gray")
        self.start_btn.config(state="disabled")
        self._logln(t("연결 해제"))

    # ── params ──
    def _params(self) -> ScenarioParams:
        _, rows, cols, _ = self._selection()
        kw = {}
        for key, *_ in PARAM_FIELDS:
            s = self.pvars[key].get().strip()
            kw[key] = float(s) if key == "r_interval_s" else int(float(s))
        return ScenarioParams(scenario=self.scen_var.get(), rows=rows, cols=cols,
                              read_mode=self.rmode_var.get(), wwl_hold=int(self.hold_var.get()), **kw)

    # ── run ──
    def _start(self):
        if self.ard is None:
            return
        try:
            p = self._params()
            vbase = self._volts("base")
            split = self.split_var.get() and p.scenario in ("PD", "DP")
            vP = dict(vbase, **self._volts("P")) if split else vbase
            vD = dict(vbase, **self._volts("D")) if split else vbase
        except Exception as e:
            messagebox.showerror(t("입력 오류"), str(e)); return
        if p.scenario in ("P", "D", "PD", "DP", "DBGP", "DBGD") and not p.rows:
            messagebox.showerror(t("입력 오류"), t("셀을 선택하세요")); return

        name = self.name_var.get().strip() or "exp"
        self.writer = ExperimentWriter(self.out_var.get(), name, p, self.psu.rails if self.psu else {},
                                       {"base": vbase, "P": vP, "D": vD} if split else vbase)
        self.maps = []
        self.ax.clear(); self.ax.grid(alpha=0.3); self.ax.set_xlabel("read index"); self.ax.set_ylabel("ADC [counts]")
        self.ax.set_title(f"{p.scenario}  rows {p.rows} × cols {p.cols}")
        self.canvas.draw_idle()
        ri = max(1, p.read_interval)
        self.n_expected = (1 + p.set_num * (p.pot_num // ri + p.dep_num // ri)) if split else p.expected_reads()
        self.prog.config(maximum=max(1, self.n_expected), value=0)

        self.stop_evt.clear()
        self.start_btn.config(state="disabled"); self.stop_btn.config(state="normal")
        self._logln(t("시작: {name} {cmd}").format(name=name, cmd=p.to_command()))
        self.worker = threading.Thread(target=self._run_worker, args=(p, vbase, vP, vD, split), daemon=True)
        self.worker.start()

    def _run_worker(self, p: ScenarioParams, vbase, vP, vD, split):
        def rails(v):
            if self.psu is not None:
                self.psu.apply(v); time.sleep(0.5)
                self.q.put(("log", f"PSU {v}"))
        try:
            if split:
                order = ["P", "D"] if p.scenario == "PD" else ["D", "P"]
                first = True
                for i in range(1, p.set_num + 1):
                    for ph in order:
                        if self.stop_evt.is_set():
                            break
                        rails(vP if ph == "P" else vD)
                        q = ScenarioParams(**{**asdict(p), "scenario": ph, "set_num": 1})
                        for rec in self.ard.run(q):
                            if not first and rec.phase == "I":
                                continue
                            rec.set = i
                            self.q.put(("rec", rec))
                        first = False
                        if getattr(self.ard, "aborted", False):
                            break
            else:
                rails(vbase)
                for rec in self.ard.run(p):
                    self.q.put(("rec", rec))
            self.q.put(("done", t("중지됨") if self.stop_evt.is_set() else t("완료")))
        except Exception as e:
            self.q.put(("done", t("오류: {e}").format(e=e)))

    def _stop(self):
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            try:
                self.ard.abort()
            except Exception:
                pass
            self._logln(t("중지 요청 (펌웨어가 현재 read를 마치면 멈춤)"))

    # ── UI poll ──
    def _poll(self):
        redraw = False
        while not self.q.empty():
            kind, payload = self.q.get_nowait()
            if kind == "rec":
                rec: ReadRecord = payload
                self.writer.add(rec)
                if rec.row == 0:
                    self.maps.append([[None] * 5 for _ in range(5)])
                if self.maps:
                    self.maps[-1][rec.row] = list(rec.adc)
                if rec.row == 4:
                    redraw = True
                    self.prog.config(value=len(self.maps))
                    self._status(f"read {len(self.maps)}/{self.n_expected}  set {rec.set} {rec.phase}{rec.pulse}  t={rec.t:.1f}s")
            elif kind == "log":
                self._logln(payload)
            elif kind == "done":
                self._logln(payload)
                if self.writer:
                    self.writer.close()
                    self._logln(t("저장: {d}").format(d=self.writer.dir))
                    self.writer = None
                self.start_btn.config(state="normal"); self.stop_btn.config(state="disabled")
        if redraw:
            self._redraw()
        self.root.after(200, self._poll)

    def _redraw(self):
        import numpy as np
        full = [m for m in self.maps if all(r[0] is not None for r in m)]
        if not full:
            return
        arr = np.array(full, dtype=float)           # n x 5 x 5
        x = np.arange(len(arr))
        self.ax.clear(); self.ax.grid(alpha=0.3)
        self.ax.set_xlabel("read index"); self.ax.set_ylabel("ADC [counts]")
        for r in range(5):
            for c in range(5):
                if (r, c) in self.sel_cells:
                    self.ax.plot(x, arr[:, r, c], "o-", ms=3, lw=1.4, label=f"({r},{c})")
                else:
                    self.ax.plot(x, arr[:, r, c], "-", color="0.65", lw=0.8)
        if self.sel_cells:
            self.ax.legend(fontsize=7, loc="best")
        self.ax_map.clear()
        im = self.ax_map.imshow(arr[-1], cmap="viridis")
        for r in range(5):
            for c in range(5):
                self.ax_map.text(c, r, f"{arr[-1, r, c]:.0f}", ha="center", va="center", fontsize=7, color="w")
                if (r, c) in self.sel_cells:
                    import matplotlib.patches as mp
                    self.ax_map.add_patch(mp.Rectangle((c - 0.5, r - 0.5), 1, 1, fill=False, ec="red", lw=2))
        self.ax_map.set_title(f"map #{len(arr)}"); self.ax_map.set_xticks(range(5)); self.ax_map.set_yticks(range(5))
        self.canvas.draw_idle()

    # ── YAML in/out ──
    def _to_yaml_dict(self) -> Dict:
        p = self._params()
        exp = {"name": self.name_var.get(), "scenario": p.scenario, "rows": p.rows, "cols": p.cols}
        d = asdict(p)
        for k in ("scenario", "rows", "cols"):
            d.pop(k)
        if self.split_var.get():
            exp["phase_voltages"] = {"P": self._volts("P"), "D": self._volts("D")}
        return {
            "output_dir": self.out_var.get(),
            "arduino": {"port": self.port_var.get()},
            "psu": self._psu_cfg(),
            "voltages": self._volts("base"),
            "defaults": d,
            "experiments": [exp],
        }

    def _save_yaml(self):
        try:
            cfg = self._to_yaml_dict()
        except Exception as e:
            messagebox.showerror(t("저장 오류"), str(e)); return
        fn = filedialog.asksaveasfilename(defaultextension=".yaml", initialdir=HERE,
                                          initialfile=f"{self.name_var.get()}.yaml")
        if fn:
            with open(fn, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
            self._logln(t("YAML 저장: {fn}  (fearray_measure.py {b} 로 실행 가능)").format(fn=fn, b=os.path.basename(fn)))

    def _load_yaml(self):
        fn = filedialog.askopenfilename(filetypes=[("YAML", "*.yaml *.yml")], initialdir=HERE)
        if not fn:
            return
        with open(fn, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.out_var.set(cfg.get("output_dir", self.out_var.get()))
        self.port_var.set(cfg.get("arduino", {}).get("port", self.port_var.get()))
        for pid in (1, 2):
            r = cfg.get("psu", {}).get("resources", {}).get(pid) or cfg.get("psu", {}).get("resources", {}).get(str(pid))
            if r:
                self.psu_vars[pid].set(r)
        for rail, v in cfg.get("voltages", {}).items():
            if rail in self.vvars["base"]:
                self.vvars["base"][rail].set(str(v))
        d = dict(cfg.get("defaults", {}))
        exps = cfg.get("experiments", [])
        if exps:
            e = exps[0]
            d.update({k: v for k, v in e.items() if k in self.pvars or k in ("read_mode", "wwl_hold")})
            self.name_var.set(e.get("name", "exp"))
            self.scen_var.set(e.get("scenario", "PD"))
            cells = {(r, c) for r in e.get("rows", []) for c in e.get("cols", [])}
            self._set_cells(cells=cells)
            pv = e.get("phase_voltages")
            self.split_var.set(bool(pv))
            for ph in ("P", "D"):
                for rail in RAILS:
                    self.vvars[ph][rail].set(str((pv or {}).get(ph, {}).get(rail, "")) if pv else "")
            self._split_toggled()
            if len(exps) > 1:
                self._logln(t("YAML에 실험 {n}개 — GUI는 첫 번째만 불러옴 (전체 실행은 fearray_measure.py)").format(n=len(exps)))
        for k, v in d.items():
            if k in self.pvars:
                self.pvars[k].set(str(v))
        if "read_mode" in d:
            self.rmode_var.set(str(d["read_mode"]).upper())
        if "wwl_hold" in d:
            self.hold_var.set(bool(d["wwl_hold"]))
        self._logln(t("YAML 불러옴: {fn}").format(fn=fn))


def main():
    sim = "--sim" in sys.argv
    root = tk.Tk()
    if "--fonts" in sys.argv:
        for fam, ok in list_fonts(root):
            print(("HANGUL OK  " if ok else "           ") + fam)
        return
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    FeArrayGUI(root, sim_default=sim)
    root.mainloop()


if __name__ == "__main__":
    main()
