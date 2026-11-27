"""小型描述文件的词法/语法分析器。

程序文件文法（缩进无关，# 起始为注释）：

    program 名称 {
        mode 名称 entry 入口函数 ;
        irq 等级 handler 处理函数 arrivals N per_period ;
        func 函数(frame N bytes, work N us) [: mode 名称 (, 名称)*] { 语句... }
    }

语句：
    call 函数 ;
    irq on  [high | low | all] ;
    irq off [high | low | all] ;
    region 名称 open 等级(, 等级)* { 语句... }

板卡文件文法：

    board 名称 {
        ram N bytes ;
        reserve 名称 [起, 止) ;
        period N us ;
        deadline 等级 N us ;
    }
"""
from __future__ import annotations

import re

from .model import (
    LEVELS,
    Board,
    Function,
    Isr,
    Mode,
    Program,
    Region,
    Reserved,
)

_TOKEN_RE = re.compile(
    r"""
      \#[^\n]*                 # 注释
    | [A-Za-z_][A-Za-z0-9_]*   # 标识符/关键字
    | 0x[0-9A-Fa-f]+           # 十六进制数
    | \d+                      # 十进制数
    | [{}()\[\],;:]            # 标点
    """,
    re.VERBOSE,
)


class ParseError(Exception):
    pass


def _tokenize(text: str) -> list[tuple[str, int]]:
    tokens: list[tuple[str, int]] = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.startswith("#"):
            continue
        line = text.count("\n", 0, m.start()) + 1
        tokens.append((tok, line))
    return tokens


def _int(tok: str) -> int:
    return int(tok, 16) if tok.lower().startswith("0x") else int(tok)


def _levels(spec: str) -> tuple[str, ...]:
    if spec == "all":
        return tuple(LEVELS)
    if spec not in LEVELS:
        raise ParseError(f"未知中断等级 {spec!r}，应为 high/low/all")
    return (spec,)


