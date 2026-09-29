"""A small IEC 61131-3 Structured Text interpreter.

Supported: PROGRAM with VAR_INPUT / VAR_OUTPUT / VAR (optionally CONSTANT); BOOL, INT, DINT, REAL, TIME;
:= assignment; IF / ELSIF / ELSE; CASE; standard function blocks TON, TOF, TP, R_TRIG, F_TRIG, SR, RS;
functions ABS, MIN, MAX, LIMIT, SEL, SQRT; all IEC operators (** is not supported).
Not supported: user function blocks, arrays, structs, FOR/WHILE, pointers. Unsupported syntax raises STError
with a line number rather than being ignored.
"""
import math
import re


class STError(Exception):
    pass


TOKEN = re.compile(r"""
   (?P<ws>\s+)|(?P<cmt>\(\*.*?\*\)|//[^\n]*)
  |(?P<time>(?:T|TIME)\#(?:-?\d+(?:\.\d+)?(?:ms|d|h|m|s))+)
  |(?P<real>\d+\.\d+(?:[eE][-+]?\d+)?)|(?P<int>\d+)
  |(?P<id>[A-Za-z_][A-Za-z_0-9]*)
  |(?P<op>:=|<>|<=|>=|=>|[-+*/=<>();:,.&])
""", re.X | re.S | re.I)

KEYWORDS = {"PROGRAM", "END_PROGRAM", "VAR", "VAR_INPUT", "VAR_OUTPUT", "END_VAR", "CONSTANT", "IF", "THEN", "ELSIF",
            "ELSE", "END_IF", "CASE", "OF", "END_CASE", "AND", "OR", "XOR", "NOT", "MOD", "TRUE", "FALSE"}
FB_TYPES = {"TON", "TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS"}
BASE_TYPES = {"BOOL", "INT", "DINT", "REAL", "TIME"}
FB_PORTS = {"TON": ({"IN", "PT"}, {"Q", "ET"}), "TOF": ({"IN", "PT"}, {"Q", "ET"}), "TP": ({"IN", "PT"}, {"Q", "ET"}),
            "R_TRIG": ({"CLK"}, {"Q"}), "F_TRIG": ({"CLK"}, {"Q"}),
            "SR": ({"S1", "R"}, {"Q1"}), "RS": ({"S", "R1"}, {"Q1"})}
FUNCS = {"ABS": 1, "MIN": 2, "MAX": 2, "LIMIT": 3, "SEL": 3, "SQRT": 1}
UNITS = {"d": 86400000.0, "h": 3600000.0, "m": 60000.0, "s": 1000.0, "ms": 1.0}


def tokenize(src):
    out, pos, line = [], 0, 1
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m:
            raise STError(f"line {line}: unexpected character {src[pos]!r}")
        kind = m.lastgroup
        text = m.group()
        if kind not in ("ws", "cmt"):
            out.append((kind, text, line))
        line += text.count("\n")
        pos = m.end()
    out.append(("eof", "", line))
    return out


def parse_time(text):
    body = text.split("#", 1)[1]
    total = 0.0
    for num, unit in re.findall(r"(-?\d+(?:\.\d+)?)(ms|d|h|m|s)", body, flags=re.I):
        total += float(num) * UNITS[unit.lower()]
    return total


