# 2T0C FeFET 5×5 어레이 측정 셋업 (Rev2)

저장소: https://github.com/yunho-song/2t0c-array-setup  ·  설명서: `FeArray_2T0C_manual.pptx`  ·  오실로스코프 검증: `SCOPE_CHECK.md`

identical pulse로 선택 셀만 Program(P)/Erase(D) 하고, 매 read마다 **25셀 전체 맵**을 기록해
비선택 셀의 disturb 여부를 함께 확인하는 셋업.

```bash
git clone https://github.com/yunho-song/2t0c-array-setup.git
cd 2t0c-array-setup/python
pip install -r requirements.txt
cp config_example.yaml config.yaml      # 포트·VISA 주소·전압 수정 (config.yaml은 git 제외)
python fearray_measure.py config.yaml --sim
```

```
PSU 2대 (EDU36311A, 6 rail) ──pyvisa──┐
                                      ├── fearray_measure.py (config.yaml) ──► data/<ts>_<name>/{data.csv, map_*.csv, plot.png, meta.json}
Arduino Due (FeArray_2T0C_Rev2.ino) ──serial─┘
```

## 폴더

| 경로 | 내용 |
|---|---|
| `firmware/FeArray_2T0C_Rev2/FeArray_2T0C_Rev2.ino` | 어레이 펌웨어 (Due). 4T0C 비트마스크 구조 + 2T0C_Ferro Rev1.0 핀맵/FE·FEE 펄스 |
| `python/fearray_gui.py` | **GUI** (연결·전압·셀 선택·타이밍 입력, 실시간 그래프, 중지, YAML 저장/불러오기) |
| `python/fearray_measure.py` | 배치 실행 스크립트 (YAML → PSU 설정 → 실험 목록 순차 실행 → 저장/플롯) |
| `python/fearray_arduino.py` | 시리얼 프로토콜, `ScenarioParams`, 전류 역산 |
| `python/psu_macro_2T0C.py` | 6 rail(WWL_ON/OFF, WBL_PROG/OFF, VREAD, VDD) ↔ PSU/채널 매핑, on/off 순서, 램프다운 |
| `python/power_supply_EDU36311A.py` | EDU36311A pyvisa 드라이버 (+ `DummyPowerSupply`) |
| `python/fearray_sim.py` | 하드웨어 없이 돌려보는 시뮬레이터 (`--sim`) |
| `python/config_example.yaml` | 설정 예시 (포트, PSU 배선, 전압, 실험 목록) |

## 설치 / 실행

```bash
pip install pyserial pyvisa pyvisa-py pyyaml numpy matplotlib
python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"   # PSU VISA 주소 확인 → yaml에 기입

python fearray_gui.py                                # GUI (--sim 으로 시뮬레이션 체크 상태로 시작)
python fearray_measure.py config.yaml --sim          # 하드웨어 없이 전체 흐름 확인 (배치)
python fearray_measure.py config.yaml --no-psu       # PSU는 수동, Arduino만
python fearray_measure.py config.yaml                # 실측
python fearray_measure.py config.yaml --only cell00_PD
```

## 펌웨어 셀 좌표 규약 (Rev1.0 그대로, PCB와 대조 필요)

| 역할 | 선택 인덱스 | 핀 |
|---|---|---|
| WWL (N1, WTR gate) | **col** | 51,50,49,48,47 (PC12–16) |
| WBL 레벨 (N2, V_prog/V_off) | **row** | 39,38,37,36,35 (PC7–3) |
| RWL | row | 25,26,27,28,29 (PD0,1,2,3,6) |
| RBL → 적분기 → ADC | col | A0–A4 |

row_mask × col_mask의 **교차곱**이 선택됨 (row [0,1] × col [0,1] = 4셀).
단일 셀/한 row/한 col 단위로 실험을 나누는 것이 disturb 해석에 깔끔함.

## 시리얼 프로토콜

