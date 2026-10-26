/*
 *  FeArray_2T0C_Rev2.ino  —  2T0C FeFET 5x5 sub-array driver (Arduino Due / SAM3X)
 *  ---------------------------------------------------------------------------
 *  Based on: 2T0C_Ferro_Rev1.0.ino (FE/FEE/FEA single-cell modes, pin map)
 *            4T0C array code (bitmask multi-cell selection, parseSerial, printResults)
 *
 *  Purpose : identical-pulse Program(P) / Erase(D) response of SELECTED cells
 *            while monitoring ALL 25 cells (disturb check) at every read point.
 *
 *  Cell (row r, col c)  —  firmware convention inherited from 2T0C_Ferro_Rev1.0:
 *      WWL (write word line, WTR gate)   = N1[c]   (PIOC, selected by COLUMN)
 *      WBL (write bit line, V_prog/V_off)= N2[r]   (PIOC, selected by ROW)
 *      RWL (read word line)              = WL[r]   (PIOD, latched by DFF/CK)
 *      RBL (read bit line -> integrator) = ADC[c]  (A0..A4)
 *      Storage node = FeFET gate (VCAP node in the schematic)
 *  !!! Verify this convention against the PCB before trusting cell coordinates. !!!
 *
 *  External voltage levels (set by PSU, NOT by this firmware):
 *      N1 pin HIGH -> WWL = V_WWL_ON ,  N1 pin LOW -> WWL = V_WWL_OFF
 *      N2 pin HIGH -> WBL = V_WBL_PROG, N2 pin LOW -> WBL = V_WBL_OFF (erase / read-write level)
 *
 *  ---------------------------------------------------------------------------
 *  SERIAL COMMAND  (115200 baud, 22 comma-separated fields, terminated by '|')
 *
 *   0  scenario      P | D | PD | DP | R | DBGP | DBGD | DBGR | INIT
 *                    LOOPP | LOOPD | LOOPR : repeat one program / erase pulse / one-row read forever (oscilloscope
 *                    check, no serial output) until the host sends '!'. extra2 (read_wait_ms) = gap between repeats.
 *   1  row_mask      5-bit mask of selected rows (bit r = row r), 0..31
 *   2  col_mask      5-bit mask of selected cols (bit c = col c), 0..31
 *   3  set_num       number of P/D sets (PD, DP)
 *   4  pot_num       program pulses per set
 *   5  dep_num       erase   pulses per set
 *   6  pot_pw        program pulse width [us]
 *   7  dep_pw        erase   pulse width [us]
 *   8  pre_en        delay after WWL ON before WBL pulse (program) / after WBL settle before WWL (erase) [us]
 *   9  post_en       delay after pulse end before next line change [us]
 *  10  zero_t        gate settle time at end of each pulse [us]
 *  11  read_time     RWL integration pulses (x RWL_PERIOD_US)
 *  12  read_set_time gate settle after WWL ON before sensing (N1ALL/N1SEL read) [us]
 *  13  read_delay    settling clocks after integration (x10 us)
 *  14  read_interval read every N pulses
 *  15  read_mode     N1OFF (WTR off, gate floating) | N1ALL (all WWL on) | N1SEL (selected WWL on)
 *  16  extra0        avg_count   : ADC samples averaged per read (0 -> 1)
 *  17  extra1        wwl_hold    : 1 = keep selected WWL ON during whole program phase (old FE mode)
 *  18  extra2        read_wait   : delay before each read [ms]
 *  19  extra3        R scenario  : interval between map reads [s]
 *  20  extra4        R scenario  : number of map reads
 *  21  extra5        phase_gap_ms: PD/DP에서 P phase와 D phase 사이 대기 [ms]
 *  22  extra6        dep_path : 0 = TOP (erase = WWL pulse with WBL at WBL_OFF, Rev1.0 FEE)
 *                               1 = BOT (erase = UD select ON (BLBTM = WBL_DEP, negative) + UDEN(S2) pulse,
 *                                        symmetric to program; board's dedicated depression path)
 *  23  extra7        (reserved)
 *
 *  Example:  PD,1,1,3,10,10,5000,5000,20,20,20,1000,1,20,1,N1OFF,10,0,100,0,0,0|
 *            -> cell (0,0), 3 sets of 10 P + 10 D pulses of 5 ms, read after every pulse,
 *               full 25-cell map at every read, read with WTR off, 10-sample ADC average.
 *
 *  ---------------------------------------------------------------------------
 *  SERIAL OUTPUT (one line per row, 5 lines per read point)
 *      set,phase,pulse,row,ADC0,ADC1,ADC2,ADC3,ADC4>
 *          set   : set index (0 = initial read)
 *          phase : I (initial) | P | D | R
 *          pulse : cumulative pulse count within the phase (0 for initial / R)
 *          row   : 0..4 ;  ADCc = cell (row, c) value, 0..1023 (raw12bit/4)
 *      ABORT> scenario aborted by host (send a single '!' byte at any time)
 *      EOD>   end of scenario
 *      ERR,...>  parse / argument error
 */

