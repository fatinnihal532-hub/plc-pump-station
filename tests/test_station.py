import pytest
from plcsim.plant import Station, steady, diurnal
from plcsim.scenarios import normal, pump1_fails, storm, L


def runs(pump):
    """(start, stop) intervals from the recorded speed trace is not needed: use start list and hours."""
    return pump.starts


@pytest.fixture(scope="module")
def n8():
    return normal(8.0)


def test_normal_level_stays_in_band(n8):
    assert L["OFF"] - 0.05 < n8.min_level and n8.max_level < L["LEAD_ON"] + 0.15
    assert n8.overflow_m3 == 0


def test_both_pumps_share_the_duty(n8):
    assert len(n8.pumps[0].starts) >= 2 and len(n8.pumps[1].starts) >= 2
    ev = sorted([(t, 1) for t in n8.pumps[0].starts] + [(t, 2) for t in n8.pumps[1].starts])
    who = [w for _, w in ev]
    assert 1 in who and 2 in who


def test_no_alarms_in_normal_operation(n8):
    assert not any(o["alarm_high"] or o["alarm_hh"] or o["fault1"] or o["fault2"] or o["dry_lock"]
                   for *_, o in n8.log)


def test_motor_start_limit(n8):
    assert max(n8.starts_in_last_hour(0), n8.starts_in_last_hour(1)) <= 6


def test_minimum_run_time_is_respected():
    """A tiny wet well forces rapid level swings; the 30 s hold must still keep every run at least 30 s."""
    s = Station(steady(60.0), area=1.2, level0=1.0, dt=0.25).run(3 * 3600)
    for k in (0, 1):
        on, runs_ = None, []
        for t, lv, s1, s2, o in s.log:
            c = o["cmd1" if k == 0 else "cmd2"]
            if c and on is None:
                on = t
            if not c and on is not None:
                runs_.append(t - on)
                on = None
        assert runs_ and min(runs_) >= 30.0 - 0.3


def test_run_hours_stay_balanced(n8):
    h1, h2 = n8.pumps[0].hours, n8.pumps[1].hours
    longest, on = 0.0, None                                        # longest pumping cycle, either pump running
    for t, lv, a, b, o in n8.log:
        busy = o["cmd1"] or o["cmd2"]
        if busy and on is None:
            on = t
        if not busy and on is not None:
            longest, on = max(longest, (t - on) / 3600.0), None
    assert longest > 0.5
    assert abs(h1 - h2) <= longest                                # equalising by cycle cannot beat one cycle


def test_fail_to_start_detected_and_standby_takes_over():
    s = pump1_fails()
    t_cmd = next(t for t, *_, o in s.log if o["cmd1"])
    t_fault = next(t for t, *_, o in s.log if o["fault1"])
    assert 3.0 <= t_fault - t_cmd <= 3.6                          # 3 s timer plus one or two scans
    t_p2 = next(t for t, lv, a, b, o in s.log if b > 0.5)
    assert t_p2 - t_fault < 2.0                                   # standby is called immediately
    assert s.max_level < L["LAG_ON"]                              # the well never gets near the lag level
    assert any(o["horn"] for *_, o in s.log)


def test_fault_does_not_clear_while_still_present_then_clears_after_repair():
    s = pump1_fails()
    # acknowledged at t=2500 s after the repair at t=2400 s
    before = [o["fault1"] for t, *_, o in s.log if 2300 < t < 2400]
    after = [o["fault1"] for t, *_, o in s.log if t > 2510]
    assert all(before) and not any(after)
    # a premature acknowledge while still faulty must not clear the latch
    s2 = Station(steady(60.0), level0=1.5)
    s2.pumps[0].stuck_open = True
    ev = [(150.0, lambda st: st.inputs.update(ack=True)), (153.0, lambda st: st.inputs.update(ack=False))]
    s2.run(600.0, ev)
    assert s2.out["fault1"] is True


def test_welded_contactor_detected():
    s = Station(steady(30.0), level0=1.2)
    s.pumps[1].welded = True
    s.run(20.0)
    assert s.out["fault2"] is True and s.out["cmd2"] is False


def test_storm_reaches_high_high_and_latches():
    s = storm()
    hh_t = next(t for t, *_, o in s.log if o["alarm_hh"])
    assert 600 < hh_t < 6000
    assert s.max_level >= L["HH"]
    both = [t for t, lv, a, b, o in s.log if a > 0.99 and b > 0.99]
    assert both and both[0] < hh_t                                # both pumps were already running
    # latched after the storm eases, cleared only by the acknowledge at 9000 s
    mid = [o["alarm_hh"] for t, *_, o in s.log if 7000 < t < 8900]
    assert all(mid) and not s.out["alarm_hh"]
    assert s.overflow_m3 == 0


def test_dry_run_cutout_in_manual_and_restart_after_recovery():
    s = Station(lambda t: 0.0 if t < 900 else 200.0, level0=1.2)
    s.inputs.update(mode_auto=False, man_cmd1=True, man_cmd2=True)
    s.run(1500.0)
    lock_t = next(t for t, *_, o in s.log if o["dry_lock"])
    stop_t = next(t for t, lv, a, b, o in s.log if t > lock_t and not (o["cmd1"] or o["cmd2"]))
    assert stop_t - lock_t < 1.0
    lv_at_lock = next(lv for t, lv, *_ in s.log if t == lock_t)
    assert lv_at_lock <= L["LOW"] + 0.02
    assert s.min_level > 0.3                                      # never runs dry
    assert any(t > 900 and not o["dry_lock"] for t, *_, o in s.log)   # lock releases above the restart level


def test_estop_drops_both_pumps_within_one_scan_and_resumes():
    s = Station(steady(200.0), level0=2.7)
    s.run(60.0)
    assert s.out["cmd1"] and s.out["cmd2"]
    s.inputs["estop_ok"] = False
    s.step()
    assert not s.out["cmd1"] and not s.out["cmd2"]
    s.run(60.0)
    s.inputs["estop_ok"] = True
    s.run(5.0)
    assert s.out["cmd1"] or s.out["cmd2"]


def test_manual_mode_still_honours_interlocks():
    s = Station(steady(60.0), level0=2.0)
    s.inputs.update(mode_auto=False, man_cmd1=True)
    s.run(10.0)
    assert s.out["cmd1"] and not s.out["cmd2"]
    s.inputs["estop_ok"] = False
    s.step()
    assert not s.out["cmd1"]


def test_both_pumps_faulty_never_starts_anything_and_alarms():
    s = Station(steady(120.0), level0=1.7)
    s.pumps[0].stuck_open = s.pumps[1].stuck_open = True
    s.run(1200.0)
    assert s.out["fault1"] and s.out["fault2"] and s.out["horn"]
    assert s.pumps[0].speed == 0 and s.pumps[1].speed == 0
    assert s.max_level > L["HIGH"]
