# 说说 shuoshuo

简体中文 | [English](README.en.md)

> 🤖 **本项目由 AI 制作**：选型、代码、测试和文档都由 [Claude Code](https://claude.com/claude-code)（Claude Opus 5.5）完成，作者负责提需求、做决定，并在日常使用中验证效果。
> 想自己搭一套？把[这段提示词](#让你的-ai-帮你部署)交给你的 AI 助手就行。

自己的 Windows 语音输入：**按住 CapsLock 说话，松开后文字直接打到光标处**。
识别跑在你自己的 GPU 上（[Qwen3-ASR-1.7B](https://github.com/QwenLM/Qwen3-ASR)），其他设备通过局域网或 Tailscale 调用，不依赖任何云服务。

## 设计要点：网络再差，识别也不打折

- **本地录完整句 → 一次性上传 → 服务端整段识别**：网络不好只会让结果晚一点到，识别准确率不受影响；
- 请求失败自动重试 3 次，还不行就把录音留着，托盘菜单「重试上一条」，一句话都不丢；
- 结果回来时如果你已经切到别的窗口，就放进剪贴板并弹通知，不往不相干的地方打字。

## 结构

```
client/   Windows 客户端：热键 + 录音 + 输入 + 托盘（只依赖 sounddevice / pystray / Pillow，不需要 GPU）
server/   识别服务：Linux / WSL2 + NVIDIA GPU，Qwen3-ASR + 热词表
```

```
[任意 Windows 设备]  按住热键录音 ──整段 WAV, HTTP──▶  [GPU 主机]  Qwen3-ASR-1.7B
   SendInput 打字  ◀──────── JSON {text} ─────────   hotwords.txt 作为上下文
```

## 快速开始

### 服务端（Linux / WSL2 + NVIDIA 显卡，1.7B 约占 5 GB 显存）

需要 [uv](https://docs.astral.sh/uv/) 和 NVIDIA 驱动。

```bash
cd server
./setup.sh        # 建 .venv（torch cu128 + transformers + qwen-asr），并从模板生成 hotwords.txt
./run_server.sh   # 首次会自动下载模型（约 4.7 GB），并生成访问令牌 server/.token
```

- **模型放在别处**：先 `hf download Qwen/Qwen3-ASR-1.7B --local-dir /你的/模型目录`，再在 `server/.env` 里写 `SHUOSHUO_MODEL=/你的/模型目录`
- **显存不够**：换 `Qwen/Qwen3-ASR-0.6B`，更小更快，中文错误率略高
- **热词**：`server/hotwords.txt`，每行一个专有名词或英文术语，保存后下一句就生效，不用重启
- `./run_server.sh -v` 会在日志里打印识别出的文字（默认只记时长和字数）

### 客户端（Windows 10 / 11）

**方式一：Python 3.10+**

```bat
py -m venv %LOCALAPPDATA%\shuoshuo\venv
%LOCALAPPDATA%\shuoshuo\venv\Scripts\pip install -r client\requirements.txt
copy client\config.example.ini client\config.ini
```

编辑 `client\config.ini`，填 `server`（服务地址）和 `token`（`server/.token` 的内容），然后双击 `client\start.bat`。
服务端和客户端在同一台机器上（比如服务跑在这台电脑的 WSL 里）时，可以配置 `server_cmd`：客户端启动时如果服务没开，会自动拉起，退出时一并关掉，释放显存。

**方式二：免安装 exe**

在一台装好 Python 的 Windows 上运行 `client\build.ps1`，把生成的 `client\dist\shuoshuo\` 整个文件夹拷到任意 Windows 设备，改好里面的 `config.ini`，双击 `shuoshuo.exe`。目标设备不需要 Python，也不需要 GPU。

### 跨设备 / 跨网络

推荐用 [Tailscale](https://tailscale.com/)：服务器和各个设备登录同一账号，`server` 填服务器在 Tailscale 里的名字或 IP，例如 `http://my-gpu-box:8765`。
先在客户端设备的浏览器打开 `http://my-gpu-box:8765/health`，看到 `{"ok": true}` 就说明网络通了。

服务跑在 WSL2 里时要注意：WSL 的 Hyper-V 防火墙默认挡入站连接。如果 WSL 里自己装了 Tailscale（会成为单独的节点），客户端连 WSL 那个节点就不用改防火墙。另外，在同一台 Windows 上测 WSL 节点的地址会假超时，要用另一台设备测。

## 让你的 AI 帮你部署

把下面这段发给你的 AI 编程助手（Claude Code、Cursor、Codex 等）：

> 请帮我在我的电脑上部署「说说」语音输入，仓库地址：https://github.com/AiChaosN/shuoshuo 。
> 先完整阅读仓库里的 AGENTS.md，再按里面的流程做：开始前先问清楚我的环境（哪台机器有 NVIDIA 显卡、模型存放位置、哪些设备要用、是否需要跨网络访问），每一步完成后按文档里的方法验证，最后让我亲自按住热键说话实测。
> 如果我的环境和文档不一样（比如没有显卡、想在 Mac 上用），请参照文档里的设计要点和踩坑记录来改造。

[AGENTS.md](AGENTS.md) 是专门写给 AI 看的：设计要点、部署步骤、验证方法、踩过的坑，以及换平台时该怎么改。

## 用法

- 按住 CapsLock 说话（听到上扬的"嘀"开始录），松开（下降的"嘀"）后自动输入
- 短按 CapsLock 仍是切换大小写
- 托盘图标：蓝 = 就绪，红 = 录音中，橙 = 识别中，灰 = 暂停 / 服务不可用
- 热键、提示音、去句号等在 `client/config.ini` 里改（托盘菜单「打开配置文件」）

## 性能参考

RTX 5090，transformers 后端：模型加载约 2 s，显存约 4.8 GB；4 s 语音约 0.2–0.3 s 出结果，8 s 约 0.6 s，15 s 约 0.9 s。
热词实测把 `Postgre SQL` 纠正为 `PostgreSQL`；静音 / 噪声下不会凭空冒出热词。

## 已知限制

- 前台是"以管理员身份运行"的程序时，热键和输入会被 Windows（UIPI）拦掉，需要客户端也以管理员运行
- Win11 新托盘图标默认折叠在 ^ 里，可以拖到任务栏上常驻，方便看录音状态
- 目前只有 Windows 客户端；macOS / iOS 的思路见 AGENTS.md

## 隐私

- 录音只发给你自己配置的服务器，不经过任何第三方；
- 服务端默认只记录时长和字数，加 `-v` 才会记录识别文字；客户端日志也只记时长、字数和耗时；
- 说说本身不做任何统计或上报（首次下载模型时会访问 HuggingFace）。

## 致谢

- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)（Apache-2.0）：识别模型与 `qwen-asr` 推理包

## 许可证

代码以 [MIT](LICENSE) 协议开源。模型权重不随仓库分发，由你自行下载，遵循 Qwen3-ASR 的 Apache-2.0 协议。