class Parser:
    def __init__(self, src):
        self.t = tokenize(src)
        self.i = 0
        self.decl = {}      # name -> (type, section, init)
        self.body = []
        self.name = None

    # helpers
    def peek(self):
        return self.t[self.i]

    def kw(self, word=None):
        k, v, _ = self.peek()
        return k == "id" and (word is None or v.upper() == word)

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect_kw(self, word):
        k, v, ln = self.take()
        if k != "id" or v.upper() != word:
            raise STError(f"line {ln}: expected {word}, found {v!r}")

    def expect_op(self, op):
        k, v, ln = self.take()
        if k != "op" or v != op:
            raise STError(f"line {ln}: expected {op!r}, found {v!r}")

    def is_op(self, op):
        k, v, _ = self.peek()
        return k == "op" and v == op

    def ident(self):
        k, v, ln = self.take()
        if k != "id" or v.upper() in KEYWORDS:
            raise STError(f"line {ln}: expected a name, found {v!r}")
        return v

    # program
    def parse(self):
        self.expect_kw("PROGRAM")
        self.name = self.ident()
        while self.kw() and self.peek()[1].upper() in ("VAR", "VAR_INPUT", "VAR_OUTPUT"):
            self.var_block()
        while not self.kw("END_PROGRAM"):
            if self.peek()[0] == "eof":
                raise STError("missing END_PROGRAM")
            self.body.append(self.statement())
        self.take()
        self.check(self.body)
        return self

    def var_block(self):
        section = self.take()[1].upper()
        if self.kw("CONSTANT"):
            self.take()
            section = "CONST"
        while not self.kw("END_VAR"):
            names = [self.ident()]
            while self.is_op(","):
                self.take()
                names.append(self.ident())
            self.expect_op(":")
            typ = self.ident().upper()
            if typ not in BASE_TYPES and typ not in FB_TYPES:
                raise STError(f"line {self.peek()[2]}: unsupported type {typ}")
            init = None
            if self.is_op(":="):
                self.take()
                if typ in FB_TYPES:
                    raise STError("function block instances cannot be initialised")
                init = self.expr()
            self.expect_op(";")
            for n in names:
                if n in self.decl:
                    raise STError(f"{n} declared twice")
                self.decl[n] = (typ, section, init)
        self.take()

    # statements
    def statement(self):
        k, v, ln = self.peek()
        if k != "id":
            raise STError(f"line {ln}: unexpected {v!r}")
        u = v.upper()
        if u == "IF":
            return self.if_stmt()
        if u == "CASE":
            return self.case_stmt()
        if u in ("FOR", "WHILE", "REPEAT", "RETURN", "EXIT"):
            raise STError(f"line {ln}: {v} is not supported")
        if u in KEYWORDS:
            raise STError(f"line {ln}: unexpected {v} (a block is probably not closed)")
        name = self.ident()
        if self.is_op("("):
            self.take()
            args = []
            while not self.is_op(")"):
                port = self.ident()
                self.expect_op(":=")
                args.append((port.upper(), self.expr()))
                if self.is_op(","):
                    self.take()
            self.take()
            self.expect_op(";")
            return ("call", name, args, ln)
        self.expect_op(":=")
        e = self.expr()
        self.expect_op(";")
        return ("assign", name, e, ln)

    def block(self, *stops):
        out = []
        while not (self.kw() and self.peek()[1].upper() in stops):
            if self.peek()[0] == "eof":
                raise STError("unterminated block")
            out.append(self.statement())
        return out

    def if_stmt(self):
        ln = self.take()[2]
        branches = []
        cond = self.expr()
        self.expect_kw("THEN")
        branches.append((cond, self.block("ELSIF", "ELSE", "END_IF")))
        other = []
        while True:
            w = self.take()[1].upper()
            if w == "ELSIF":
                cond = self.expr()
                self.expect_kw("THEN")
                branches.append((cond, self.block("ELSIF", "ELSE", "END_IF")))
            elif w == "ELSE":
                other = self.block("END_IF")
            else:
                break
        self.expect_op(";")
        return ("if", branches, other, ln)

    def case_stmt(self):
        ln = self.take()[2]
        sel = self.expr()
        self.expect_kw("OF")
        arms, other = [], []
        while not self.kw("END_CASE"):
            if self.kw("ELSE"):
                self.take()
                other = self.block("END_CASE")
                break
            vals = [int(self.take()[1])]
            while self.is_op(","):
                self.take()
                vals.append(int(self.take()[1]))
            self.expect_op(":")
            body = []
            while not (self.peek()[0] == "int" or self.kw("ELSE") or self.kw("END_CASE")):
                body.append(self.statement())
            arms.append((vals, body))
        self.expect_kw("END_CASE")
        self.expect_op(";")
        return ("case", sel, arms, other, ln)

    # expressions, lowest to highest precedence
    def expr(self):
        return self.binary(0)

    LEVELS = [("OR",), ("XOR",), ("AND", "&"), ("=", "<>"), ("<", ">", "<=", ">="), ("+", "-"), ("*", "/", "MOD")]

    def binary(self, lvl):
        if lvl == len(self.LEVELS):
            return self.unary()
        left = self.binary(lvl + 1)
        while True:
            k, v, _ = self.peek()
            op = v.upper() if k == "id" else v
            if op in self.LEVELS[lvl] and (k == "op" or v.upper() in self.LEVELS[lvl]):
                self.take()
                left = ("bin", op, left, self.binary(lvl + 1))
            else:
                return left

    def unary(self):
        k, v, ln = self.peek()
        if k == "id" and v.upper() == "NOT":
            self.take()
            return ("not", self.unary())
        if k == "op" and v == "-":
            self.take()
            return ("neg", self.unary())
        return self.primary()

    def primary(self):
        k, v, ln = self.take()
        if k == "int":
            return ("num", int(v))
        if k == "real":
            return ("num", float(v))
        if k == "time":
            return ("time", parse_time(v))
        if k == "op" and v == "(":
            e = self.expr()
            self.expect_op(")")
            return e
        if k == "id":
            u = v.upper()
            if u == "TRUE":
                return ("bool", True)
            if u == "FALSE":
                return ("bool", False)
            if u in KEYWORDS:
                raise STError(f"line {ln}: unexpected {v}")
            if u in FUNCS and self.is_op("("):
                self.take()
                args = [self.expr()]
                while self.is_op(","):
                    self.take()
                    args.append(self.expr())
                self.expect_op(")")
                if len(args) != FUNCS[u]:
                    raise STError(f"line {ln}: {u} takes {FUNCS[u]} arguments")
                return ("fn", u, args)
            if self.is_op("."):
                self.take()
                return ("member", v, self.ident().upper(), ln)
            return ("var", v, ln)
        raise STError(f"line {ln}: unexpected {v!r}")

    # semantic check: every name declared, member/port names valid, no writes to inputs or constants
    def check(self, stmts):
        def expr(e):
            t = e[0]
            if t == "var":
                if e[1] not in self.decl:
                    raise STError(f"line {e[2]}: {e[1]} is not declared")
                if self.decl[e[1]][0] in FB_TYPES:
                    raise STError(f"line {e[2]}: {e[1]} is a function block, use {e[1]}.Q")
            elif t == "member":
                d = self.decl.get(e[1])
                if d is None or d[0] not in FB_TYPES or e[2] not in FB_PORTS[d[0]][1]:
                    raise STError(f"line {e[3]}: {e[1]}.{e[2]} is not a valid output")
            elif t in ("bin",):
                expr(e[2]); expr(e[3])
            elif t in ("not", "neg"):
                expr(e[1])
            elif t == "fn":
                for a in e[2]:
                    expr(a)

        for s in stmts:
            if s[0] == "assign":
                d = self.decl.get(s[1])
                if d is None:
                    raise STError(f"line {s[3]}: {s[1]} is not declared")
                if d[1] in ("VAR_INPUT", "CONST"):
                    raise STError(f"line {s[3]}: cannot assign to {'input' if d[1] == 'VAR_INPUT' else 'constant'} {s[1]}")
                if d[0] in FB_TYPES:
                    raise STError(f"line {s[3]}: {s[1]} is a function block")
                expr(s[2])
            elif s[0] == "call":
                d = self.decl.get(s[1])
                if d is None or d[0] not in FB_TYPES:
                    raise STError(f"line {s[3]}: {s[1]} is not a function block instance")
                for port, e in s[2]:
                    if port not in FB_PORTS[d[0]][0]:
                        raise STError(f"line {s[3]}: {d[0]} has no input {port}")
                    expr(e)
            elif s[0] == "if":
                for c, b in s[1]:
                    expr(c); self.check(b)
                self.check(s[2])
            elif s[0] == "case":
                expr(s[1])
                for _, b in s[2]:
                    self.check(b)
                self.check(s[3])