class _Parser:
    def __init__(self, text: str):
        self.tokens = _tokenize(text)
        self.pos = 0

    # -- 基础工具 ------------------------------------------------------------
    def _peek(self) -> str | None:
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def _line(self) -> int:
        return self.tokens[self.pos][1] if self.pos < len(self.tokens) else -1

    def _take(self, what: str | None = None) -> str:
        if self.pos >= len(self.tokens):
            raise ParseError(f"描述文件意外结束（期望 {what}）")
        tok, _ = self.tokens[self.pos]
        if what is not None and tok != what:
            raise ParseError(f"第 {self._line()} 行：期望 {what!r}，实际 {tok!r}")
        self.pos += 1
        return tok

    def _next(self, what: str) -> str:
        """消费一个标识符/数字 token，what 仅用于报错信息。"""
        if self.pos >= len(self.tokens):
            raise ParseError(f"描述文件意外结束（期望 {what}）")
        tok, _ = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _accept(self, what: str) -> bool:
        if self._peek() == what:
            self.pos += 1
            return True
        return False

    def _expect_kw(self, kw: str) -> None:
        tok = self._take(kw)
        if tok != kw:
            raise ParseError(f"第 {self._line()} 行：期望关键字 {kw!r}，实际 {tok!r}")

    def _number(self) -> int:
        tok = self._next('数字')
        try:
            return _int(tok)
        except ValueError:
            raise ParseError(f"第 {self._line()} 行：{tok!r} 不是整数") from None

    # -- 程序文件 ------------------------------------------------------------
    def parse_program(self, text: str, path: str | None) -> Program:
        self._expect_kw("program")
        name = self._next('程序名')
        self._take('{')
        functions: dict[str, Function] = {}
        isrs: dict[str, Isr] = {}
        modes: dict[str, Mode] = {}
        while self._peek() != "}":
            kw = self._take()
            if kw == "mode":
                mname = self._next('模式名')
                self._expect_kw("entry")
                entry = self._next('入口函数名')
                self._take(';')
                if mname in modes:
                    raise ParseError(f"第 {self._line()} 行：模式 {mname} 重复定义")
                modes[mname] = Mode(mname, entry)
            elif kw == "irq":
                level = self._next('中断等级')
                if level not in LEVELS:
                    raise ParseError(f"第 {self._line()} 行：中断等级只能是 high/low")
                if level in isrs:
                    raise ParseError(f"第 {self._line()} 行：{level} 中断重复定义")
                self._expect_kw("handler")
                handler = self._next('处理函数名')
                self._expect_kw("arrivals")
                n = self._number()
                if n < 1:
                    raise ParseError("arrivals 必须 >= 1")
                self._expect_kw("per_period")
                self._take(';')
                isrs[level] = Isr(level, handler, n)
            elif kw == "func":
                fn = self._parse_func()
                if fn.name in functions:
                    raise ParseError(f"第 {self._line()} 行：函数 {fn.name} 重复定义")
                functions[fn.name] = fn
            else:
                raise ParseError(f"第 {self._line()} 行：program 块内出现意外标记 {kw!r}")
        self._take('}')
        if self._peek() is not None:
            raise ParseError(f"第 {self._line()} 行：program 块后仍有多余内容")

        prog = Program(name, functions, isrs, modes, source_path=path, source_text=text)
        self._validate_program(prog)
        return prog

    def _parse_func(self) -> Function:
        name = self._next('函数名')
        self._take('(')
        frame = work = None
        while True:
            key = self._take()
            n = self._number()
            unit = self._take()
            if key == "frame":
                if unit != "bytes":
                    raise ParseError(f"第 {self._line()} 行：frame 单位应为 bytes")
                frame = n
            elif key == "work":
                if unit != "us":
                    raise ParseError(f"第 {self._line()} 行：work 单位应为 us")
                work = n
            else:
                raise ParseError(f"第 {self._line()} 行：函数头只允许 frame/work，实际 {key!r}")
            if self._accept(","):
                continue
            break
        self._take(')')
        func_modes: set[str] = set()
        if self._accept(":"):
            self._expect_kw("mode")
            func_modes.add(self._next('模式名'))
            while self._accept(","):
                func_modes.add(self._next('模式名'))
        self._take('{')
        body = self._parse_stmt_block()
        self._take('}')
        assert frame is not None and work is not None
        return Function(name, frame, work, body, func_modes)

    def _parse_stmt_block(self) -> list:
        stmts: list = []
        while self._peek() != "}":
            kw = self._take()
            if kw == "call":
                target = self._next('被调用函数名')
                self._take(';')
                stmts.append(("call", target))
            elif kw == "irq":
                action = self._take()
                if action not in ("on", "off"):
                    raise ParseError(f"第 {self._line()} 行：irq 后应为 on/off")
                spec = self._take()
                self._take(';')
                stmts.append(("irq", action, _levels(spec)))
            elif kw == "region":
                rname = self._next('区间名')
                self._expect_kw("open")
                mask: list[str] = []
                spec = self._take()
                mask.extend(_levels(spec))
                while self._accept(","):
                    mask.extend(_levels(self._take()))
                self._take('{')
                rbody = self._parse_stmt_block()
                self._take('}')
                stmts.append(("region", Region(rname, tuple(dict.fromkeys(mask)), rbody)))
            else:
                raise ParseError(f"第 {self._line()} 行：意外的语句标记 {kw!r}")
        return stmts

    def _validate_program(self, prog: Program) -> None:
        if not prog.modes:
            raise ParseError("程序至少需要一个 mode")
        for level in LEVELS:
            if level not in prog.isrs:
                raise ParseError(f"缺少 {level} 级中断声明（irq ... handler ...）")
        all_modes = set(prog.modes)
        for fn in prog.functions.values():
            unknown = fn.modes - all_modes
            if unknown:
                raise ParseError(f"函数 {fn.name} 引用了未声明的模式 {sorted(unknown)}")
        for mode in prog.modes.values():
            if mode.entry not in prog.functions:
                raise ParseError(f"模式 {mode.name} 的入口 {mode.entry} 未定义")
        handler_names = {isr.handler for isr in prog.isrs.values()}
        for isr in prog.isrs.values():
            if isr.handler not in prog.functions:
                raise ParseError(f"{isr.level} 中断处理函数 {isr.handler} 未定义")
        for fn in prog.functions.values():
            fn.is_handler = fn.name in handler_names
            if fn.is_handler:
                if fn.modes:
                    raise ParseError(f"中断处理函数 {fn.name} 不能限定模式，两级中断在任何模式都可触发")
                for lvl, isr in prog.isrs.items():
                    if isr.handler == fn.name:
                        fn.handler_level = lvl
            self._validate_calls(prog, fn, fn.body, handler_names)
        # 可达性：声明属于某模式的函数，必须能从该模式入口到达
        for mode in prog.modes.values():
            seen: set[str] = set()
            stack = [mode.entry]
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(self._calls_in(prog.functions[cur].body))
            for fn in prog.functions.values():
                if fn.is_handler or mode.name not in fn.modes:
                    continue
                if fn.name not in seen:
                    raise ParseError(
                        f"模式 {mode.name} 中声明的函数 {fn.name} "
                        f"从入口 {mode.entry} 不可达"
                    )

    def _validate_calls(self, prog: Program, owner: Function, stmts: list,
                        handler_names: set[str]) -> None:
        for st in stmts:
            kind = st[0]
            if kind == "call":
                target = st[1]
                if target not in prog.functions:
                    raise ParseError(f"函数 {owner.name} 调用了未定义的函数 {target}")
                if target in handler_names:
                    raise ParseError(
                        f"函数 {owner.name} 不能直接 call 中断处理函数 {target}：中断只能由硬件进入"
                    )
                tfn = prog.functions[target]
                if owner.is_handler and tfn.modes:
                    raise ParseError(
                        f"处理函数 {owner.name} 不能调用模式专用函数 {target}（处理函数跨模式运行）"
                    )
                if tfn.is_handler:
                    raise ParseError(f"函数 {owner.name} 不能调用处理函数 {target}")
            elif kind == "region":
                self._validate_calls(prog, owner, st[1].body, handler_names)

    @staticmethod
    def _calls_in(stmts: list) -> list[str]:
        out: list[str] = []
        for st in stmts:
            if st[0] == "call":
                out.append(st[1])
            elif st[0] == "region":
                out.extend(_Parser._calls_in(st[1].body))
        return out

    # -- 板卡文件 ------------------------------------------------------------
    def _parse_board(self, text: str, path: str) -> Board:
        self._expect_kw("board")
        name = self._next('板卡名')
        self._take('{')
        ram_total = None
        reserved: list[Reserved] = []
        period = None
        deadlines: dict[str, int] = {}
        while self._peek() != "}":
            kw = self._take()
            if kw == "ram":
                ram_total = self._number()
                self._expect_kw("bytes")
                self._take(';')
            elif kw == "reserve":
                rname = self._next('保留区名')
                self._take('[')
                start = self._number()
                self._take(',')
                end = self._number()
                self._take(')')
                self._take(';')
                if end <= start:
                    raise ParseError(f"保留区 {rname} 区间为空或反向：[{start}, {end})")
                reserved.append(Reserved(rname, start, end - start))
            elif kw == "period":
                period = self._number()
                self._expect_kw("us")
                self._take(';')
            elif kw == "deadline":
                level = self._take()
                if level not in LEVELS:
                    raise ParseError("deadline 等级只能是 high/low")
                deadlines[level] = self._number()
                self._expect_kw("us")
                self._take(';')
            else:
                raise ParseError(f"第 {self._line()} 行：board 块内出现意外标记 {kw!r}")
        self._take('}')
        if ram_total is None or period is None:
            raise ParseError("板卡必须声明 ram 与 period")
        for level in LEVELS:
            if level not in deadlines:
                raise ParseError(f"板卡缺少 {level} 级 deadline")
        board = Board(name, ram_total, reserved, period, deadlines,
                      source_path=path, source_text=text)
        self._validate_board(board)
        return board

    def _validate_board(self, board: Board) -> None:
        for r in board.reserved:
            if r.start < 0 or r.end > board.ram_total:
                raise ParseError(f"保留区 {r.name} [{r.start},{r.end}) 超出 RAM 0..{board.ram_total}")
        ordered = sorted(board.reserved, key=lambda r: r.start)
        for a, b in zip(ordered, ordered[1:]):
            if a.end > b.start:
                raise ParseError(f"保留区 {a.name} 与 {b.name} 重叠")


def parse_program_text(text: str, path: str | None = None) -> Program:
    return _Parser(text).parse_program(text, path)


def parse_board_text(text: str, path: str | None = None) -> Board:
    boards = parse_boards_text(text, path)
    if len(boards) != 1:
        raise ParseError(f"文件含 {len(boards)} 块板卡，请用 parse_boards_text 按名取用")
    return boards[0]


def parse_boards_text(text: str, path: str | None = None) -> list[Board]:
    parser = _Parser(text)
    boards: list[Board] = []
    while parser._peek() is not None:
        boards.append(parser._parse_board(text, path))
    return boards


def parse_program_file(path: str) -> Program:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return parse_program_text(text, path)


def parse_board_file(path: str, name: str | None = None) -> Board:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    boards = parse_boards_text(text, path)
    if name is None:
        if len(boards) != 1:
            raise ParseError(f"{path} 含多块板卡 {[b.name for b in boards]}，请指定板卡名")
        return boards[0]
    for board in boards:
        if board.name == name:
            return board
    raise ParseError(f"{path} 中没有板卡 {name!r}，可选 {[b.name for b in boards]}")
