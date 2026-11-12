# sph-download

受开源项目 [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download) 启发，借鉴了它「用元宝网页端解析视频号分享链接」的思路；本仓库是独立的 Python 实现，没有复制它的代码。

> **English summary.** `sph-download` is a small command-line tool and agent skill that saves a WeChat Channels (视频号) share link as a local MP4, together with its caption, author, engagement counts and cover image. It works by asking the Yuanbao web app — using your own logged-in session — to resolve the share link into a playable video address, then downloading and verifying the file with ffmpeg. It is an independent Python implementation of an idea from [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download); use that project if you need a desktop client, MCP server or multi-platform support. For study and personal archiving of content you have the right to keep.

## 它做什么

给它一条视频号分享链接，它在本地存下：

```
out/<短码>/
├── video.mp4      视频（已用 ffprobe 校验，并抽 3 帧确认能解码）
├── cover.jpg      封面
├── meta.json      文案、作者、发布时间、点赞/收藏/转发/评论数、时长、分辨率
├── frames/        加 --save-frames 时保留的 3 张抽帧
└── transcript.txt 加 --transcribe 时生成的文字稿
```

支持的输入：`https://weixin.qq.com/sph/<短码>`、`https://channels.weixin.qq.com/finder-preview/pages/sph?id=<短码>`、带链接的一段分享文字，或者短码本身。

## 原理

1. 视频号的公开预览页可以匿名看到文案、作者、互动数和封面，但不提供视频地址。
2. 元宝网页端支持解析视频号分享链接。本工具带上**你自己的**元宝登录态请求一次解析，得到一个临时访问凭据，再凭它取得可直接播放的视频地址，然后下载。

所以你需要一个元宝账号，并且登录态只保存在你自己的机器上。

## 安装

需要：

- Python 3.10 及以上（只用标准库，不用 `pip install`）
- `ffmpeg`（含 `ffprobe`）
- 仅脚本登录方式需要：Node.js 22 及以上、Chrome 或 Chromium

```bash
git clone https://github.com/ythx-101/sph-download.git
cd sph-download
chmod +x scripts/sph scripts/sph_batch.sh
scripts/sph --help
```

作为 Claude Code 技能使用：把整个目录放到 `~/.claude/skills/sph-download/`（或项目里的 `.claude/skills/sph-download/`）。其他 agent 让它读 `SKILL.md` 即可。

## 登录元宝

登录态默认存放在 `~/.config/sph-download/yuanbao.cookie`。可用环境变量 `SPH_COOKIE_FILE` 或参数 `--cookie-file` 换位置，也可用 `SPH_STATE_DIR` 换整个状态目录。

### 方式一：手动复制（推荐）

1. 在浏览器里打开元宝网页端并登录。
2. 打开开发者工具（F12）→ Network，随便点开一个发往元宝的请求，复制请求头里完整的 `Cookie`。
3. 保存到文件，权限设为只有自己可读：

   ```bash
   mkdir -p ~/.config/sph-download && umask 077 && cat > ~/.config/sph-download/yuanbao.cookie
   # 粘贴，回车，再按 Ctrl-D
   ```

### 方式二：脚本用手机验证码登录

适合没有图形界面的服务器。脚本在无头 Chrome 里打开元宝的登录框，替你填手机号、点发送，再等你把收到的验证码写进一个文件。

```bash
# 先检查环境（不会发短信）
YB_PHONE=<你的手机号> node scripts/yb_sms_login.mjs --check

# 正式登录：后台运行
YB_PHONE=<你的手机号> nohup node scripts/yb_sms_login.mjs > /tmp/yb_login.log 2>&1 &

# 等 ~/.config/sph-download/NEED_CODE 出现（表示短信已发出），再把验证码写进去
(umask 077; printf %s <验证码> > ~/.config/sph-download/sms.code)

# 日志出现「登录成功」即完成
tail -f /tmp/yb_login.log
```

说明：

- 在容器里或以 root 运行时加 `YB_CHROME_NO_SANDBOX=1`；Chrome 不在常见位置时用 `CHROME_BIN` 指定。
- 脚本只保存解析需要的三项登录信息，读到验证码后立即删除 `sms.code`，不打印登录态和验证码。
- 验证码错误或过期时脚本会重发（最多 2 次），重新写一次 `sms.code` 即可。脚本最多等 20 分钟。
- 如果页面要求人机验证，脚本会报错退出，请改用方式一。

登录态过一段时间会失效，届时 `sph` 返回退出码 3，重新登录一次即可。

## 用法

### 单条

```bash
scripts/sph "https://weixin.qq.com/sph/XXXXXXXXXX" --out out --save-frames
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--out <目录>` | 输出根目录，默认 `./out` |
| `--save-frames` | 保留 3 张抽帧，方便人工确认画面 |
| `--meta-only` | 只取文案、作者、互动数和封面，不需要登录态 |
| `--transcribe` | 生成文字稿，见下文 |
| `--cookie-file <文件>` | 指定登录态文件 |
| `--max-mb <数字>` | 单个视频大小上限，默认 200 |

