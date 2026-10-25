# shuoshuo (说说)

[简体中文](README.md) | English

> 🤖 **Built by AI**: model selection, code, tests and docs were all done by [Claude Code](https://claude.com/claude-code) (Claude Opus 5.5). The author wrote the requirements, made the decisions, and validates it in daily use.
> Want your own? Hand [this prompt](#let-your-ai-deploy-it) to your AI assistant.

Self-hosted push-to-talk voice input for Windows and macOS: **hold the hotkey, speak, release, and the text is typed at your cursor.** The default hotkey is CapsLock on Windows and Right Option on macOS.
Recognition runs on your own GPU ([Qwen3-ASR-1.7B](https://github.com/QwenLM/Qwen3-ASR)). Other devices use it over the LAN or Tailscale. No cloud services involved.

It works best for Chinese, English and mixed Chinese–English speech. Qwen3-ASR covers 30 languages and 22 Chinese dialects. The client UI is in Chinese.

## Design: a bad network never costs accuracy

- **Record the whole utterance locally, upload it once, transcribe the full clip on the server.** A poor connection only delays the result; accuracy is unaffected.
- Failed requests are retried 3 times. If they still fail, the audio is kept and can be resent from the tray menu (「重试上一条」, "retry last"), so nothing you say is lost.
- If you have switched windows by the time the result arrives, the text goes to the clipboard with a notification instead of being typed into the wrong place.

## Layout

```
client/   Client (Windows / macOS): hotkey + mic + text input + tray / menu bar (no GPU needed)
server/   Recognition server: Linux / WSL2 + NVIDIA GPU, Qwen3-ASR + hotword list
```

```
[Windows / Mac device]  hold hotkey, record ──whole WAV, HTTP──▶  [GPU box]  Qwen3-ASR-1.7B
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
- **Numbers**: Chinese numerals in the transcript are converted to digits, e.g. 120次, 15%, 下午3点 (3 p.m.), 2026年10月19号, 1、2、3. Words that merely contain numerals (一起, 十分重要, 三个方法) are left alone. Pass `--no-itn` to keep Chinese numerals.
- `./run_server.sh -v` logs the recognized text. By default only duration and character count are logged.
- **Transcript history**: put `SHUOSHUO_HISTORY=history` in `server/.env` and every result is appended to `server/history/2026-10.jsonl` (one file per month, one utterance per line, with time, client IP, duration and text; when digit conversion changed the text, the original is kept too). Skim it now and then and add proper nouns that keep coming out wrong to the hotword list. Off by default. Add `SHUOSHUO_HISTORY_AUDIO=1` to keep the audio too (`server/history/2026-10/*.wav`, about 115 MB per hour of speech), so you can re-run it after changing hotwords or the model.
- **Replay**: `.venv/bin/python replay.py --hotwords candidate.txt` re-recognizes the saved audio with a candidate hotword list and prints only the utterances whose result changed, with the changed fragments (e.g. `Postgre SQL → PostgreSQL`). Check what got fixed and whether anything broke, then replace `hotwords.txt`. Without `--hotwords` it uses the current list; `--last 200` replays only the latest 200 utterances. It loads a second copy of the model (about 5 GB of VRAM), so it can run while the server is up.

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

### Client (macOS 12+)

**Option 1: Python 3.9+**

```bash
python3 -m venv ~/Library/Application\ Support/shuoshuo/venv
~/Library/Application\ Support/shuoshuo/venv/bin/pip install -r client/requirements.txt
cp client/config.example.ini client/config.ini
```

Set `server` and `token`, then run `client/start.sh`. Permissions are then granted to the terminal app you launched it from.

**Option 2: shuoshuo.app**

Run `client/build_mac.sh`, drag `client/dist/shuoshuo.app` into Applications and open it. The config lives at `~/Library/Application Support/shuoshuo/config.ini` (menu bar 「打开配置文件」) and is created on first run.

Either way, grant **Accessibility**, **Input Monitoring** (hotkey and typing) and **Microphone** in System Settings → Privacy & Security. The hotkey starts working a few seconds after you grant access; no restart needed.

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
- A short tap still toggles CapsLock. On macOS, a lone tap of Right Option does nothing anyway.
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
- On macOS, holding CapsLock can't be detected reliably, so it can't be the hotkey. To use Fn/🌐, first set "Press 🌐 key to" to "Do Nothing" in System Settings → Keyboard.
- On macOS the hotkey is not received while Secure Input is on (a password field has focus, or apps like 1Password enable it).
- On macOS, when an input method such as Pinyin is active, the text is pasted via clipboard + ⌘V and the previous clipboard is restored afterwards (see `mac_inject`).
- No iOS / Linux client yet. See AGENTS.md for approaches.

## Privacy

- Audio is sent only to the server you configure, never to a third party.
- The server logs only duration and character count by default. Transcripts are shown in the log only with `-v`, saved to disk only when `SHUOSHUO_HISTORY` is set, and audio is saved only when `SHUOSHUO_HISTORY_AUDIO=1` is set too. The client log records only duration, character count and latency.
- shuoshuo itself collects and reports nothing. The first model download contacts HuggingFace.

## Acknowledgements

- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) (Apache-2.0): the recognition model and the `qwen-asr` inference package.

## License

The code is released under the [MIT](LICENSE) license. Model weights are not distributed with this repository; you download them yourself, and they are governed by Qwen3-ASR's Apache-2.0 license.