#include <ctype.h>
#include <stdlib.h>

// ───────────────────────────── PIN TABLES ─────────────────────────────
// N1 = WWL, indexed by COLUMN  (pins 51,50,49,48,47 -> PC12..PC16)
const uint32_t N1_PIN[5] = {1u << 12, 1u << 13, 1u << 14, 1u << 15, 1u << 16};
// N2 = WBL select, indexed by ROW (pins 39,38,37,36,35 -> PC7..PC3)
const uint32_t N2_PIN[5] = {1u << 7, 1u << 6, 1u << 5, 1u << 4, 1u << 3};
// Bottom (depression) path — board connectors UDEN0..4 / UD0..4
// UDEN = S2 gate select, indexed by COLUMN (pins 46,45,44,41,40 -> PC17,PC18,PC19,PC9,PC8)   [from 4T0C table]
const uint32_t UDEN_PIN[5] = {1u << 17, 1u << 18, 1u << 19, 1u << 9, 1u << 8};
// UD = BLBTM mux select, indexed by ROW. Only row 0 (pin 34 = PC2) is confirmed by the schematic;
// rows 1..4 are a GUESS (pins 33,7,6,5 -> PC1,PC23,PC24,PC25) — verify with LOOPD dep_path=1 on each row.
const uint32_t UD_PIN[5]   = {1u << 2, 1u << 1, 1u << 23, 1u << 24, 1u << 25};
// RWL, indexed by ROW (pins 25,26,27,28,29 -> PD0,PD1,PD2,PD3,PD6)
const uint32_t WL_PIN[5] = {1u << 0, 1u << 1, 1u << 2, 1u << 3, 1u << 6};
// ADC channel per COLUMN (A0..A4 -> ADC ch7..ch3)
const int ADC_CH[5] = {7, 6, 5, 4, 3};

const uint32_t N1_ALL = N1_PIN[0] | N1_PIN[1] | N1_PIN[2] | N1_PIN[3] | N1_PIN[4];
const uint32_t N2_ALL = N2_PIN[0] | N2_PIN[1] | N2_PIN[2] | N2_PIN[3] | N2_PIN[4];
const uint32_t UDEN_ALL = UDEN_PIN[0] | UDEN_PIN[1] | UDEN_PIN[2] | UDEN_PIN[3] | UDEN_PIN[4];
const uint32_t UD_ALL   = UD_PIN[0] | UD_PIN[1] | UD_PIN[2] | UD_PIN[3] | UD_PIN[4];
const uint32_t WL_ALL = WL_PIN[0] | WL_PIN[1] | WL_PIN[2] | WL_PIN[3] | WL_PIN[4];

// Control signals (same as 2T0C_Ferro_Rev1.0 / 4T0C)
#define CK_SET()      (PIOA->PIO_SODR = 1u << 7)    // DFF clock (pin 31)
#define CK_CLR()      (PIOA->PIO_CODR = 1u << 7)
#define DS_SET()      (PIOA->PIO_SODR = 1u << 19)   // pin 42
#define DS_CLR()      (PIOA->PIO_CODR = 1u << 19)
#define CON_SET()     (PIOB->PIO_SODR = 1u << 14)   // pin 53, LOW = cell<->integrator connected
#define CON_CLR()     (PIOB->PIO_CODR = 1u << 14)
#define CR_SET()      (PIOB->PIO_SODR = 1u << 21)   // pin 52, HIGH = integrator cap open (integrating)
#define CR_CLR()      (PIOB->PIO_CODR = 1u << 21)   //         LOW  = cap cleared
#define FB_CLR()      (PIOB->PIO_CODR = 1u << 26)   // pin 22
#define HLCHOP_CLR()  (PIOB->PIO_CODR = 1u << 25)   // pin 2, LOW = top read TR

const int RWL_PERIOD_US   = 100;   // Read_scaling: delayMicroseconds(100) per RWL pulse
const int READ_DELAY_US   = 10;
const int CAP_CLEAR_MS    = 3;

