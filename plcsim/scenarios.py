"""Named closed-loop scenarios shared by the tests, verify.py and the figures."""
from .plant import Station, diurnal, steady

L = dict(LEAD_ON=1.8, LAG_ON=2.6, LAG_OFF=1.6, OFF=0.8, LOW=0.4, RESTART=1.0, HIGH=3.2, HH=3.6)


def normal(hours=8.0, seed=1):
    return Station(diurnal(60, 25, 6.0), level0=0.9, seed=seed).run(hours * 3600)


def pump1_fails(seed=1):
    """Pump 1 contactor will not close from the start; the operator repairs it and acknowledges later."""
    s = Station(steady(60.0), level0=1.5, seed=seed)
    s.pumps[0].stuck_open = True
    ev = [(2400.0, lambda st: setattr(st.pumps[0], "stuck_open", False)),
          (2500.0, lambda st: st.inputs.update(ack=True)),
          (2503.0, lambda st: st.inputs.update(ack=False))]
    return s.run(3600.0, ev)


def storm(seed=1):
    """Inflow of 190 m3/h against a 180 m3/h pump station capacity for 100 minutes, then it eases."""
    s = Station(lambda t: 190.0 if 600.0 <= t < 600.0 + 6000.0 else 45.0, level0=1.0, seed=seed)
    ev = [(9000.0, lambda st: st.inputs.update(ack=True)), (9003.0, lambda st: st.inputs.update(ack=False))]
    return s.run(3.5 * 3600, ev)
