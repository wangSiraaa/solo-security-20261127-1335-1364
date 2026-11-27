"""端到端故事线与验收断言。

故事（对应题目要求）：
0. 造两个显示名完全相同、字节不同的小包；名字与简介都由制包者填写，
   且都包含“已验证”三个普通字。只有 alpha 的字节经校验席核验绑定。
1. 基线：从画面/朗读稿确认归属清楚；核准短包一次选对并真实安装；
   同名未核包即使名字带“已验证”也被拒绝。
2. 仅更换 alpha 的字节：旧核验不得沿用（stale），安装被阻止。
3. 还原字节：恢复 verified；窗口收窄一格、交换列表顺序；
   依据实际朗读稿（而非光标原行号）选出有依据的一项并安装。
4. 用安装目录字节与操作回单核对所选对象。
另含小字库双向约定的单元检查（数字保序、括号归属、窄窗续行标注）。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from pick import bidi
from pick.digest import bytes_sha256, file_sha256
from pick.installer import InstallError, install, verify_receipt
from pick.model import Model
from pick.renderer import render
from pick.scriptmode import parse_speech_verdicts
from pick.verifier import run as run_verifier
from pick.verifier import write_roster

STATE = Path(__file__).resolve().parent.parent / "state"
TRUST = STATE / "trust"
SELECTOR = STATE / "selector"
WIDTH_WIDE = 50
WIDTH_NARROW = WIDTH_WIDE - 1

DISPLAY_NAME = "已验证 安装器 2026"   # 两家起的同名，含“已验证”普通字
ALPHA_BYTES = b"alpha-studio payload build 0001\n"
BETA_BYTES = b"beta-studio different payload build 0002\n"
ALPHA_TAMPERED = b"alpha-studio payload TAMPERED build 9999\n"

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, bool, str]] = []
alpha_hash_holder: list[str] = [""]


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"[{PASS if cond else FAIL}] {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def reset_dirs() -> None:
    for d in (STATE,):
        if d.exists():
            shutil.rmtree(d)
    (TRUST / "inbox" / "alpha-studio").mkdir(parents=True)
    (TRUST / "inbox" / "beta-studio").mkdir(parents=True)
    (SELECTOR / "packages").mkdir(parents=True)


def make_package(where: Path, filename: str, payload: bytes,
                 display_name: str, summary: str, claimed: str) -> Path:
    pkg = where / filename
    pkg.write_bytes(payload)
    meta = {
        "schema": "package-meta/v1",
        "package_file": filename,
        "display_name": display_name,
        "summary": summary,
        "claimed_publisher": claimed,
    }
    meta_path = where / f"{filename.removesuffix('.pkg')}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return pkg


def stage_initial() -> dict:
    reset_dirs()
    # 校验席名册：只有 alpha 是受信发行方；beta 根本不在名册里。
    write_roster(TRUST, [
        ("alpha-studio", "阿尔法工坊"),
    ])
    # 受信 inbox：本席实际收到的包。
    make_package(TRUST / "inbox" / "alpha-studio", "alpha.pkg",
                 ALPHA_BYTES, DISPLAY_NAME,
                 "官方 已验证 版（数字 2026 保序）", "阿尔法工坊官方")
    make_package(TRUST / "inbox" / "beta-studio", "beta.pkg",
                 BETA_BYTES, DISPLAY_NAME,
                 "同名 已验证 福利包（数字 2026 保序）", "贝塔小站")
    atts = run_verifier(TRUST)

    # 选择器从另一个目录读：只搬包、元数据、核验产物与信任锚，
    # 校验席私钥环 keyring.json 不离开受信目录。
    shutil.copytree(TRUST / "attestations", SELECTOR / "attestations")
    shutil.copy(TRUST / "keyring.pub.json", SELECTOR / "keyring.pub.json")
    make_package(SELECTOR / "packages", "alpha.pkg",
                 ALPHA_BYTES, DISPLAY_NAME,
                 "官方 已验证 版（数字 2026 保序）", "阿尔法工坊官方")
    make_package(SELECTOR / "packages", "beta.pkg",
                 BETA_BYTES, DISPLAY_NAME,
                 "同名 已验证 福利包（数字 2026 保序）", "贝塔小站")
    (SELECTOR / "order.conf").write_text("alpha\nbeta\n", encoding="utf-8")
    return {"attestations": len(atts)}


def assert_no_private_key_leak() -> None:
    leaked = list(SELECTOR.rglob("keyring.json"))
    check("校验席私钥环不进入选择器目录", not leaked,
          f"选择器目录中发现 {leaked}" if leaked else "仅存在信任锚 keyring.pub.json")


def entry(model: Model, eid: str):
    e = model.entry_by_id(eid)
    assert e is not None
    return e


def baseline() -> dict:
    print("\n=== 阶段 1：基线，同名两包，一个有核验依据 ===")
    model = Model.open(SELECTOR)
    frame = render(model, WIDTH_WIDE, 24)
    alpha, beta = entry(model, "alpha"), entry(model, "beta")

    check("两个包显示名相同但字节哈希不同",
          alpha.display_name == beta.display_name == DISPLAY_NAME
          and alpha.sha256 != beta.sha256,
          f"alpha={alpha.sha256[:12]}… beta={beta.sha256[:12]}…")
    check("alpha 校验席结论为 verified", alpha.verification.status == "verified",
          f"发行方 {alpha.verification.publisher_display_name} / {alpha.verification.publisher_id}")
    check("beta 没有核验记录，结论为 unverified",
          beta.verification.status == "unverified", beta.verification.reason)
    check("绑定的是发行方 id 而非包名",
          alpha.verification.publisher_id == "alpha-studio")

    visual = frame.visual()
    check("固定核验区只出现校验席结论，不出现制包者文字",
          "校验席结论：已验证" in visual
          and "固定核验区" in visual
          and "福利包" not in "\n".join(
              line for line in visual.splitlines() if "固定核验区" in line or
              ("校验席结论" in line and "条目列表" not in line)))
    # 列表里制包者文字被来源括号包住，且与 gutter 信任标记分区。
    list_section = visual.split("条目列表", 1)[1]
    check("列表中的“已验证”被包在发行方来源括号 ｢｣ 内",
          "｢" in list_section and "｣" in list_section
          and "[V]" in list_section and "[?]" in list_section)

    verdicts = parse_speech_verdicts(frame.speech())
    check("朗读稿中结论句均以“校验席结论”起头且状态正确",
          {v["status"] for v in verdicts} == {"verified", "unverified"}
          and all(v["raw"].startswith("校验席结论：") for v in verdicts))
    check("朗读稿明确警告名称里的“已验证”只是普通文字",
          any("普通文字" in s for s in frame.speech_lines))
    check("朗读稿依据哈希与模型一致",
          all(v["basis_hash"] in (alpha.sha256, beta.sha256) for v in verdicts))

    # 核准短包一次选对并真实安装。
    install_root = SELECTOR / "installed-baseline"
    model.cursor = [e.entry_id for e in model.entries].index("alpha")
    frame = render(model, WIDTH_WIDE, 24)
    result = install(model, "alpha", install_root, frame)
    on_disk = (install_root / "alpha.pkg").read_bytes()
    check("alpha 真实装入空目录且字节逐字节相同",
          on_disk == ALPHA_BYTES
          and file_sha256(install_root / "alpha.pkg") == alpha.sha256)
    check("回单已生成并记录画面/朗读来源",
          result.receipt_path.exists()
          and result.receipt["what_user_heard"]["speech_lines"]
          and result.receipt["what_user_saw"]["visual_lines"])
    check("回单记录的信任依据是签名记录而非行号",
          result.receipt["selection_basis"]["attestation_signature"]
          and result.receipt["selection_basis"]["bound_publisher_id"] == "alpha-studio")
    check("回单把制包者自填字段明确标注为不可信",
          result.receipt["package_self_described_fields"]["trusted"] is False)

    # 同名未核包：名字里的“已验证”不能给它背书。
    blocked = None
    try:
        model.cursor = [e.entry_id for e in model.entries].index("beta")
        install(model, "beta", SELECTOR / "installed-should-fail",
                render(model, WIDTH_WIDE, 24))
    except InstallError as exc:
        blocked = str(exc)
    check("beta 安装被阻止，名称中的“已验证”不构成背书",
          blocked is not None and "未核验" in blocked, blocked or "")
    check("失败时没有产生任何安装目录",
          not (SELECTOR / "installed-should-fail").exists())

    # 回单核对。
    problems = verify_receipt(result.receipt_path, install_root)
    check("回单事后核对通过", not problems, "; ".join(problems))
    return {"receipt": result.receipt_path, "alpha_hash": alpha.sha256}


def tamper_story() -> None:
    print("\n=== 阶段 2：仅更换 alpha 的字节，旧核验不得沿用 ===")
    pkg = SELECTOR / "packages" / "alpha.pkg"
    old_hash = file_sha256(pkg)
    pkg.write_bytes(ALPHA_TAMPERED)
    new_hash = file_sha256(pkg)
    check("字节确实被更换（哈希变化）", old_hash != new_hash,
          f"{old_hash[:12]}… -> {new_hash[:12]}…")

    model = Model.open(SELECTOR)
    alpha = entry(model, "alpha")
    check("更换字节后状态为 stale（旧记录不得沿用）",
          alpha.verification.status == "stale", alpha.verification.reason)
    check("stale 原因明确点出新旧哈希不一致",
          old_hash[:12] in alpha.verification.reason
          and new_hash[:12] in alpha.verification.reason)

    frame = render(model, WIDTH_WIDE, 24)
    verdicts = {v["basis_hash"]: v for v in parse_speech_verdicts(frame.speech())}
    check("朗读稿对换过字节的包播报“记录失效”，不播“已验证”",
          verdicts[new_hash]["verdict"] == "记录失效"
          and verdicts[new_hash]["status"] == "stale")

    blocked = None
    try:
        install(model, "alpha", SELECTOR / "installed-tampered",
                render(model, WIDTH_WIDE, 24))
    except InstallError as exc:
        blocked = str(exc)
    check("换字节后的 alpha 安装被阻止",
          blocked is not None and "记录失效" in blocked, blocked or "")

    # 旧核验记录文件仍在，证明不是“记录丢了”，而是哈希对不上。
    old_att = SELECTOR / "attestations" / f"{old_hash}.json"
    check("旧核验记录仍在但无法为新字节背书", old_att.exists())


def restore_and_narrow_story(alpha_hash: str) -> None:
    print("\n=== 阶段 3：还原字节，窗口收窄一格并交换顺序，依据朗读选择 ===")
    pkg = SELECTOR / "packages" / "alpha.pkg"
    pkg.write_bytes(ALPHA_BYTES)
    check("字节还原后哈希恢复", file_sha256(pkg) == alpha_hash)

    # 交换列表顺序：现在 beta 排在第一行，alpha 排在第二行。
    (SELECTOR / "order.conf").write_text("beta\nalpha\n", encoding="utf-8")

    model = Model.open(SELECTOR)
    order = [e.entry_id for e in model.entries]
    check("列表顺序已交换", order == ["beta", "alpha"], str(order))

    frame_wide = render(model, WIDTH_WIDE, 24)
    frame_narrow = render(model, WIDTH_NARROW, 24)
    check("窗口恰好收窄一格",
          frame_wide.width == WIDTH_WIDE and frame_narrow.width == WIDTH_NARROW)

    # 窄窗口下出现续行，且续行带选择器的续行标记、不带信任标记。
    narrow_visual = frame_narrow.visual()
    check("窄窗口触发换行并标注续行归属",
          "↳" in narrow_visual)
    cont_lines = [ln for ln in narrow_visual.splitlines() if ln.lstrip().startswith("↳")]
    check("续行行首不携带任何信任标记，防止折行被误读为结论",
          cont_lines and all("[V]" not in ln and "[?]" not in ln and "[X]" not in ln
                             for ln in cont_lines))
    check("所有画面行都不超出窄窗口宽度（无截断）",
          all(bidi.text_width(ln.rstrip()) <= WIDTH_NARROW
              for ln in narrow_visual.splitlines()))
    # 窄窗口下固定核验区仍完整可读：当前聚焦第一项 beta → 未核验且说明阻止安装。
    check("窄窗口固定核验区对聚焦的未核包给出结论与阻止说明",
          "校验席结论：未核验" in narrow_visual
          and "安装已被阻止" in narrow_visual)
    # 把聚焦移到 alpha，固定核验区必须给出绑定哈希，且与朗读依据是同一个值。
    model.cursor = 1
    narrow_alpha = render(model, WIDTH_NARROW, 24)
    joined_panel = "".join(narrow_alpha.visual().splitlines())
    check("窄窗口聚焦已核包时固定核验区给出 sha256 绑定（允许跨行折行）",
          "校验席结论：已验证" in narrow_alpha.visual()
          and f"sha256={alpha_hash}" in joined_panel
          and "受信发行方：阿尔法工坊" in narrow_alpha.visual())
    model.cursor = 0

    # 关键：依据“实际朗读稿”选择，而不是记住 alpha 原来在第几行。
    verdicts = parse_speech_verdicts(frame_narrow.speech())
    chosen_index = None
    for v in verdicts:
        if v["status"] == "verified" and v["basis_hash"] == alpha_hash:
            chosen_index = v["item_index"]
    check("能从朗读稿中按结论+哈希选出有依据的条目",
          chosen_index is not None, f"朗读条目序号 {chosen_index}")
    check("被选中的不是原来的第一行（顺序已交换，行号不可作依据）",
          chosen_index == 2, f"实际朗读序号 {chosen_index}")
    chosen_id = model.entries[chosen_index - 1].entry_id
    check("朗读选出的条目确为 alpha", chosen_id == "alpha")

    # 真实安装并核对。
    install_root = SELECTOR / "installed-narrow"
    model.cursor = chosen_index - 1
    result = install(model, chosen_id, install_root,
                     render(model, WIDTH_NARROW, 24))
    installed_file = install_root / "alpha.pkg"
    check("窄窗+换序后安装的字节仍是 alpha 原始字节",
          installed_file.read_bytes() == ALPHA_BYTES
          and file_sha256(installed_file) == alpha_hash)
    check("安装目录中只有这一个文件",
          [p.name for p in install_root.iterdir()] == ["alpha.pkg"])
    problems = verify_receipt(result.receipt_path, install_root)
    check("新回单事后核对通过", not problems, "; ".join(problems))
    check("新回单保存了窄窗口画面与当时朗读稿",
          result.receipt["what_user_saw"]["frame_width"] == WIDTH_NARROW
          and any("校验席结论：已验证" in s
                  for s in result.receipt["what_user_heard"]["speech_lines"]))


def bidi_unit_checks() -> None:
    print("\n=== 单元：小字库双向约定 ===")
    name = "已验证 安装器 2026"
    vis = bidi.visual(name, "R")
    check("R 组视觉整体反转，数字片段 2026 内部保持顺序",
          "2026" in vis and vis != name, f"视觉形态：{vis}")
    check("朗读形态不反转且数字顺序不变",
          bidi.spoken(name, "R").endswith(name))
    check("L 组选择器文字两个方向都不反转",
          bidi.visual("[V]", "L") == "[V]" and bidi.spoken("[V]", "L") == "[V]")

    seq = (bidi.cells("[V]", "L", "selector")
           + bidi.cells("｢", "L", "selector")
           + bidi.cells("已验证 2026", "R", "publisher")
           + bidi.cells("｣", "L", "selector"))
    wrapped = bidi.wrap_cells(seq, 8)
    check("窄窗口按显示宽度换行且每个格子都带来源标注",
          len(wrapped) >= 2
          and all(all(c.source in ("selector", "publisher") for c in line)
                  for line in wrapped))
    pub_cells = [c for c in seq if c.source == "publisher"]
    check("归属随格子走：发行方格子的来源标注始终是 publisher",
          all(c.source == "publisher" and c.direction in ("R", "D") for c in pub_cells))
    check("括号 ｢｣ 归属选择器而非发行方",
          all(c.source == "selector" for c in seq if c.ch in "｢｣[]"))

    # 全角宽度：换行不会拆开一个全角字符。
    wide = bidi.cells("安装器安装器", "R", "publisher")
    lines = bidi.wrap_cells(wide, 5)
    check("换行不把全角字符挤出边界",
          all(sum(c.width for c in line) <= 5 for line in lines))


def tampered_attestation_check() -> None:
    print("\n=== 单元：核验记录被篡改时验签失败 ===")
    model = Model.open(SELECTOR)
    alpha = entry(model, "alpha")
    att = SELECTOR / "attestations" / f"{alpha.sha256}.json"
    raw = json.loads(att.read_text(encoding="utf-8"))
    backup = json.dumps(raw, ensure_ascii=False)
    raw["publisher_display_name"] = "冒名顶替者"
    att.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    try:
        reloaded = Model.open(SELECTOR)
        a = entry(reloaded, "alpha")
        check("记录被改字后验签失败，不能冒充受信发行方",
              a.verification.status == "unverified"
              and "签名" in a.verification.reason, a.verification.reason)
    finally:
        att.write_text(backup, encoding="utf-8")
    restored = Model.open(SELECTOR)
    check("恢复记录后重新核验通过",
          entry(restored, "alpha").verification.status == "verified")


def tui_state_machine_check() -> None:
    print("\n=== 单元：键盘状态机（无 PTY 环境下直接驱动 handle_key）===")
    import curses

    from pick.tui import handle_key

    model = Model.open(SELECTOR)  # 当前 order.conf 为 beta 在前
    start_focus = model.focus().entry_id
    check("键盘测试起点聚焦第一项 beta", start_focus == "beta")

    # 在未核包上按回车：必须被拒绝，且无安装目录、无回单。
    root = SELECTOR / "installed-tui"
    outcome = handle_key(model, 10, WIDTH_NARROW, 24, root)
    check("未核包上按回车被拒绝",
          outcome.installed is None and "安装被拒绝" in outcome.message,
          outcome.message)
    check("拒绝安装时不落盘、不写回单",
          not root.exists()
          and not list((SELECTOR / "receipts").glob("*beta*")))

    # 按 j 下移到 alpha（模拟一次按键移动），读屏日志应播报已验证结论。
    moved = handle_key(model, ord("j"), WIDTH_NARROW, 24, root)
    check("按 j 后聚焦移到 alpha", model.focus().entry_id == "alpha")
    check("移动即触发读屏播报，且结论为已验证",
          moved.speech is not None
          and any("校验席结论：已验证" in s for s in moved.speech))

    # 再按回车：真实安装。
    ok = handle_key(model, 10, WIDTH_NARROW, 24, root)
    check("已核包上按回车安装成功",
          ok.installed is not None and ok.installed["entry_id"] == "alpha")
    installed = root / "alpha.pkg"
    check("TUI 路径落盘字节与核验哈希一致",
          installed.exists() and installed.read_bytes() == ALPHA_BYTES
          and file_sha256(installed) == alpha_hash_holder[0])

    # 读屏日志文件可回放，且每句结论都带“校验席结论”来源前缀。
    log = (SELECTOR / "speech.log").read_text(encoding="utf-8")
    check("speech.log 记录了播报流且结论句都带来源前缀",
          "校验席结论：已验证" in log
          and all("校验席结论" in line for line in log.splitlines()
                  if "结论" in line))

    # q 退出。
    quit_outcome = handle_key(model, ord("q"), WIDTH_NARROW, 24, root)
    check("按 q 请求退出", quit_outcome.quit_requested)


def main() -> int:
    info = stage_initial()
    check("校验席只为受信名册中的 alpha 签发（beta 不在名册）",
          info["attestations"] == 1, f"签发数 {info['attestations']}")
    assert_no_private_key_leak()
    bidi_unit_checks()
    baseline_info = baseline()
    alpha_hash_holder[0] = baseline_info["alpha_hash"]
    tamper_story()
    restore_and_narrow_story(baseline_info["alpha_hash"])
    tampered_attestation_check()
    tui_state_machine_check()

    total = len(results)
    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 60)
    print(f"验收 {total - len(failed)}/{total} 通过")
    if failed:
        for name, _, detail in failed:
            print(f"  失败：{name} {detail}")
        return 1
    print("全部通过：信任只来自校验席的字节-发行方绑定，")
    print("同名文字、换行、换序与读屏次序都没有改变归属。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
