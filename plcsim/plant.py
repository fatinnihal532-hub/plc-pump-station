"""Simulated wet well and pumps, closed around the PLC program."""
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from .st import PLC

ST_FILE = Path(__file__).resolve().parent.parent / "st" / "pump_station.st"


@dataclass
class Pump:
    q_rated: float = 90.0          # m3/h at the operating point
    ramp_s: float = 2.0            # speed ramp after the contactor closes
    aux_delay_s: float = 0.5       # contactor auxiliary contact delay
    stuck_open: bool = False       # injected fault: contactor never closes
    welded: bool = False           # injected fault: contactor stays closed
    energised: bool = False
    speed: float = 0.0
    fb: bool = False
    _t_change: float = 0.0
    hours: float = 0.0
    starts: list = field(default_factory=list)


class Station:
    """Wet well of area `area` m2, two pumps, inflow(t) in m3/h. PLC scans every `dt` seconds."""

    def __init__(self, inflow, area=12.0, level0=0.9, dt=0.25, source=None, seed=1):
        self.plc = PLC(source if source is not None else ST_FILE.read_text())
        self.pumps = [Pump(), Pump()]
        self.inflow, self.area, self.level, self.dt = inflow, area, level0, dt
        self.t = 0.0
        self.inputs = dict(estop_ok=True, mode_auto=True, man_cmd1=False, man_cmd2=False, ack=False)
        self.out = {}
        self.rng = random.Random(seed)
        self.log = []
        self.overflow_m3 = 0.0
        self.max_level = level0
        self.min_level = level0
        self.spill_level = 4.0

    def _pump_step(self, p, cmd):
        contactor = cmd and not p.stuck_open or p.welded
        if contactor and not p.energised:
            p.starts.append(self.t)
        if contactor != p.energised:
            p._t_change = self.t
        p.energised = contactor
        target = 1.0 if contactor else 0.0
        step = self.dt / p.ramp_s
        p.speed += max(-step, min(step, target - p.speed))
        p.fb = contactor and (self.t - p._t_change) >= p.aux_delay_s
        if p.energised:
            p.hours += self.dt / 3600.0

    def step(self):
        lvl = self.level + self.rng.gauss(0, 0.002)               # level transmitter noise
        inp = dict(self.inputs, level=lvl, run_fb1=self.pumps[0].fb, run_fb2=self.pumps[1].fb)
        self.out = self.plc.scan(inp, self.dt * 1000.0)
        self._pump_step(self.pumps[0], self.out["cmd1"])
        self._pump_step(self.pumps[1], self.out["cmd2"])
        qin = self.inflow(self.t)
        qout = sum(p.q_rated * p.speed for p in self.pumps)
        self.level += (qin - qout) / 3600.0 * self.dt / self.area
        if self.level > self.spill_level:
            self.overflow_m3 += (self.level - self.spill_level) * self.area
            self.level = self.spill_level
        self.level = max(self.level, 0.0)
        self.max_level = max(self.max_level, self.level)
        self.min_level = min(self.min_level, self.level)
        self.t += self.dt
        self.log.append((self.t, self.level, self.pumps[0].speed, self.pumps[1].speed, dict(self.out)))

    def run(self, seconds, events=None):
        """events: list of (time_s, callable(station))."""
        events = sorted(events or [], key=lambda e: e[0])
        end = self.t + seconds
        while self.t < end:
            while events and events[0][0] <= self.t:
                events.pop(0)[1](self)
            self.step()
        return self

    def starts_in_last_hour(self, k):
        s = self.pumps[k].starts
        return max((sum(1 for u in s if t <= u < t + 3600) for t in s), default=0)


def diurnal(base=60.0, swing=25.0, period_h=6.0):
    return lambda t: base + swing * math.sin(2 * math.pi * t / (period_h * 3600.0))


def steady(q):
    return lambda t: q
