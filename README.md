# 本地字幕工具

**把视频/音频转成字幕文件，全离线，不上传任何数据。**

双击 `启动.bat` → 浏览器自动打开 → 把文件拖进去 → 点「开始转写」→ 下载字幕。

- 引擎：[whisper.cpp](https://github.com/ggml-org/whisper.cpp) + `large-v3` 模型（本地 GPU 加速）
- 界面：单文件离线网页（`ui\index.html`），无 CDN、无联网请求
- 服务：Python 标准库写的本地 HTTP 服务（`server.py`），**零第三方依赖**
- 速度：31 秒音频，GPU 版约 **3.6 秒**；CPU 版约 **33 秒**

---

## 〇、从 GitHub 克隆后如何补全（仓库未包含模型与二进制）

本仓库**不包含** `bin/`（whisper.cpp 二进制，约 57 MB）和 `models/`（large-v3 约 2.9 GB + silero VAD 约 865 KB），原因如下：

- GitHub 单文件上限 **100 MB**，仓库体积上限建议 1 GB；large-v3 一个文件就 2.9 GB，远超限制。
- 二进制属于平台相关产物，不同机器 / CUDA 版本需要对应构建。

### 1. 获取 whisper.cpp 二进制（放入 `bin/`）

从 [ggml-org/whisper.cpp releases](https://github.com/ggml-org/whisper.cpp/releases) 下载对应平台的预编译包（例如 `whisper-cuda-x86_64-windows.zip`），解压后将以下文件复制到项目根的 `bin/` 目录：

```
bin/
├─ whisper-cli.exe
├─ whisper.dll
├─ ggml.dll
├─ ggml-base.dll
├─ ggml-cpu.dll
└─ ggml-cuda.dll        # 仅 GPU 版需要
```

> 若本机无 NVIDIA 显卡或不想装 CUDA，需改用 CPU 版构建（不含 `ggml-cuda.dll`）。

### 2. 获取 Whisper 模型（放入 `models/`）

| 模型 | 文件名 | 大小 | 来源 |
|---|---|---|---|
| large-v3（主模型） | `ggml-large-v3.bin` | ~2.9 GB | [HuggingFace: ggml-org/whisper-ggml](https://huggingface.co/ggml-org/whisper-ggml) 或 [whisper.cpp releases](https://github.com/ggml-org/whisper.cpp/releases) |
| Silero VAD | `ggml-silero-v5.1.2.bin` | ~865 KB | [HuggingFace: ggml-org/silero-vad](https://huggingface.co/ggml-org/silero-vad) |

将两个 `.bin` 文件放到项目根的 `models/` 目录即可。

### 3. 其他依赖（见「三、依赖与前提」）

- **Python 3** — 安装后确保在 PATH 中
- **ffmpeg** — 安装后确保在 PATH 中，或在 `config.json` 的 `ffmpeg_path` 中指定路径
- **CUDA 13.x 运行库** — 安装 NVIDIA CUDA Toolkit 13.x；`server.py` 会自动扫描并注入 PATH

完成以上三步后，双击 `启动.bat` 即可使用。

---

## 一、快速开始

1. 双击 **`启动.bat`**
2. 浏览器会自动打开 `http://127.0.0.1:8848/`（端口被占时自动顺延，最多试到 8907）
3. 拖入视频文件（或点输入框直接粘贴本机绝对路径）
4. 点「开始转写」，跑完后在界面里预览、下载字幕

**关闭那个黑色命令行窗口即可退出工具。**

---

## 二、界面怎么用

### 第 1 步 · 选文件

两种方式，任选其一：

| 方式 | 适合 | 说明 |
|---|---|---|
| 拖拽 / 点选 | 小文件（几百 MB 以内） | 文件会被**复制一份**到 `tmp\`，传完才开始转写 |
| 直接粘路径 | 大文件 | 例如 `E:\录像\会议.mp4`，**跳过复制**，省时间也省磁盘 |

> 上传没有大小上限，真正的限制是磁盘剩余空间。超过几个 GB 建议直接用路径。

### 第 2 步 · 设置

| 设置项 | 默认 | 说明 |
|---|---|---|
| 模型 | `large-v3` | 精度最高也最慢。换成 `medium`/`small` 可大幅提速 |
| 语言 | 中文 | 中英混杂的素材建议**手动指定**，别用自动检测 |
| 输出格式 | SRT | 另可选 VTT、纯文本 |
| CPU 线程 | 16 | 只在**关掉 GPU** 时才有影响 |
| GPU 加速 | 开 | 关掉后走 CPU，慢约 9 倍，但结果基本一致 |
| VAD | 开 | 见下方「VAD 是什么」 |
| 提示词 | 空 | 填入人名、专有名词等，能明显减少错别字 |

### 第 3 步 · 开始

进度条、实时日志都在这一屏。**「取消」随时可点**，会立刻杀掉正在跑的进程。

### 第 4 步 · 结果

字幕内容直接显示在页面上，点「下载字幕」导出。

### 维护（第 5 张卡片）

- **缓存占用** — 实时显示 `tmp\` 和 `output\` 占了多少空间
- **清理缓存** — 删掉上传的副本、抽出的音频、旧字幕。**不碰程序、模型和设置**
- **恢复默认设置** — 把上面那张设置表还原成默认值（注意：线程会回到 8，不是 16）

任务运行时清理按钮会自动禁用，避免删掉正在写入的文件。

---

## 三、依赖与前提 ⚠️

这三样是**必需**的，缺一样都跑不起来：

| 依赖 | 用途 | 本机位置 | 能搬走吗 |
|---|---|---|---|
| **Python 3** | 跑本地服务 | `启动.bat` 里写死的托管 Python | 目标机器需自备 Python 3 |
| **ffmpeg** | 从视频里抽音轨 | `config.json` 指向 ComfyUI 目录里那份 | 需自备，或手改 `config.json` |
| **CUDA 运行库** | 给 GPU 加速用 | `CUDA\v13.3\bin\x64` | **必须**，见下 |

### 为什么 ffmpeg 是必需的不是可选的

whisper.cpp 只认 `flac / mp3 / ogg / wav` 四种格式，**不认 mp4**。
所以 mp4 必须先经 ffmpeg 抽出音轨。代码里虽然有「找不到 ffmpeg 就直接把视频交给模型」
的降级分支，但对 mp4 来说那条路必然失败 —— 它只对本来就是 wav 的输入有意义。

### 为什么 CUDA 运行库是必需的，连纯 CPU 模式都躲不开

`bin\whisper-cli.exe` 的依赖链是**静态导入**的：

```
whisper-cli.exe → ggml.dll → ggml-cuda.dll → cublas64_13.dll + cublasLt64_13.dll（共 488 MB）
```

也就是说，只要 `CUDA\v13.3\bin\x64` 不在 `PATH` 上，`whisper-cli.exe`
**连进程都起不来**，Windows 直接报 `0xC0000135`（DLL 未找到）。

注意：这个失败发生在**命令行参数被解析之前**，所以勾掉界面上的「GPU 加速」
也救不回来 —— 它不是在 CPU/GPU 之间二选一，而是彻底跑不起来。

`server.py` 会自动扫描 `CUDA\v*\bin\x64`（按版本号倒序）并注入 PATH，
所以**只要本机装着 CUDA 13.x 就不用管这一条**。装在其他位置时，
在 `config.json` 的 `cuda_dirs` 里手填路径即可。

---

## 四、目录结构

```
D:\WhisperSubtitle\
├─ 启动.bat                # 双击即用（GBK 编码，勿用编辑器「另存为 UTF-8」）
├─ server.py               # 本地服务，638 行，纯标准库
├─ config.json             # 设置（界面里改，或手改这个文件）
├─ README.md               # 本文件
├─ ui\
│  └─ index.html           # 单文件界面，534 行
├─ bin\                    # whisper.cpp 二进制（GPU 版）
│  ├─ whisper-cli.exe
│  ├─ whisper.dll  ggml.dll  ggml-base.dll  ggml-cpu.dll
│  └─ ggml-cuda.dll        # 51 MB，CUDA 后端
├─ models\
│  ├─ ggml-large-v3.bin    # 2.9 GB，主模型
│  └─ ggml-silero-v5.1.2.bin  # 865 KB，VAD 模型
├─ tests\                  # 自检套件（见第八节）
├─ output\                 # 字幕产物
└─ tmp\                    # 上传副本 / 抽出的音频 / ffmpeg 日志
```

**只有 `output\` 和 `tmp\` 是临时目录，其余都是程序本体，别删。**

---

## 五、配置文件 `config.json`

```json
{
  "ffmpeg_path": "D:\\ComfyUI\\...\\ffmpeg.exe",
  "cuda_dirs": [],
  "model": "models/ggml-large-v3.bin",
  "language": "zh",
  "format": "srt",
  "vad": true,
  "use_gpu": true,
  "prompt": "",
  "threads": 16
}
```

- `ffmpeg_path` 留空会自动去找：`PATH` → ComfyUI 目录 → `C:\ffmpeg\bin\`
- `cuda_dirs` 留空会自动扫描 `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\bin\x64`
- 界面上的「设置」改的就是这个文件；「恢复默认设置」会把 `threads` 重置为 8

---

## 六、VAD 是什么，什么时候该关

**VAD = Voice Activity Detection，语音活动检测。**

Whisper 有个毛病叫**幻觉**：遇到纯静音或只有背景音乐的地方，它不会老实留白，
而是凭空编字（常见的是编出「请不吝点赞订阅转发」这类训练数据里的高频句），
或者把上一句反复重复。

开了 VAD 之后，先用 Silero 模型把音频切成「有语音 / 无语音」两段，
只把有语音的部分喂给模型，静音段直接跳过。

**建议关掉的场景**：素材里根本没有人说话（纯音乐转歌词）、
或者说话声音极轻（VAD 可能把轻声误判成静音而整段漏掉）。

---

## 七、出问题时怎么办

| 现象 | 原因与处理 |
|---|---|
| 双击 `启动.bat` 一闪就没了 | 没找到 Python 3。脚本会依次找：写死的托管 Python → `py -3` → `PATH` 里的 `python`（已排除应用商店的假 exe）。装个 Python 3 即可 |
| 提示 `0xC0000135` / 缺 `cublas64_13.dll` | CUDA 运行库不在 PATH 上。见第三节 |
| 提示「未找到输出的字幕文件」 | 多半是没装/没配对 ffmpeg，或者输入格式 whisper 读不了。**看一下界面里的日志**，真实原因会打在那里 |
| 进度条不动、日志也不动 | 罕见，直接点「取消」再重来 |
| 中文全是错别字 | ① 手动指定语言为「中文」；② 在「提示词」里填入人名和专有名词；③ 换更大的模型 |
| 字幕出现莫名其妙的重复句子 | 幻觉。确认 VAD 是开着的 |
| 控制台窗口里中文乱码 | 只是 GBK 渲染问题，**生成的 SRT 文件本身是 UTF-8，正常** |
| 端口不是 8848 | 8848 被别的程序占了，自动顺延。看命令行窗口里打印的实际地址 |

---

## 八、自检套件 `tests\`

改完 `ui\index.html` 或 `启动.bat` 后建议跑一遍：

```
双击 tests\run_tests.bat        或
python tests\run_all.py
```

| 文件 | 作用 |
|---|---|
| `run_all.py` | 统一入口，汇总退出码 |
| `bat_lint.py` | `启动.bat` 静态检查（12 项：编码、行尾、括号平衡、路径斜杠…） |
| `ui_smoke.js` | 界面冒烟测试（Node + DOM 桩，81 项断言，跑真实交互路径） |
| `mutation_check.py` | 变异测试：故意改坏源码，验证上面两个**真的会报错**（9 个变异） |

零第三方依赖（Python 标准库 + Node）。详见 `tests\README.md`。

---

## 九、搬到别的机器

**这个文件夹不是开箱即搬的。** 目标机器需要：

1. **Python 3** — 没有就装一个
2. **ffmpeg** — 没有的话，改 `config.json` 的 `ffmpeg_path` 指到目标机器上那份
3. **CUDA 13.x 运行库** — 没有的话，`whisper-cli.exe` 起不来（见第三节）

模型（2.9 GB）和界面都在文件夹里，不用另外准备。

> 如果目标机器没有 NVIDIA 显卡或不想装 CUDA：需要一个 CPU 版构建
> （`E:\whisper.cpp\build-cpu\bin` 下有一份现成的，取 `whisper-cli.exe` +
> `whisper.dll` + `ggml.dll` + `ggml-base.dll` + `ggml-cpu.dll`，共约 3.3 MB）。
> 本包**暂未**内置这个兜底。实测该 CPU 版在无 CUDA 环境下可正常转写，
> 30.9 秒音频耗时 33.1 秒。

---

## 十、已知限制

- **一次只能转一个文件**，没有批量队列。
- **whisper-cli 读不到音频时退出码是 0**，所以「退出码非 0 即失败」这个判断不可靠；
  `server.py` 额外用「产物是否存在」兜底，代价是错误提示偏笼统。
- 文件名含 `[` `]` 时，产物兜底查找（glob）会失配 —— 主路径正常，只影响兜底分支。
- 长视频会让 `tmp\` 多占一份空间（1 小时音频的 WAV 约 115 MB）。
  空间紧张时用「直接粘路径」或跑完点「清理缓存」。

---

## 十一、技术备注

- 服务端 12 个接口：`status` / `progress` / `result` / `config` / `start` /
  `cancel` / `upload` / `download` / `open_output` / `cache_info` /
  `clear_cache` / `reset_config`
- 取消功能靠**直接 kill 子进程**实现（只设标志位会被阻塞式 stdout 读取卡住，无效）
- 进度条依赖 whisper-cli 的 `-pp` 参数（不加则进度永远 0%）
- CUDA 13.x 的运行时 DLL 在 `bin\x64\` 子目录，不是 `bin\` 根目录
- 清理缓存有三重保护：只处理 `tmp\`/`output\`、只删直系文件不下钻子目录、
  `resolve()` 后校验父目录（挡符号链接外逃）