class PLC:
    """Load a program and run scans. Inputs are written before each scan, outputs read after."""

    def __init__(self, source):
        p = Parser(source).parse()
        self.name, self.decl, self.body = p.name, p.decl, p.body
        self.v = {}
        self.fb = {}
        for n, (typ, sec, init) in self.decl.items():
            if typ in FB_TYPES:
                self.fb[n] = {"type": typ, "Q": False, "ET": 0.0, "prev": False, "Q1": False, "t": 0.0}
            else:
                self.v[n] = self._zero(typ) if init is None else self._coerce(typ, self.ev(init), n)
        self.time_ms = 0.0
        self.dt = 0.0

    @staticmethod
    def _zero(typ):
        return {"BOOL": False, "INT": 0, "DINT": 0, "REAL": 0.0, "TIME": 0.0}[typ]

    @staticmethod
    def _coerce(typ, x, name=""):
        if typ == "BOOL":
            if not isinstance(x, bool):
                raise STError(f"{name}: BOOL needs a boolean, got {x!r}")
            return x
        if isinstance(x, bool):
            raise STError(f"{name}: {typ} cannot take a boolean")
        if typ in ("INT", "DINT"):
            return int(x)
        return float(x)

    # evaluation
    def ev(self, e):
        t = e[0]
        if t in ("num", "bool", "time"):
            return e[1]
        if t == "var":
            return self.v[e[1]]
        if t == "member":
            return self.fb[e[1]][e[2]]
        if t == "not":
            x = self.ev(e[1])
            if not isinstance(x, bool):
                raise STError("NOT needs a BOOL")
            return not x
        if t == "neg":
            return -self.ev(e[1])
        if t == "fn":
            a = [self.ev(x) for x in e[2]]
            f = e[1]
            if f == "ABS":
                return abs(a[0])
            if f == "MIN":
                return min(a)
            if f == "MAX":
                return max(a)
            if f == "LIMIT":
                return max(a[0], min(a[1], a[2]))
            if f == "SEL":
                return a[2] if a[0] else a[1]
            return math.sqrt(a[0])
        _, op, l, r = e
        if op == "AND" or op == "&":
            a = self.ev(l); b = self.ev(r)
            return self._bool2(a, b, op) and (a and b)
        if op == "OR":
            a = self.ev(l); b = self.ev(r)
            return self._bool2(a, b, op) and (a or b)
        if op == "XOR":
            a = self.ev(l); b = self.ev(r)
            return self._bool2(a, b, op) and (a != b)
        a, b = self.ev(l), self.ev(r)
        if op == "=":
            return a == b
        if op == "<>":
            return a != b
        if op == "<":
            return a < b
        if op == ">":
            return a > b
        if op == "<=":
            return a <= b
        if op == ">=":
            return a >= b
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise STError("division by zero")
            if isinstance(a, int) and isinstance(b, int):
                return int(a / b)                         # IEC integer division truncates toward zero
            return a / b
        if op == "MOD":
            if b == 0:
                raise STError("MOD by zero")
            return a - b * int(a / b)
        raise STError(f"bad operator {op}")

    @staticmethod
    def _bool2(a, b, op):
        if not (isinstance(a, bool) and isinstance(b, bool)):
            raise STError(f"{op} needs BOOL operands")
        return True

    def run(self, stmts):
        for s in stmts:
            k = s[0]
            if k == "assign":
                self.v[s[1]] = self._coerce(self.decl[s[1]][0], self.ev(s[2]), s[1])
            elif k == "call":
                self.call_fb(s[1], {p: self.ev(e) for p, e in s[2]})
            elif k == "if":
                for cond, body in s[1]:
                    c = self.ev(cond)
                    if not isinstance(c, bool):
                        raise STError(f"line {s[3]}: IF needs a BOOL condition")
                    if c:
                        self.run(body)
                        break
                else:
                    self.run(s[2])
            else:
                x = int(self.ev(s[1]))
                for vals, body in s[2]:
                    if x in vals:
                        self.run(body)
                        break
                else:
                    self.run(s[3])

    def call_fb(self, name, a):
        f = self.fb[name]
        typ = f["type"]
        if typ in ("TON", "TOF", "TP"):
            IN, PT = bool(a.get("IN", False)), a.get("PT", 0.0)
            if typ == "TON":
                f["ET"] = min(f["ET"] + self.dt, PT) if IN else 0.0
                f["Q"] = IN and f["ET"] >= PT
            elif typ == "TOF":
                if IN:
                    f["ET"] = 0.0
                    f["Q"] = True
                else:
                    f["ET"] = min(f["ET"] + self.dt, PT) if f["Q"] else 0.0
                    f["Q"] = f["Q"] and f["ET"] < PT
            else:
                if not f["Q"] and IN and not f["prev"]:
                    f["Q"], f["ET"] = True, 0.0
                elif f["Q"]:
                    f["ET"] = min(f["ET"] + self.dt, PT)
                    if f["ET"] >= PT:
                        f["Q"] = False
                        f["ET"] = PT if IN else 0.0
                if not f["Q"] and not IN:
                    f["ET"] = 0.0
                f["prev"] = IN
        elif typ == "R_TRIG":
            c = bool(a.get("CLK", False))
            f["Q"], f["prev"] = c and not f["prev"], c
        elif typ == "F_TRIG":
            c = bool(a.get("CLK", False))
            f["Q"], f["prev"] = (not c) and f["prev"], c
        elif typ == "SR":       # set dominant
            f["Q1"] = bool(a.get("S1", False)) or (f["Q1"] and not bool(a.get("R", False)))
        else:                   # RS: reset dominant
            f["Q1"] = (bool(a.get("S", False)) or f["Q1"]) and not bool(a.get("R1", False))

    def scan(self, inputs, dt_ms):
        """One PLC scan of dt_ms milliseconds. Returns a dict of the VAR_OUTPUT values."""
        self.dt = float(dt_ms)
        for n, x in inputs.items():
            d = self.decl.get(n)
            if d is None or d[1] != "VAR_INPUT":
                raise STError(f"{n} is not an input")
            self.v[n] = self._coerce(d[0], x, n)
        self.run(self.body)
        self.time_ms += self.dt
        return {n: self.v[n] for n, d in self.decl.items() if d[1] == "VAR_OUTPUT"}
