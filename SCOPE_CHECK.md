# 오실로스코프 펄스 검증 절차 (소자 연결 전)

목표: 소자를 꽂기 전에 (1) Arduino 핀에서 의도한 타이밍이 나오는지, (2) 보드 mux 출력에서 PSU rail이 의도한 전압으로 바뀌는지, (3) read 시퀀스(CON/CR/RWL/적분기)가 맞는지 확인.

## 0. 준비

| 항목 | 내용 |
|---|---|
| 펌웨어 | `FeArray_2T0C_Rev2.ino` 최신(LOOPP/LOOPD/LOOPR, `!` abort 포함) 업로드 |
| 실행 | Windows CMD → `pushd \\wsl.localhost\Ubuntu-22.04\home\yunho\2t0c-array-setup\python` → `python fearray_gui.py` |
| 프로브 | GND는 Arduino GND(=보드 GND). Due 핀은 3.3 V 로직. mux 출력은 ±수 V이므로 10x |
| 스코프 | Normal 트리거, 트리거 소스 = N1(51번) 상승엣지. 2ch면 CH1=51, CH2=39 |
| GUI | 시나리오 LOOPP/LOOPD/LOOPR: ■ 중지 누를 때까지 같은 펄스를 반복 (반복 간격 = "Read 전 대기 [ms]") |

## 1단계 — Arduino 단독 (보드·PSU 연결 없음, "PSU 제어 사용" 해제)

셀 (0,0) 선택. 기본 타이밍(pre 20 / P폭 5000 / zero 20 / post 20 µs).

| 시나리오 | 프로브 | 기대 파형 | 확인 포인트 |
|---|---|---|---|
| LOOPP | 51(N1), 39(N2) | N1 High 5040 µs. N2는 N1보다 20 µs 늦게 올라가 5000 µs 유지, N1보다 20 µs 먼저 내려감 | pre/zero = 20 µs 오프셋, P폭 값 변경 시 폭 추종 |
| LOOPD (D 경로 = TOP) | 51(N1), 39(N2) | N1 5000 µs 단일 펄스, N2 계속 Low. 보드 UP0는 WBL_OFF 고정 → 변화 없음(정상) | depression 전압은 UP0에 '펄스'가 아니라 WBL_OFF 레벨로 나타남 |
| LOOPD (D 경로 = BOT, 권장) | 46(UDEN0=S2), 34(UD0 select) | UD 34번 High 5040 µs, 그 안에 UDEN 46번 5000 µs 펄스 (program과 대칭). 보드 UD0가 BLBTM_OFF↔WBL_DEP 스윙 | row 1~4의 UD 선택 핀(펌웨어 추정 33,7,6,5)은 실제 PCB와 대조 필수 |
| LOOPP, 셀 (1,2) | 49(N1, col 2), 38(N2, row 1) | 선택 셀에 따라 핀이 바뀜: col c → 핀 51−c, row r → 핀 39−r | 핀 테이블(.ino 상단) 검증 |
| LOOPR | 25(RWL row0), 31(CK), 53(CON), 52(CR) | CON Low·CR High 약 100 ms 구간 / RWL은 DFF 입력이라 100 µs 주기 짧은 펄스 ×1000, CK 매번 토글 / 끝에 CON High → CR Low 3 ms | read_time 바꾸면 구간 길이 변경(1000→100 ms) |

판정: 폭·오프셋이 GUI 값과 ±1 µs 이내, 핀이 셀 좌표와 일치. 틀리면 `.ino` 상단 `N1_PIN / N2_PIN / WL_PIN / ADC_CH` 테이블만 수정.

## 2단계 — 보드 연결, PSU 낮은 전압 (소자 소켓은 비움)

PSU "제어 사용" 켜고 전압은 안전값으로: VDD 5, WWL_ON +1, WWL_OFF −0.5, WBL_PROG +1, WBL_OFF 0, VREAD −0.5. 배선 확인: WWL_OFF·WBL_OFF·VREAD 채널은 + 단자를 GND에(반전), 모든 PSU GND 공통.

