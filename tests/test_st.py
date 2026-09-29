import pytest
from plcsim import PLC, STError


def prog(body, decl="", inputs="", outputs="VAR_OUTPUT y : INT; b : BOOL; r : REAL; END_VAR"):
    return PLC(f"PROGRAM T\n{inputs}\n{outputs}\nVAR {decl} END_VAR\n{body}\nEND_PROGRAM")


def test_precedence_and_integer_division():
    p = prog("y := 2 + 3 * 4; r := 7 / 2.0; b := NOT FALSE AND TRUE OR FALSE;")
    o = p.scan({}, 10)
    assert o["y"] == 14 and o["r"] == 3.5 and o["b"] is True
    assert prog("y := -7 / 2;").scan({}, 1)["y"] == -3            # truncates toward zero
    assert prog("y := -7 MOD 3;").scan({}, 1)["y"] == -1
    assert prog("y := (2 + 3) * 4 - 10 / 5;").scan({}, 1)["y"] == 18


def test_comparison_chain_and_bool_ops():
    p = prog("b := (1 < 2) AND (2 <= 2) AND (3 <> 4) AND NOT (5 = 6) AND (7 >= 7);")
    assert p.scan({}, 1)["b"] is True
    assert prog("b := TRUE XOR TRUE;").scan({}, 1)["b"] is False


def test_functions():
    o = prog("r := LIMIT(0.0, 5.5, 3.0) + MAX(1.0, 2.0) + MIN(4.0, 3.0) + ABS(-1.0) + SQRT(16.0);").scan({}, 1)
    assert o["r"] == pytest.approx(3.0 + 2.0 + 3.0 + 1.0 + 4.0)
    assert prog("y := SEL(TRUE, 1, 2);").scan({}, 1)["y"] == 2


def test_if_elsif_else_and_case():
    src = "IF x < 0 THEN y := 1; ELSIF x < 10 THEN y := 2; ELSE y := 3; END_IF;"
    p = prog(src, inputs="VAR_INPUT x : INT; END_VAR")
    assert [p.scan({"x": v}, 1)["y"] for v in (-5, 5, 50)] == [1, 2, 3]
    src = "CASE x OF 1: y := 10; 2, 3: y := 20; ELSE y := 99; END_CASE;"
    p = prog(src, inputs="VAR_INPUT x : INT; END_VAR")
    assert [p.scan({"x": v}, 1)["y"] for v in (1, 2, 3, 4)] == [10, 20, 20, 99]


def test_ton_timing_is_exact():
    p = prog("t(IN := x, PT := T#1s500ms); b := t.Q; r := t.ET;", decl="t : TON;",
             inputs="VAR_INPUT x : BOOL; END_VAR")
    seen = [p.scan({"x": True}, 100)["b"] for _ in range(20)]
    assert seen.index(True) == 14                       # true on the 15th scan, at 1.5 s
    assert p.scan({"x": False}, 100)["b"] is False and p.fb["t"]["ET"] == 0.0
    p.scan({"x": True}, 100)
    assert p.fb["t"]["ET"] == 100.0                     # restarts from zero after IN drops


def test_tof_and_tp():
    p = prog("t(IN := x, PT := T#300ms); b := t.Q;", decl="t : TOF;", inputs="VAR_INPUT x : BOOL; END_VAR")
    out = [p.scan({"x": v}, 100)["b"] for v in (True, False, False, False, False)]
    assert out == [True, True, True, False, False]
    p = prog("t(IN := x, PT := T#300ms); b := t.Q;", decl="t : TP;", inputs="VAR_INPUT x : BOOL; END_VAR")
    out = [p.scan({"x": v}, 100)["b"] for v in (True, True, True, True, True, False)]
    assert out == [True, True, True, False, False, False]      # 300 ms pulse regardless of IN


def test_edges_and_latches():
    p = prog("r1(CLK := x); f1(CLK := x); y := 0; IF r1.Q THEN y := 1; END_IF; IF f1.Q THEN y := 2; END_IF;",
             decl="r1 : R_TRIG; f1 : F_TRIG;", inputs="VAR_INPUT x : BOOL; END_VAR")
    assert [p.scan({"x": v}, 1)["y"] for v in (False, True, True, False, False)] == [0, 1, 0, 2, 0]
    p = prog("a(S1 := s, R := rr); c(S := s, R1 := rr); b := a.Q1 AND NOT c.Q1;", decl="a : SR; c : RS;",
             inputs="VAR_INPUT s : BOOL; rr : BOOL; END_VAR")
    assert p.scan({"s": True, "rr": True}, 1)["b"] is True         # SR set-dominant, RS reset-dominant


def test_state_persists_between_scans_and_constants_initialise():
    p = prog("y := y + k;", decl="k : INT := 3;")
    assert [p.scan({}, 1)["y"] for _ in range(3)] == [3, 6, 9]


def test_comments_and_case_insensitive_keywords():
    p = PLC("program t (* header *)\nvar_output y : int; end_var // note\ny := 1 (* one *) + 1;\nend_program")
    assert p.scan({}, 1)["y"] == 2


@pytest.mark.parametrize("src,msg", [
    ("PROGRAM T VAR_OUTPUT y : INT; END_VAR z := 1; END_PROGRAM", "not declared"),
    ("PROGRAM T VAR_INPUT x : INT; END_VAR x := 1; END_PROGRAM", "cannot assign to input"),
    ("PROGRAM T VAR CONSTANT k : INT := 1; END_VAR k := 2; END_PROGRAM", "cannot assign to constant"),
    ("PROGRAM T VAR a : INT; a : INT; END_VAR END_PROGRAM", "declared twice"),
    ("PROGRAM T VAR a : INT; END_VAR IF a = 1 THEN a := 2; END_PROGRAM", "unexpected END_PROGRAM"),
    ("PROGRAM T VAR a : INT; END_VAR FOR a := 1 TO 3 DO END_FOR; END_PROGRAM", "not supported"),
    ("PROGRAM T VAR a : ARRAY; END_VAR END_PROGRAM", "unsupported type"),
    ("PROGRAM T VAR t : TON; END_VAR t(IN := TRUE, XX := 1); END_PROGRAM", "no input"),
    ("PROGRAM T VAR t : TON; y : BOOL; END_VAR y := t.Z; END_PROGRAM", "not a valid output"),
    ("PROGRAM T VAR y : INT; END_VAR y := 1 END_PROGRAM", "expected"),
    ("PROGRAM T VAR y : INT; END_VAR y := 1 $ 2; END_PROGRAM", "unexpected character"),
])
def test_static_errors(src, msg):
    with pytest.raises(STError, match=msg):
        PLC(src)


def test_runtime_errors():
    with pytest.raises(STError, match="BOOL needs"):
        prog("b := 1;").scan({}, 1)
    with pytest.raises(STError, match="division by zero"):
        prog("y := 1 / z;", decl="z : INT;").scan({}, 1)
    with pytest.raises(STError, match="not an input"):
        prog("y := 1;").scan({"nope": 1}, 1)
    with pytest.raises(STError, match="BOOL"):
        prog("IF 1 THEN y := 1; END_IF;").scan({}, 1)