// ───────────────────────────── GLOBAL PARAMS ──────────────────────────
struct Params {
  String scenario;
  int row_mask, col_mask;
  int set_num, pot_num, dep_num;
  int pot_pw, dep_pw;
  int pre_en, post_en, zero_t;
  int read_time, read_set_time, read_delay, read_interval;
  String read_mode;
  double extra[8];
  // derived
  int avg_count;
  int wwl_hold;
  int read_wait_ms;
  int phase_gap_ms;
  int dep_path;
};
Params g;
int adc_row[5];
bool g_abort = false;

// host can abort a running scenario by sending '!'
bool checkAbort() {
  while (Serial.available()) { if (Serial.read() == '!') g_abort = true; }
  return g_abort;
}

// ───────────────────────────── HELPERS ────────────────────────────────
// delayMicroseconds()는 큰 값에서 정확도가 떨어지므로 ms 단위는 delay()로 분리
void udelay(long us) {
  if (us <= 0) return;
  if (us >= 1000) { delay(us / 1000); us %= 1000; }
  if (us > 0) delayMicroseconds((unsigned int)us);
}

uint32_t maskToPins(int mask, const uint32_t *table) {
  uint32_t a = 0;
  for (int i = 0; i < 5; i++) if (mask & (1 << i)) a |= table[i];
  return a;
}

void allLinesOff() {
  PIOC->PIO_CODR = N1_ALL | N2_ALL | UDEN_ALL | UD_ALL;
  PIOD->PIO_CODR = WL_ALL;
}

// ───────────────────────────── WRITE PULSES ───────────────────────────
// Program: WBL = V_prog pulse while WWL ON.  (FE mode of Rev1.0)
//   wwl_hold=0 : N1 ON -> pre -> N2 ON -> pw -> N2 OFF -> zero -> N1 OFF -> post
//   wwl_hold=1 : N1 assumed already ON (caller holds it) -> N2 ON -> pw -> N2 OFF -> zero
void programPulse(uint32_t n1, uint32_t n2) {
  if (!g.wwl_hold) {
    PIOC->PIO_SODR = n1;
    udelay(g.pre_en);
  }
  PIOC->PIO_SODR = n2;
  udelay(g.pot_pw);
  PIOC->PIO_CODR = n2;
  udelay(g.zero_t);          // gate -> V_WBL_OFF through WTR
  if (!g.wwl_hold) {
    PIOC->PIO_CODR = n1;
    udelay(g.post_en);
  }
}

// Erase: WBL stays at V_off, WWL pulse (FEE mode of Rev1.0)
//   N2 OFF -> pre -> N1 ON -> pw -> N1 OFF -> post -> zero
void erasePulse(uint32_t n1) {
  PIOC->PIO_CODR = N2_ALL;
  udelay(g.pre_en);
  PIOC->PIO_SODR = n1;
  udelay(g.dep_pw);
  PIOC->PIO_CODR = n1;
  udelay(g.post_en);
  udelay(g.zero_t);
}

// Erase via bottom path (dep_path=1): UD select ON (BLBTM = WBL_DEP, negative) -> pre -> UDEN(S2) ON -> pw
//   -> UDEN OFF -> zero -> UD OFF -> post.  Top path stays off (N1 OFF, N2 LOW).
void erasePulseBot(uint32_t ud, uint32_t uden) {
  PIOC->PIO_CODR = N1_ALL | N2_ALL;
  PIOC->PIO_SODR = ud;
  udelay(g.pre_en);
  PIOC->PIO_SODR = uden;
  udelay(g.dep_pw);
  PIOC->PIO_CODR = uden;
  udelay(g.zero_t);
  PIOC->PIO_CODR = ud;
  udelay(g.post_en);
}

// ───────────────────────────── READ ───────────────────────────────────
// Integrate RBL current of one row into the 5 column integrators, sample ADC.
void readRow(int row, int *out) {
  uint32_t wl = WL_PIN[row];

  CON_CLR();            // connect cells to integrators
  DS_SET();
  CR_SET();             // cap open -> integrating

  for (int i = 0; i < g.read_time; i++) {   // RWL pulse train (latched by DFF)
    PIOD->PIO_SODR = wl;
    CK_SET(); CK_CLR();
    PIOD->PIO_CODR = wl;
    delayMicroseconds(RWL_PERIOD_US);
  }
  for (int i = 0; i < g.read_delay; i++) {  // settle, RWL latched low
    CK_SET(); CK_CLR();
    PIOD->PIO_CODR = WL_ALL;
    delayMicroseconds(READ_DELAY_US);
  }
  CK_SET(); CK_CLR();
  DS_CLR();
  CON_SET();            // disconnect, hold cap voltage

  long sum[5] = {0, 0, 0, 0, 0};
  int n = g.avg_count > 0 ? g.avg_count : 1;
  for (int a = 0; a < n; a++)
    for (int c = 0; c < 5; c++) sum[c] += ADC->ADC_CDR[ADC_CH[c]];
  for (int c = 0; c < 5; c++) out[c] = (int)((sum[c] / n) / 4);

  CR_CLR();             // clear integrator cap
  delay(CAP_CLEAR_MS);
}

