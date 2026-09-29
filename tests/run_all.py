"""run_all.py —— 本地字幕工具的一键自检入口

依次执行：
  1) 启动.bat  静态检查（本机无法执行 .bat，所以把能静态查的全查了）
  2) 界面冒烟测试（Node + DOM 桩，跑 ui/index.html 的真实交互路径）

用法：    python tests/run_all.py
双击：    tests/run_tests.bat
退出码：  0 = 全部通过；1 = 有失败
"""

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def find_node():
    """先找托管 Node（按版本号倒序，兼容以后升级），再退回 PATH。"""
    versions = pathlib.Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if versions.is_dir():
        for p in sorted(versions.glob("*/node.exe"), reverse=True):
            return str(p)
    return shutil.which("node")


def run_step(title, cmd):
    print("\n" + "#" * 64)
    print(f"# {title}")
    print("#" * 64)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    sys.stdout.flush()      # 否则输出被重定向时，子进程的字会跑到标题前面
    try:
        rc = subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
    except FileNotFoundError as e:
        print(f"  [FAIL] 无法执行：{e}")
        return 1
    return rc


def main():
    print("=" * 64)
    print(f"本地字幕工具 · 自检    ({ROOT})")
    print("=" * 64)
    if not (ROOT / "server.py").exists():
        print(f"[FAIL] 这里不像是字幕工具的根目录：{ROOT}")
        return 1

    results = []

    rc = run_step("① 启动.bat 静态检查",
                  [sys.executable, str(TESTS / "bat_lint.py")])
    results.append(("启动.bat 静态检查", rc, 12))

    node = find_node()
    if not node:
        print("\n" + "#" * 64)
        print("# ② 界面冒烟测试 —— 跳过：找不到 Node.js")
        print("#" * 64)
        results.append(("界面冒烟测试", 2, 0))
    else:
        rc = run_step(f"② 界面冒烟测试  (node: {node})",
                      [node, str(TESTS / "ui_smoke.js")])
        results.append(("界面冒烟测试", rc, 80))

    print("\n" + "=" * 64)
    print("自检汇总")
    print("=" * 64)
    failed = 0
    for name, rc, _ in results:
        if rc == 0:
            print(f"  [PASS] {name}")
        elif rc == 2:
            print(f"  [SKIP] {name}（缺依赖）")
            failed += 1
        else:
            print(f"  [FAIL] {name}（退出码 {rc}）")
            failed += 1
    print("=" * 64)
    if failed:
        print(f"结果：{len(results) - failed}/{len(results)} 项通过，需要处理")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