| 시나리오 | 프로브 | 기대 | 확인 포인트 |
|---|---|---|---|
| LOOPP | S1 mux 출력(WTR gate), BLTOP mux 출력(WBL) | WTR gate: WWL_OFF↔WWL_ON 스윙 / WBL: WBL_OFF↔WBL_PROG 스윙, Arduino 펄스와 같은 타이밍 | 레벨시프터/mux 지연, 상승·하강 시간 → pre/zero(20 µs)보다 크면 그 값 늘리기 |
| LOOPD | 같은 지점 | WTR gate만 펄스, WBL은 WBL_OFF 고정 | — |
| "전압 적용" 후 멀티미터 | 각 mux 입력 | PSU 설정값과 극성 일치 (반전 rail은 음수) | GUI "측정값 읽기"의 전압/전류와 비교 |
| LOOPR | RWL(read TR gate 측), 적분기 V_OUT | RWL이 VINP↔VREAD 사이를 오가는지 / 소자 없으니 V_OUT은 CR High 동안 거의 평탄 (누설만) | VREAD가 VINP보다 **낮아야** 전류가 적분기에서 셀로 흐르고 ADC가 올라감 |

판정: mux 출력 레벨 = PSU 설정값, 지연이 타이밍 파라미터 안에 들어옴. 이후 실제 전압(WWL_ON 3, WBL_PROG 4 등)으로 올려 재확인.

## 3단계 — 소자 삽입 후 첫 read

1. INIT → DBGR 로 25셀 맵. 값이 0 또는 1023에 붙어 있으면 VREAD / read_time 조정.
2. LOOPR 하면서 V_OUT 램프 확인: RWL High 100 ms 동안 선형 상승 → CON High에서 멈춤 → CR Low에서 리셋. 3.3 V 근처까지 가면 포화 → read_time ↓.
3. DBGP 1펄스 → DBGR, DBGD 1펄스 → DBGR 로 선택 셀만 변하는지.
4. 그 다음 PD(phase_voltages: P에서 WBL_OFF 0, D에서 −3)로 본 측정.

## 알아 두어야 할 것

- Erase(D)는 두 경로: **BOT(권장)** = 보드의 하단 경로 UD/UDEN. UD 선택 HIGH → BLBTM = WBL_DEP(−3 V, 반전 배선) → UDEN(S2) 펄스. program과 대칭이라 PSU 전환 없이 P/D 혼합 가능. **TOP** = WWL 펄스로 게이트를 WBL_OFF에 연결 → WBL_OFF가 0/−3을 겸하므로 `phase_voltages`로 phase마다 바꿔야 하고 read는 `N1OFF`.
- BOT 경로 핀: UDEN0..4 = 46,45,44,41,40 (4T0C 표). UD0 = 34 확인, UD1..4 = 33,7,6,5 **추정** → LOOPD(BOT)로 row를 바꿔가며 어느 핀이 토글되는지 확인하고 `.ino`의 `UD_PIN` 수정.
- 새 rail: WBL_DEP(PSU2 CH2, 반전), BLBTM_OFF(PSU1 CH2). UDEN의 게이트 레벨(3V/−2V mux)이 WWL_ON/WWL_OFF rail을 공유하는지 확인.
- RWL(VREAD)은 반전 적분기 때문에 가상접지(VINP)보다 **낮아야** ADC가 양으로 올라감. VINP가 0 V가 아닐 수 있으니 보드 설정 확인.
- 음전압 rail(WWL_OFF, WBL_OFF, VREAD)은 PSU 채널 + 단자를 GND에 묶어 만들고 yaml `negative: true`. 코드는 |V|만 설정하고 극성 불일치는 에러로 막음.
- row×col 교차곱 선택. (0,0)+(1,1)을 찍으면 (0,1),(1,0)도 선택됨(+ 표시).
- LOOP* 동안 GUI 그래프는 비어 있음(데이터 없음). ■ 중지로 종료.
- 측정 데이터는 `python/data/<시각>_<이름>/` (git 제외).
