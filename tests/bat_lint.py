"""bat_lint.py —— 启动.bat 静态检查器

背景：本机环境不允许调用 cmd.exe，所以 .bat 无法真正执行验证。
这个检查器把「能静态查出问题」的部分全部自动化：
  字节层（编码 / 行尾）、语法层（括号平衡 / 元字符）、语义层（路径斜杠 / 标签 / 关键逻辑）。

用法：  python tests/bat_lint.py
退出码：0 = 无问题；1 = 发现问题
"""

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_env = os.environ.get("BAT_PATH")
BAT = pathlib.Path(_env).resolve() if _env else ROOT / "启动.bat"

problems = []
notes = []
checked = 0


def bad(msg):
    problems.append(msg)


def ok(msg=""):
    global checked
    checked += 1
    if msg:
        notes.append(msg)


# ---------------------------------------------------------------- 存在性
if not BAT.exists():
    print(f"[FAIL] 找不到 {BAT}")
    sys.exit(1)

raw = BAT.read_bytes()

# ---------------------------------------------------------------- 字节层
if b"\x00" in raw:
    bad("文件含 NUL 字节，bat 会解析异常")
else:
    ok("无 NUL 字节")

crlf = raw.count(b"\r\n")
bare_lf = raw.count(b"\n") - crlf
if bare_lf:
    bad(f"存在 {bare_lf} 个裸 LF；cmd 对裸换行处理不一致，应全用 CRLF")
else:
    ok(f"行尾全为 CRLF（{crlf} 行）")

try:
    text = raw.decode("gbk")
    ok("可用 GBK 解码（中文控制台不乱码）")
except UnicodeDecodeError as e:
    bad(f"GBK 解码失败：{e}")
    sys.exit(1)

lines = text.replace("\r\n", "\n").split("\n")

# ---------------------------------------------------------------- 语法层
def is_rem(s: str) -> bool:
    low = s.strip().lower()
    return low == "rem" or low.startswith("rem ") or low.startswith("::")


# 1) rem 注释里的 cmd 元字符：只有出现在括号块**内部**才会破坏结构
#    （块会先被整体解析，其中的 ) 会提前闭合块；顶层的 rem 整行忽略，无害）
bad_rem = []
depth = 0
min_depth = 0
for i, s in enumerate(lines, 1):
    if is_rem(s):
        if depth > 0 and any(ch in s for ch in "()|&<>^"):
            bad_rem.append(i)
        continue
    if s.lstrip().startswith("@"):
        continue
    inq = False
    for ch in s:
        if ch == '"':
            inq = not inq
        elif not inq:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                min_depth = min(min_depth, depth)
if bad_rem:
    bad(f"括号块内的注释含 cmd 元字符（第 {bad_rem} 行）—— 会提前闭合块")
else:
    ok("注释行不含危险的 cmd 元字符")

# 2) 括号平衡
if depth != 0:
    bad(f"括号不平衡：结束时深度 {depth}，说明有 if ( 未闭合")
elif min_depth < 0:
    bad("存在多余的右括号（深度曾降到负值）")
else:
    ok("括号平衡")

# 3) for 变量必须双百分号
single = [i for i, s in enumerate(lines, 1)
          if not is_rem(s) and re.search(r"for\s+%[A-Za-z]\s+in\s", s)]
if single:
    bad(f"for 变量用了单百分号（第 {single} 行）—— 批处理里必须写成 %%P")
else:
    ok("for 变量写法正确（双百分号）")

# ---------------------------------------------------------------- 路径斜杠
quoted = re.findall(r'"([^"]*)"', text)
slash_paths = [q for q in quoted if "/" in q]
if slash_paths:
    bad(f"引号内出现正斜杠路径：{slash_paths} —— cmd.exe 把 / 当开关字符，应改反斜杠")
else:
    ok("所有引号内路径均使用反斜杠")

# ---------------------------------------------------------------- 标签跳转
labels = {s.strip()[1:].split()[0].lower() for s in lines
          if s.strip().startswith(":") and not s.strip().startswith("::")}
gotos = set()
for s in lines:
    if is_rem(s):
        continue
    m = re.search(r"\bgoto\s+:?([A-Za-z_][\w]*)", s, re.I)
    if m:
        gotos.add(m.group(1).lower())
missing = gotos - labels
if missing:
    bad(f"goto 指向不存在的标签：{sorted(missing)}")
elif gotos:
    ok(f"goto 标签全部有定义（{sorted(gotos)}）")
else:
    ok("未使用 goto（结构简单）")

# ---------------------------------------------------------------- 语义层
if "%ERRORLEVEL%" in text and "exit /b" not in text.lower():
    bad("依赖 %ERRORLEVEL% 但没有 exit /b 传递退出码")
else:
    ok("错误分支的退出码处理正常")

if "%~dp0" not in text:
    bad("未使用 %~dp0 定位自身目录，换目录启动会失败")
else:
    ok("用 %~dp0 定位自身目录")

# 以下两项只对「启动工具的那个 bat」有意义
IS_LAUNCHER = BAT.name.lower() in ("启动.bat", "start.bat")
if IS_LAUNCHER:
    if "server.py" not in text:
        bad("脚本里没有引用 server.py")
    elif not (ROOT / "server.py").exists():
        bad("引用了 server.py，但该文件不存在")
    else:
        ok("引用的 server.py 确实存在")

    # 设计契约：PATH 兜底查找存在时，必须有应用商店占位符防护
    has_path_fallback = "$PATH:P" in text and "python.exe" in text
    has_store_guard = "WindowsApps" in text
    if has_path_fallback and not has_store_guard:
        bad("有 PATH 兜底查找 python.exe，但没有排除 WindowsApps 占位符"
            "（那个 exe 是跳转器，只会弹出应用商店）")
    elif has_path_fallback:
        ok("PATH 兜底查找已排除 WindowsApps 占位符")
    else:
        ok("无 PATH 兜底查找，无需商店防护")
else:
    ok(f"非启动脚本（{BAT.name}），跳过 server.py / 商店防护检查")

# ---------------------------------------------------------------- 输出
print("=" * 64)
print(f"bat 静态检查   ({BAT})")
print("=" * 64)
for n in notes:
    print(f"  [PASS] {n}")
for p in problems:
    print(f"  [FAIL] {p}")
print("-" * 64)
if problems:
    print(f"结果：{checked} 项通过 / {len(problems)} 项失败")
    sys.exit(1)
print(f"结果：{checked} 项全部通过")
sys.exit(0)
