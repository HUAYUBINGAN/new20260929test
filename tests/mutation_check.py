"""mutation_check.py —— 验证「测试套件本身是有效的」

一个不会失败的测试等于没有测试。这个脚本故意把源文件改坏（变异），
确认对应的测试**确实报错**，并且原文件**不会被误报**。

用法：  python tests/mutation_check.py
退出码：0 = 测试套件有效；1 = 有变异没被抓到（说明测试有盲区）
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_all import ROOT, find_node          # noqa: E402

TESTS = ROOT / "tests"
UI = ROOT / "ui" / "index.html"
BAT = ROOT / "启动.bat"

# ------------------------------------------------------------------ 变异定义
# (说明, 原片段, 变异后片段, 期望被测出的关键字)
UI_MUTATIONS = [
    (
        "M1 去掉「打开选择框前清空 value」",
        "  drop.onclick = ()=>{ $('file').value = ''; $('file').click(); };",
        "  drop.onclick = ()=>{ $('file').click(); };",
        "打开选择框前先清空 value",
    ),
    (
        "M2 抽音轨阶段不再清掉行内 width",
        "    bar.classList.add('indet');\n    inner.style.width = '';",
        "    bar.classList.add('indet');\n    inner.style.width = '100%';",
        "清掉行内 width",
    ),
    (
        "M3 取消后进度不归零",
        "const pct = s.stage === 'done' ? 100 : (s.stage === 'cancelled' ? 0 : s.progress);",
        "const pct = s.stage === 'done' ? 100 : s.progress;",
        "进度归零",
    ),
]

# 说明：这里按字节位置精确替换，避免误伤别处
BAT_GUARD = '  if not "%PYEXE:WindowsApps=%"=="%PYEXE%" set "PYEXE="\r\n'
BAT_PYLINE = '  for %%P in (py.exe) do if exist "%%~$PATH:P" set "PYLAUNCH=%%~$PATH:P"\r\n)\r\n'


def bat_mutations(base):
    return [
        ("M1 路径改回正斜杠",
         base.replace(r"C:\Users\Admin\.workbuddy", "C:/Users/Admin/.workbuddy"),
         "正斜杠路径"),
        ("M2 去掉 WindowsApps 占位符防护",
         base.replace(BAT_GUARD, "", 1),
         "WindowsApps"),
        ("M3 混入裸 LF",
         base.replace("\r\n", "\n", 5),
         "裸 LF"),
        ("M4 for 变量写成单百分号",
         base.replace("%%P", "%P"),
         "单百分号"),
        ("M5 括号块不闭合",
         base.replace(BAT_PYLINE, BAT_PYLINE.rsplit(")", 1)[0], 1),
         "括号"),
        ("M6 括号块内的注释含右括号",
         base.replace(BAT_PYLINE, "  rem 注意 (这里混进一个括号\r\n" + BAT_PYLINE, 1),
         "元字符"),
    ]


# ------------------------------------------------------------------ 执行
def run_case(node, name, mutated_bytes, kind, expect, tmpdir):
    """kind: 'ui' 或 'bat'。返回 True 表示该变异被抓到。"""
    if kind == "ui":
        target = tmpdir / "index.html"
        target.write_bytes(mutated_bytes)
        cmd = [node, str(TESTS / "ui_smoke.js")]
        env_key, env_val = "UI_HTML", str(target)
    else:
        target = tmpdir / "启动.bat"
        target.write_bytes(mutated_bytes)
        cmd = [sys.executable, str(TESTS / "bat_lint.py")]
        env_key, env_val = "BAT_PATH", str(target)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", env_key: env_val}
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    caught = r.returncode == 1 and expect in r.stdout
    hits = [l.strip() for l in r.stdout.splitlines() if "[FAIL]" in l]
    mark = "OK  " if caught else "!!  "
    print(f"  [{mark}] {name}")
    print(f"          被测出 {len(hits)} 项失败；关键项「{expect}」"
          f"{'已命中' if caught else '未命中'}")
    if not caught:
        print("          输出片段：", r.stdout.strip()[-400:].replace("\n", " | "))
    return caught


def main():
    node = find_node()
    if not node:
        print("[FAIL] 找不到 Node.js，无法跑界面变异测试")
        return 1
    for f in (UI, BAT):
        if not f.exists():
            print(f"[FAIL] 找不到 {f}")
            return 1

    ui_base = UI.read_text(encoding="utf-8")
    bat_base = BAT.read_bytes().decode("gbk")

    print("=" * 64)
    print("变异测试 —— 确认测试套件真的会报错")
    print("=" * 64)

    all_ok = True
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="wbmut_"))
    try:
        print("\n[界面冒烟测试 · ui_smoke.js]")
        for name, old, new, expect in UI_MUTATIONS:
            if old not in ui_base:
                print(f"  [!!  ] {name} —— 找不到目标代码（界面已改动？请更新变异定义）")
                all_ok = False
                continue
            mutated = ui_base.replace(old, new, 1)
            sub = tmpdir / "ui"
            sub.mkdir(exist_ok=True)
            all_ok &= run_case(node, name, mutated.encode("utf-8"), "ui", expect, sub)

        print("\n[启动脚本检查 · bat_lint.py]")
        for name, mutated, expect in bat_mutations(bat_base):
            if mutated == bat_base:
                print(f"  [!!  ] {name} —— 替换未生效")
                all_ok = False
                continue
            sub = tmpdir / "bat"
            sub.mkdir(exist_ok=True)
            all_ok &= run_case(node, name, mutated.encode("gbk"), "bat", expect, sub)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n[反向确认：原始文件不会被误报]")
    r1 = subprocess.run([node, str(TESTS / "ui_smoke.js")], capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    r2 = subprocess.run([sys.executable, str(TESTS / "bat_lint.py")],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    print(f"  [{'OK  ' if r1.returncode == 0 else '!!  '}] ui_smoke.js  退出码 {r1.returncode}")
    print(f"  [{'OK  ' if r2.returncode == 0 else '!!  '}] bat_lint.py  退出码 {r2.returncode}")
    all_ok &= (r1.returncode == 0 and r2.returncode == 0)

    print("\n" + "=" * 64)
    print("结论：" + ("测试套件有效（能报错、不乱报）" if all_ok else "存在问题，需检查盲区"))
    print("=" * 64)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