### 批量

把链接写进文本文件，每行一个（`#` 开头和空行会跳过，重复的只跑一次）：

```bash
scripts/sph_batch.sh links.txt --out out -- --save-frames
```

- 严格串行，每条之间随机等 5～10 秒，任意 60 秒内最多开始 6 条。请不要调高频率或并发运行。
- 已经有 `video.mp4` 的会跳过，中断后重跑同一条命令即可续上。
- 登录态失效时立刻停下；重新登录后重跑。
- 每条的结果追加到 `<输出目录>/batch.tsv`。
- `--` 后面的参数原样传给 `sph`。

### 文字稿（可选）

`--transcribe` 默认调用本机的 `whisper` 命令。也可以用环境变量接任意转写工具，`{audio}` 是 16kHz 单声道 16 位 FLAC，`{out}` 是要写出的文本文件：

```bash
export SPH_TRANSCRIBE_CMD='my-asr --input {audio} --output {out}'
scripts/sph "<链接>" --transcribe
```

转写失败不影响视频和元数据。

### 在自己的程序里复用

解析逻辑在 `scripts/sphlib/`，与命令行分开，只依赖标准库：

```python
import sys
sys.path.insert(0, "scripts")
from sphlib import auth, resolve

cookie, _ = auth.load_cookie()
result = resolve.resolve("https://weixin.qq.com/sph/XXXXXXXXXX", cookie)
print(result.meta["author"], result.meta["description"])
print(result.video_url)   # 有时效，拿到后尽快下载
```

| 模块 | 内容 |
| --- | --- |
| `sphlib/resolve.py` | 链接 → 元数据 + 视频地址。纯网络请求，不读写磁盘 |
| `sphlib/auth.py` | 登录态文件的位置约定与读取 |
| `sphlib/media.py` | 限大小下载、ffprobe 校验、抽帧、提取音频 |

## 常见报错

`sph` 的退出码：

| 退出码 | 含义 | 处理 |
| --- | --- | --- |
| 0 | 成功 | — |
| 1 | 链接无效、网络错误、文件超过大小上限或校验失败 | 看报错文字；超限可调 `--max-mb` |
| 2 | 没有配置登录态，只保存了元数据和封面 | 按「登录元宝」配置 |
| **3** | **登录态失效** | **重新登录元宝，然后重跑** |
| 4 | 这条链接拿不到视频（已删除、纯图文动态等） | 换一条链接确认工具本身正常 |

`sph_batch.sh` 的退出码：0 全部成功或已跳过；1 有失败的条目；3 因登录态失效中止；5 因磁盘空间不足中止（下限 300MB，可用 `SPH_BATCH_MIN_FREE_MB` 调整）；2 用法错误。

其他情况：

- **`缺少命令: ffmpeg`**：安装 ffmpeg。
- **`登录态缺少必需字段`**：Cookie 没复制完整，按方式一重新复制整段。
- **登录脚本报「连不上无头 Chrome」**：加 `YB_CHROME_NO_SANDBOX=1`，或用 `CHROME_BIN` 指定浏览器。
- **登录脚本报「需要 Node.js 22 及以上」**：升级 Node.js。
- **昨天还好好的，今天全部失败**：这个工具依赖元宝网页端和视频号预览页的现有行为，对方调整后可能失效。请到原项目 [wx_channels_download](https://github.com/ltaoo/wx_channels_download) 看看最新进展。

## 安全提示

- 登录态文件等同于你的元宝账号，请保持权限 600，不要提交到仓库、不要发给别人。`.gitignore` 已排除它。
- 不要把登录态写在命令行参数里，那样会留在 shell 历史中。

## 免责声明

本项目仅供学习研究。请只下载你自己有权保存的内容；视频的版权归原作者所有，未经授权请勿转载、传播或用于商业用途。使用本项目时请遵守微信与元宝的用户协议，因使用本项目产生的一切后果由使用者自行承担。本项目与腾讯公司没有任何关联。

## 许可

[MIT](LICENSE)

## 致谢

感谢 [ltaoo](https://github.com/ltaoo) 和 [wx_channels_download](https://github.com/ltaoo/wx_channels_download) 项目。「用元宝网页端解析视频号分享链接」这个思路来自该项目，本仓库只是把这一条思路用 Python 独立重写成了一个命令行小工具。

原项目的功能要全得多：本地客户端、MCP、多平台支持等。需要更多功能，请直接使用原项目。

原项目采用 MIT + Commons Clause 许可。本仓库没有复制它的代码，同时尊重它的许可条款；如果你要使用或分发原项目的代码，请遵守它的许可。