명령 (22 필드, `|` 종료):
```
SCEN,row_mask,col_mask,set_num,pot_num,dep_num,pot_pw,dep_pw,pre,post,zero,read_time,read_set_time,read_delay,read_interval,read_mode,avg,wwl_hold,read_wait_ms,r_interval_s,r_points,0|
```
| 시나리오 | 동작 | 용도 |
|---|---|---|
| `INIT` | 라인 OFF 후 맵 1회 | 기준 맵 |
| `P` / `D` | 초기 맵 → program/erase 펄스 N회, read_interval마다 맵 | P 또는 D 응답 |
| `PD` / `DP` | 초기 맵 → (P→D) 또는 (D→P) × set_num | identical pulse P/D + disturb |
| `PD` + `phase_voltages` | Python이 set마다 PSU를 P용/D용으로 바꿈 (WBL_OFF 0 V / −3 V) | 2T0C 권장 운용 (depression 전압은 WBL_OFF로) |
| `dep_path` 1 (BOT) | UD 선택 + UDEN(S2) 펄스 (하단 경로) | 4T0C 셀 전용, 2T0C 미사용 |
| `R` | 맵을 r_points회, r_interval_s 간격 반복 | retention |
| `DBGR` / `DBGP` / `DBGD` | 맵 1회 / 펄스만 | 수동 확인 |
| `LOOPP` / `LOOPD` / `LOOPR` | 펄스 1회·row read를 `!`까지 무한 반복 | 오실로스코프 |
실행 중 호스트가 `!` 1바이트를 보내면 현재 read/펄스 루프를 빠져나와 `ABORT>` `EOD>`로 끝남 (GUI 중지 버튼)

응답 (read 포인트당 5줄):
```
set,phase,pulse,row,ADC0,ADC1,ADC2,ADC3,ADC4>      phase = I | P | D | R
EOD>
```

## 펄스 정의

- **Program** (Rev1.0 FE): `N1[col] ON → pre → N2[row] ON → pot_pw → N2 OFF → zero → N1 OFF → post`
  `wwl_hold=1`이면 program phase 동안 N1을 계속 ON (Rev1.0 FE와 동일, GI 스트레스 회피 목적)
- **Erase** (Rev1.0 FEE): `N2 모두 OFF(WBL=V_off) → pre → N1[col] ON → dep_pw → N1 OFF → post → zero`
- **Read** (비파괴): WTR 꺼짐(WWL_OFF) → SN 플로팅 상태에서
  `CON LOW(RBL↔적분기 연결), CR HIGH(cap 개방) → RWL[row] HIGH read_time×100 µs (FeFET 전류 I_RBL이 cap에 적분, RBL은 op-amp 가상접지)
  → RWL LOW, read_delay → CON HIGH(분리) → 5 col ADC 동시 샘플(avg_count 평균) → CR LOW cap 방전 3 ms → 다음 row`
  row 0~4 반복 = 25셀 맵 1회. ADC ∝ V_cap = (1/C)∫I_RBL dt 이므로 Program 후 ↑, Erase 후 ↓.
  `read_mode`로 read 중 WWL 상태 선택: `N1OFF` WTR OFF(기본, 논문 방식) / `N1ALL` 5 col 모두 ON(게이트=WBL_OFF) / `N1SEL` 선택 col만 ON

## 타이밍 파라미터 (전부 yaml `defaults:` / 실험별 오버라이드, 단위 µs 아니면 표기)

| 키 | 기본 | 어디에 쓰이나 |
|---|---|---|
| `pot_pw` / `dep_pw` | 5000 | Program / Erase 펄스 폭 |
| `pre_en` | 20 | Program: N1 ON 후 N2 ON까지 · Erase: N2 OFF 확정 후 N1 ON까지 |
| `post_en` | 20 | 펄스 끝난 뒤 다음 라인 변경까지 |
| `zero_t` | 20 | Program: N2 OFF 후 게이트가 WBL_OFF로 복귀하는 시간 · Erase: 마지막 대기 |
| `read_wait_ms` | 100 ms | 맵 read 직전 대기 |
| `read_set_time` | 20 | N1ALL/N1SEL read에서 WWL ON 후 게이트 안정화 |
| `read_time` | 1000 (×100 µs) | RWL 적분 시간 |
| `read_delay` | 20 (×10 µs) | 적분 끝 → ADC 샘플 사이 settle |
| `phase_gap_ms` | 0 ms | 펌웨어 PD/DP에서 P→D 사이 대기 |
| `phase_gap_s` | — | `phase_voltages` 모드에서 phase 전환 후 추가 대기 (PSU settle 뒤) |
| `pause_after_s` | 0 | 실험 사이 대기 |

