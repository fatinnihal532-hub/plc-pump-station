"""Closed-loop checks on the Structured Text program. Exit code 1 if any fails."""
import sys
from plcsim.scenarios import normal, pump1_fails, storm, L
from plcsim.plant import Station, steady
from plcsim import PLC

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    fails += not ok


src = open("st/pump_station.st").read()
plc = PLC(src)
check("ST program parses and all names resolve", True, f"({len(plc.decl)} variables, {len(src.splitlines())} lines)")

n = normal(8.0)
check("8 h normal operation: level stays inside the control band",
      L["OFF"] - 0.05 < n.min_level and n.max_level < L["LEAD_ON"] + 0.15, f"({n.min_level:.2f} to {n.max_level:.2f} m)")
check("8 h normal operation: no alarms", not any(o["alarm_high"] or o["fault1"] or o["fault2"] or o["dry_lock"] for *_, o in n.log))
st = [len(p.starts) for p in n.pumps]
check("both pumps share the duty", min(st) >= 2, f"(starts {st}, hours {n.pumps[0].hours:.2f} / {n.pumps[1].hours:.2f})")
check("no pump exceeds 6 starts per hour", max(n.starts_in_last_hour(0), n.starts_in_last_hour(1)) <= 6)

f = pump1_fails()
tc = next(t for t, *_, o in f.log if o["cmd1"])
tf = next(t for t, *_, o in f.log if o["fault1"])
t2 = next(t for t, lv, a, b, o in f.log if b > 0.5)
check("pump 1 fail-to-start: latched in 3 to 3.6 s", 3.0 <= tf - tc <= 3.6, f"({tf - tc:.2f} s)")
check("pump 1 fail-to-start: standby running within 2 s of the fault", t2 - tf < 2.0, f"({t2 - tf:.2f} s)")
check("pump 1 fail-to-start: level never reaches the lag level", f.max_level < L["LAG_ON"], f"(peak {f.max_level:.2f} m)")

s = storm()
hh = next(t for t, *_, o in s.log if o["alarm_hh"])
check("storm: high-high alarm raised, no spill", s.max_level >= L["HH"] and s.overflow_m3 == 0,
      f"(alarm at {hh/60:.0f} min, peak {s.max_level:.2f} m of 4.00 m)")
check("storm: alarm latched until acknowledged", all(o["alarm_hh"] for t, *_, o in s.log if 7000 < t < 8900) and not s.out["alarm_hh"])

d = Station(lambda t: 0.0 if t < 900 else 200.0, level0=1.2)
d.inputs.update(mode_auto=False, man_cmd1=True, man_cmd2=True)
d.run(1500.0)
check("dry-run: manual pumps cut out at the low-low level and never run dry", d.min_level > 0.3,
      f"(minimum {d.min_level:.2f} m)")

e = Station(steady(200.0), level0=2.7)
e.run(60.0)
e.inputs["estop_ok"] = False
e.step()
check("e-stop drops both pumps in one scan", not e.out["cmd1"] and not e.out["cmd2"])
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