// Read all 25 cells and print 5 lines. WWL state during read set by read_mode.
void readMap(int set, char phase, int pulse) {
  uint32_t n1_read = 0;
  if (g.read_mode == "N1ALL")      n1_read = N1_ALL;
  else if (g.read_mode == "N1SEL") n1_read = maskToPins(g.col_mask, N1_PIN);
  // "N1OFF" (default): WTR off, gate floating

  if (g.read_wait_ms > 0) delay(g.read_wait_ms);

  PIOC->PIO_CODR = N2_ALL;                 // WBL = V_off everywhere
  FB_CLR(); HLCHOP_CLR();
  bool held = g.wwl_hold && (phase == 'P');  // caller already holds selected N1
  if (n1_read) {
    PIOC->PIO_SODR = n1_read;
    udelay(g.read_set_time);
  }

  for (int r = 0; r < 5 && !checkAbort(); r++) {
    readRow(r, adc_row);
    Serial.print(set);   Serial.print(',');
    Serial.print(phase); Serial.print(',');
    Serial.print(pulse); Serial.print(',');
    Serial.print(r);
    for (int c = 0; c < 5; c++) { Serial.print(','); Serial.print(adc_row[c]); }
    Serial.println('>');
  }

  if (n1_read) {
    uint32_t keep = held ? maskToPins(g.col_mask, N1_PIN) : 0;
    PIOC->PIO_CODR = (n1_read & ~keep);
  }
}

// ───────────────────────────── PHASES ─────────────────────────────────
void runProgramPhase(int set) {
  uint32_t n1 = maskToPins(g.col_mask, N1_PIN);
  uint32_t n2 = maskToPins(g.row_mask, N2_PIN);
  PIOC->PIO_CODR = N2_ALL;
  if (g.wwl_hold) {                        // old FE mode: hold selected WWL ON
    PIOC->PIO_SODR = n1;
    udelay(g.read_set_time);
  }
  for (int k = 0; k < g.pot_num && !checkAbort(); k++) {
    programPulse(n1, n2);
    if (g.read_interval > 0 && ((k + 1) % g.read_interval == 0))
      readMap(set, 'P', k + 1);
  }
  if (g.wwl_hold) PIOC->PIO_CODR = n1;
}

void runErasePhase(int set) {
  uint32_t n1 = maskToPins(g.col_mask, N1_PIN);
  uint32_t ud = maskToPins(g.row_mask, UD_PIN);
  uint32_t uden = maskToPins(g.col_mask, UDEN_PIN);
  for (int k = 0; k < g.dep_num && !checkAbort(); k++) {
    if (g.dep_path == 1) erasePulseBot(ud, uden); else erasePulse(n1);
    if (g.read_interval > 0 && ((k + 1) % g.read_interval == 0))
      readMap(set, 'D', k + 1);
  }
}

// ───────────────────────────── SERIAL PARSE ───────────────────────────
bool parseSerial(Params &p) {
  const int VAR_NUM = 24;
  String line = Serial.readStringUntil('|');
  line.trim();
  if (line.length() == 0) return false;

  String f[VAR_NUM];
  int start = 0, count = 0;
  while (count < VAR_NUM) {
    int j = line.indexOf(',', start);
    if (j < 0 || count == VAR_NUM - 1) { f[count++] = line.substring(start); break; }
    f[count++] = line.substring(start, j);
    start = j + 1;
  }
  if (count < 16) { Serial.print("ERR,fields="); Serial.print(count); Serial.println('>'); return false; }
  for (int i = 0; i < VAR_NUM; i++) f[i].trim();

  p.scenario      = f[0];
  p.row_mask      = f[1].toInt() & 31;
  p.col_mask      = f[2].toInt() & 31;
  p.set_num       = f[3].toInt();
  p.pot_num       = f[4].toInt();
  p.dep_num       = f[5].toInt();
  p.pot_pw        = f[6].toInt();
  p.dep_pw        = f[7].toInt();
  p.pre_en        = f[8].toInt();
  p.post_en       = f[9].toInt();
  p.zero_t        = f[10].toInt();
  p.read_time     = f[11].toInt();
  p.read_set_time = f[12].toInt();
  p.read_delay    = f[13].toInt();
  p.read_interval = f[14].toInt();
  p.read_mode     = f[15]; p.read_mode.toUpperCase();
  for (int i = 0; i < 8; i++) p.extra[i] = (16 + i < count) ? f[16 + i].toDouble() : 0.0;

  p.avg_count    = (int)p.extra[0]; if (p.avg_count < 1) p.avg_count = 1;
  p.wwl_hold     = (int)p.extra[1] ? 1 : 0;
  p.read_wait_ms = (int)p.extra[2];
  p.phase_gap_ms = (int)p.extra[5];
  p.dep_path     = (int)p.extra[6] ? 1 : 0;
  if (p.read_mode != "N1ALL" && p.read_mode != "N1SEL") p.read_mode = "N1OFF";
  return true;
}