펌웨어 고정값: RWL 주기 100 µs(`RWL_PERIOD_US`), cap clear 3 ms(`CAP_CLEAR_MS`), read_delay 단위 10 µs. µs 값은 `udelay()`가 ms+µs로 나눠 처리하므로 수십 ms 펄스도 됨.

## P와 D에 다른 전압 쓰기 (`phase_voltages`)

```yaml
  - name: cell00_PD_splitV
    scenario: PD            # DP 도 가능
    rows: [0]
    cols: [0]
    set_num: 2
    phase_voltages:         # 없는 키는 voltages: 값 사용
      P: {WBL_PROG: 4.0, WBL_OFF: 0.0, WWL_ON: 3.0}
      D: {WBL_OFF: -3.0, WWL_ON: 4.0}
    phase_gap_s: 1.0
```
Python이 set마다 P rail 적용 → 펌웨어 `P` 실행 → D rail 적용 → `D` 실행을 반복하고, 데이터는 한 폴더(`set` 열로 구분)에 이어서 저장된다. 펌웨어 단독 `PD`는 rail 변경 없이 같은 전압으로 P→D를 돈다.
`WBL_OFF`에 음수를 쓰려면 그 채널을 `negative: true`로 배선해야 한다 (기본 yaml은 PSU2 CH3을 그렇게 배정).

## 전압 rail (PSU)

| rail | 의미 | 아두이노 핀 상태 |
|---|---|---|
| WWL_ON / WWL_OFF | WTR gate | N1 HIGH / LOW |
| WBL_PROG / WBL_OFF | WBL program / erase·read-write 레벨 | N2 HIGH / LOW |
| VREAD | RWL read 바이어스 — 반전 적분기라 가상접지(VINP)보다 **낮아야** ADC가 올라감 → 음수(반전 배선) | — |
| VDD | 보드 아날로그 전원 | — |

음전압 rail은 PSU 채널의 +를 GND에 묶어 만들고 yaml에서 `negative: true`로 표시 (드라이버는 |V|만 프로그램).
CH1은 6 V 제한이므로 큰 전압은 CH2/CH3에 배정. 보드용 PSU 3호기는 이 코드에서 건드리지 않음.

## 데이터

`data.csv` (long): `read_idx,t_s,set,phase,pulse,row,col,selected,adc,I_uA`
`I_uA = C·V_cap/t_int`, C=3000 pF, V_cap=adc·4/4095·3.3, t_int=read_time·100 µs (기존 GUI와 동일)
`plot.png`: 선택 셀(색) vs 비선택 24셀(회색) 추이 + final 맵 + final−initial 맵 (빨간 테두리 = 선택 셀)

## 확인/주의

1. 셀 좌표 규약(WWL=col, WBL=row)을 PCB에서 확인. 다르면 `.ino` 상단 PIN 테이블만 수정.
2. Rev1.0의 `R` 모드는 pulse_width를 초 단위로 썼음 → Rev2에서는 `R` 시나리오가 `r_interval_s`/`r_points`를 사용.
3. 처음에는 `INIT` → `DBGR`로 맵이 정상인지 본 뒤 `P` 몇 펄스로 시작 권장.
4. PSU rail 전압은 **실험마다 바꿀 수 있고**(`voltages:` 오버라이드) `sweep:`으로 ISPP처럼 스윕 가능.

## GUI (`fearray_gui.py`)

왼쪽에서 연결(시뮬레이션 체크, Arduino 포트, PSU1/2 VISA 주소)과 시나리오·타이밍을 입력하고,
가운데 5×5 격자에서 셀을 클릭해 선택(펌웨어는 row×col 교차곱을 선택하므로 추가되는 셀은 `+`로 표시),
PSU 전압은 기본 / P phase / D phase 열로 입력(P/D 열이 비면 기본값). `▶ 측정 시작`으로 실행하면
오른쪽에 선택 셀(색)과 비선택 셀(회색) 추이, 현재 25셀 맵이 실시간으로 그려지고, 저장 형식은 배치 실행과 동일.
`YAML 저장`으로 현재 입력을 `fearray_measure.py`용 설정으로 내보낼 수 있고, 반대로 불러올 수도 있다(첫 실험만).
