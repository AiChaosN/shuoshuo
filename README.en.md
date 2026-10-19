# shuoshuo (说说)

[简体中文](README.md) | English

> 🤖 **Built by AI**: model selection, code, tests and docs were all done by [Claude Code](https://claude.com/claude-code) (Claude Opus 5.5). The author wrote the requirements, made the decisions, and validates it in daily use.
> Want your own? Hand [this prompt](#let-your-ai-deploy-it) to your AI assistant.

Self-hosted push-to-talk voice input for Windows: **hold CapsLock, speak, release, and the text is typed at your cursor.**
Recognition runs on your own GPU ([Qwen3-ASR-1.7B](https://github.com/QwenLM/Qwen3-ASR)). Other devices use it over the LAN or Tailscale. No cloud services involved.

It works best for Chinese, English and mixed Chinese–English speech. Qwen3-ASR covers 30 languages and 22 Chinese dialects. The client UI is in Chinese.

## Design: a bad network never costs accuracy

- **Record the whole utterance locally, upload it once, transcribe the full clip on the server.** A poor connection only delays the result; accuracy is unaffected.
- Failed requests are retried 3 times. If they still fail, the audio is kept and can be resent from the tray menu (「重试上一条」, "retry last"), so nothing you say is lost.
- If you have switched windows by the time the result arrives, the text goes to the clipboard with a notification instead of being typed into the wrong place.

## Layout

```
client/   Windows client: hotkey + mic + text input + tray (only sounddevice / pystray / Pillow, no GPU needed)
server/   Recognition server: Linux / WSL2 + NVIDIA GPU, Qwen3-ASR + hotword list
```

```
[any Windows device]  hold hotkey, record ──whole WAV, HTTP──▶  [GPU box]  Qwen3-ASR-1.7B
   SendInput typing   ◀─────────── JSON {text} ────────────   hotwords.txt as context
```

## Quick start

### Server (Linux / WSL2 + NVIDIA GPU; the 1.7B model uses about 5 GB of VRAM)

Requires [uv](https://docs.astral.sh/uv/) and an NVIDIA driver.

```bash
cd server
./setup.sh        # creates .venv (torch cu128 + transformers + qwen-asr) and hotwords.txt from the template
./run_server.sh   # first run downloads the model (~4.7 GB) and creates the access token server/.token
```

- **Model stored elsewhere**: run `hf download Qwen/Qwen3-ASR-1.7B --local-dir /your/model/dir`, then put `SHUOSHUO_MODEL=/your/model/dir` in `server/.env`.
- **Not enough VRAM**: switch to `Qwen/Qwen3-ASR-0.6B`. It is smaller and faster, with a slightly higher Chinese error rate.
- **Hotwords**: `server/hotwords.txt` takes one proper noun or English term per line. Changes apply from the next utterance; no restart needed.
- `./run_server.sh -v` logs the recognized text. By default only duration and character count are logged.

### Client (Windows 10 / 11)

**Option 1: Python 3.10+**

```bat
py -m venv %LOCALAPPDATA%\shuoshuo\venv
%LOCALAPPDATA%\shuoshuo\venv\Scripts\pip install -r client\requirements.txt
copy client\config.example.ini client\config.ini
```

Edit `client\config.ini`: set `server` (the server URL) and `token` (the contents of `server/.token`). Then double-click `client\start.bat`.

If the server runs on the same machine (for example in this PC's WSL), set `server_cmd`. The client then starts the server when it isn't running, and stops it on exit to free the VRAM.

**Option 2: portable exe**

On a Windows machine with Python, run `client\build.ps1`. Copy the resulting `client\dist\shuoshuo\` folder to any Windows device, edit its `config.ini`, and double-click `shuoshuo.exe`. The target device needs neither Python nor a GPU.

### Across devices and networks

[Tailscale](https://tailscale.com/) is recommended. Sign in to the same account on the server and on every device, then set `server` to the server's Tailscale name or IP, e.g. `http://my-gpu-box:8765`.

To check connectivity, open `http://my-gpu-box:8765/health` in a browser on the client device. `{"ok": true}` means you're good.

Running the server in WSL2:
- WSL's Hyper-V firewall blocks inbound connections by default.
- If Tailscale is installed inside WSL, it becomes a separate node. Point clients at that node and no firewall changes are needed.
- Testing the WSL node's address from the same Windows host gives a false timeout. Test from another device instead.

## Let your AI deploy it

Send this to your AI coding assistant (Claude Code, Cursor, Codex, ...):

> Please deploy the "shuoshuo" voice input on my machines. Repository: https://github.com/AiChaosN/shuoshuo
> First read AGENTS.md in the repository in full, then follow its playbook. Before starting, ask me about my environment: which machine has an NVIDIA GPU, where to store the model, which devices will use it, and whether access across networks is needed. Verify each step as the doc describes. Finally, have me test it myself by holding the hotkey and speaking.
> If my setup differs from the docs (e.g. no GPU, or I want it on a Mac), adapt it using the design notes and pitfalls in that file.

[AGENTS.md](AGENTS.md) is written for AI assistants. It covers:
- design rationale
- the deployment playbook
- how to verify each step
- the pitfalls we hit
- how to port the client to other platforms

## Usage

- Hold CapsLock and speak. A rising beep marks the start. Release (falling beep) and the text is typed.
- A short tap still toggles CapsLock.
- Tray icon colors: blue = ready, red = recording, orange = recognizing, gray = paused or server unavailable.
- Hotkey, beeps, trailing-period removal and more are set in `client/config.ini` (tray menu 「打开配置文件」, "open config file").

## Performance

On an RTX 5090 with the transformers backend:
- Model load: about 2 s. VRAM: about 4.8 GB.
- Latency: 4 s of speech returns in about 0.2–0.3 s, 8 s in about 0.6 s, 15 s in about 0.9 s.
- Hotwords corrected `Postgre SQL` to `PostgreSQL` in testing. No hotwords were hallucinated on silence or noise.

## Known limitations

- When the foreground app runs as administrator, Windows (UIPI) blocks both the hotkey and text input. Run the client as administrator too.
- Windows 11 hides new tray icons under the ^ overflow by default. Drag the icon onto the taskbar to keep the recording state visible.
- Only a Windows client exists so far. See AGENTS.md for macOS / iOS approaches.

## Privacy

- Audio is sent only to the server you configure, never to a third party.
- The server logs only duration and character count by default; transcripts are logged only with `-v`. The client log records only duration, character count and latency.
- shuoshuo itself collects and reports nothing. The first model download contacts HuggingFace.

## Acknowledgements

- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) (Apache-2.0): the recognition model and the `qwen-asr` inference package.

## License

The code is released under the [MIT](LICENSE) license. Model weights are not distributed with this repository; you download them yourself, and they are governed by Qwen3-ASR's Apache-2.0 license.