// ───────────────────────────── SETUP / LOOP ───────────────────────────
void setup() {
  const int outs[] = {53,52,51,50,49,48,47,46,45,44,43,42,41,40,39,38,37,36,35,34,33,31,29,28,27,26,25,22,2,5,6,7};
  for (unsigned i = 0; i < sizeof(outs) / sizeof(outs[0]); i++) pinMode(outs[i], OUTPUT);
  ADC->ADC_MR  |= 0x80;   // free-run
  ADC->ADC_CR   = 2;      // start
  ADC->ADC_CHER = 0xF8;   // ch3..ch7
  allLinesOff();
  FB_CLR(); HLCHOP_CLR();
  Serial.begin(115200);
  Serial.setTimeout(2000);
}

void loop() {
  if (!Serial.available()) return;
  if (!parseSerial(g)) return;

  g_abort = false;
  allLinesOff();
  FB_CLR(); HLCHOP_CLR();
  const String &s = g.scenario;

  if (s == "P") {
    readMap(0, 'I', 0);
    runProgramPhase(1);
  } else if (s == "D") {
    readMap(0, 'I', 0);
    runErasePhase(1);
  } else if (s == "PD") {
    readMap(0, 'I', 0);
    for (int i = 1; i <= g.set_num && !g_abort; i++) { runProgramPhase(i); if (g.phase_gap_ms > 0) delay(g.phase_gap_ms); runErasePhase(i); }
  } else if (s == "DP") {
    readMap(0, 'I', 0);
    for (int i = 1; i <= g.set_num && !g_abort; i++) { runErasePhase(i); if (g.phase_gap_ms > 0) delay(g.phase_gap_ms); runProgramPhase(i); }
  } else if (s == "R") {                       // repeated map read (retention / drift)
    int n = (int)g.extra[4]; if (n < 1) n = 1;
    long interval_ms = (long)(g.extra[3] * 1000.0);
    for (int i = 0; i < n && !checkAbort(); i++) {
      if (i > 0 && interval_ms > 0) { for (long t = 0; t < interval_ms && !checkAbort(); t += 50) delay(50); }
      readMap(i, 'R', 0);
    }
  } else if (s == "DBGP") {
    runProgramPhase(1);                        // pulses only (read_interval=0 -> no read)
  } else if (s == "DBGD") {
    runErasePhase(1);
  } else if (s == "DBGR") {
    readMap(0, 'R', 0);
  } else if (s == "INIT") {                    // all lines off, one map read
    readMap(0, 'I', 0);
  } else if (s == "LOOPP" || s == "LOOPD" || s == "LOOPR") {   // oscilloscope test: repeat until '!'
    uint32_t n1 = maskToPins(g.col_mask, N1_PIN);
    uint32_t n2 = maskToPins(g.row_mask, N2_PIN);
    uint32_t ud = maskToPins(g.row_mask, UD_PIN);
    uint32_t uden = maskToPins(g.col_mask, UDEN_PIN);
    int row = 0; for (int r = 0; r < 5; r++) if (g.row_mask & (1 << r)) { row = r; break; }
    PIOC->PIO_CODR = N2_ALL;
    if (s == "LOOPP" && g.wwl_hold) PIOC->PIO_SODR = n1;
    Serial.println("LOOP>");
    while (!checkAbort()) {
      if (s == "LOOPP")      programPulse(n1, n2);
      else if (s == "LOOPD") { if (g.dep_path == 1) erasePulseBot(ud, uden); else erasePulse(n1); }
      else                   readRow(row, adc_row);
      if (g.read_wait_ms > 0) delay(g.read_wait_ms);
    }
  } else {
    Serial.print("ERR,unknown scenario "); Serial.print(s); Serial.println('>');
  }

  allLinesOff();
  if (g_abort) Serial.println("ABORT>");
  delay(50);
  Serial.println("EOD>");
}
