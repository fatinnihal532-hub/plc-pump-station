(* Duty/standby wet-well pump station: two pumps, level control, protection and alarms.
   Written in IEC 61131-3 Structured Text. Executed by plcsim/st.py in the tests. *)
PROGRAM PumpStation

VAR_INPUT
  level      : REAL;      (* wet-well level, m *)
  run_fb1    : BOOL;      (* contactor auxiliary contact, pump 1 *)
  run_fb2    : BOOL;
  estop_ok   : BOOL;      (* TRUE while the emergency-stop loop is healthy *)
  mode_auto  : BOOL;      (* selector: TRUE auto, FALSE manual *)
  man_cmd1   : BOOL;      (* manual start pushbuttons (latched by the HMI) *)
  man_cmd2   : BOOL;
  ack        : BOOL;      (* alarm acknowledge / fault reset *)
END_VAR

VAR_OUTPUT
  cmd1       : BOOL;
  cmd2       : BOOL;
  fault1     : BOOL;      (* fail to start, or running without a command *)
  fault2     : BOOL;
  dry_lock   : BOOL;      (* low-low level cut-out *)
  alarm_high : BOOL;
  alarm_hh   : BOOL;      (* latched until acknowledged *)
  horn       : BOOL;
END_VAR

VAR CONSTANT
  SCAN_S     : REAL := 0.25;  (* task interval, s: used for the run-hour counters *)
  L_LEAD_ON  : REAL := 1.8;   (* start lead pump *)
  L_LAG_ON   : REAL := 2.6;   (* start lag pump too *)
  L_LAG_OFF  : REAL := 1.6;
  L_OFF      : REAL := 0.8;   (* stop all pumps *)
  L_LOW      : REAL := 0.4;   (* dry-run cut-out level *)
  L_RESTART  : REAL := 1.0;   (* dry-run lock clears above this *)
  L_HIGH     : REAL := 3.2;
  L_HH       : REAL := 3.6;
END_VAR

VAR
  lead_dem   : BOOL;
  lag_dem    : BOOL;
  lead_is_1  : BOOL := TRUE;
  n_req      : INT;
  avail1     : BOOL;
  avail2     : BOOL;
  auto1      : BOOL;
  auto2      : BOOL;
  hold1      : BOOL;
  hold2      : BOOL;
  hh_latch   : BOOL;
  dry_tmr    : TON;
  ft1        : TON;
  ft2        : TON;
  mr1        : TON;      (* minimum run time, pump 1 *)
  mr2        : TON;
  lead_on    : R_TRIG;
  rh1        : REAL;      (* run time, s, pump 1 *)
  rh2        : REAL;
  ack_edge   : R_TRIG;
  hh_ack     : BOOL;
END_VAR

(* ---- demand from level, with hysteresis ---- *)
IF level >= L_LEAD_ON THEN lead_dem := TRUE; END_IF;
IF level <= L_OFF THEN lead_dem := FALSE; END_IF;
IF level >= L_LAG_ON THEN lag_dem := TRUE; END_IF;
IF level <= L_LAG_OFF THEN lag_dem := FALSE; END_IF;
IF NOT lead_dem THEN lag_dem := FALSE; END_IF;

(* run-hour equalisation: each pumping cycle starts with the pump that has run less *)
IF run_fb1 THEN rh1 := rh1 + SCAN_S; END_IF;
IF run_fb2 THEN rh2 := rh2 + SCAN_S; END_IF;
lead_on(CLK := lead_dem);
IF lead_on.Q THEN lead_is_1 := rh1 <= rh2; END_IF;

n_req := 0;
IF lead_dem THEN n_req := 1; END_IF;
IF lag_dem THEN n_req := 2; END_IF;

(* ---- dry-run protection ---- *)
dry_tmr(IN := level <= L_LOW, PT := T#5s);
IF dry_tmr.Q THEN dry_lock := TRUE; END_IF;
IF level >= L_RESTART THEN dry_lock := FALSE; END_IF;

(* ---- fault detection: 3 s of disagreement between command and feedback ---- *)
ack_edge(CLK := ack);
ft1(IN := (cmd1 AND NOT run_fb1) OR (run_fb1 AND NOT cmd1), PT := T#3s);
ft2(IN := (cmd2 AND NOT run_fb2) OR (run_fb2 AND NOT cmd2), PT := T#3s);
IF ft1.Q THEN fault1 := TRUE; END_IF;
IF ft2.Q THEN fault2 := TRUE; END_IF;
IF ack_edge.Q THEN
  IF NOT ft1.Q THEN fault1 := FALSE; END_IF;
  IF NOT ft2.Q THEN fault2 := FALSE; END_IF;
END_IF;

(* ---- availability and duty allocation (auto mode) ---- *)
avail1 := mode_auto AND estop_ok AND NOT dry_lock AND NOT fault1;
avail2 := mode_auto AND estop_ok AND NOT dry_lock AND NOT fault2;
auto1 := FALSE;
auto2 := FALSE;
IF lead_is_1 THEN
  IF n_req >= 1 AND avail1 THEN auto1 := TRUE; END_IF;
  IF n_req >= 2 AND avail2 THEN auto2 := TRUE; END_IF;
  IF n_req >= 1 AND NOT avail1 AND avail2 THEN auto2 := TRUE; END_IF;
ELSE
  IF n_req >= 1 AND avail2 THEN auto2 := TRUE; END_IF;
  IF n_req >= 2 AND avail1 THEN auto1 := TRUE; END_IF;
  IF n_req >= 1 AND NOT avail2 AND avail1 THEN auto1 := TRUE; END_IF;
END_IF;

(* minimum run time of 30 s once started, to protect the motor from short cycling *)
mr1(IN := cmd1, PT := T#30s);
mr2(IN := cmd2, PT := T#30s);
hold1 := cmd1 AND NOT mr1.Q AND avail1;
hold2 := cmd2 AND NOT mr2.Q AND avail2;

IF mode_auto THEN
  cmd1 := auto1 OR hold1;
  cmd2 := auto2 OR hold2;
ELSE
  (* manual: operator commands still pass through e-stop, dry-run and fault interlocks *)
  cmd1 := man_cmd1 AND estop_ok AND NOT dry_lock AND NOT fault1;
  cmd2 := man_cmd2 AND estop_ok AND NOT dry_lock AND NOT fault2;
END_IF;
IF NOT estop_ok THEN cmd1 := FALSE; cmd2 := FALSE; END_IF;

(* ---- alarms ---- *)
alarm_high := level >= L_HIGH;
IF level >= L_HH THEN alarm_hh := TRUE; END_IF;
IF ack_edge.Q AND level < L_HIGH THEN alarm_hh := FALSE; END_IF;
horn := (alarm_hh OR alarm_high OR fault1 OR fault2 OR dry_lock) AND NOT ack;

END_PROGRAM
