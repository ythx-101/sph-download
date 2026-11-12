---
name: sph-download
description: 把微信视频号的分享链接保存成本地 mp4（附文案、作者、互动数、封面）。用户发来 weixin.qq.com/sph/… 或 channels.weixin.qq.com/finder-preview… 链接，或者说「下这个视频号」「保存这条视频号」时使用。
---

# 视频号下载

`scripts/sph` 把一条视频号分享链接保存到本地：视频、封面和一份元数据。
原理：视频号的公开预览页可以匿名看到文案、作者和封面；元宝网页端能解析视频号分享链接，带上用户自己的元宝登录态请求一次，就能得到可直接播放的视频地址。

下面的路径都相对于本技能所在目录（记作 `<skill_dir>`）。

## 前提

- Python 3.10+、`ffmpeg`（含 `ffprobe`）。
- 用户自己的元宝登录态，默认存在 `~/.config/sph-download/yuanbao.cookie`。没有的话先走「登录」一节。
- 只保存用户有权保存的内容。视频版权归原作者。

## 单条

```bash
<skill_dir>/scripts/sph "<链接>" --out <输出目录> --save-frames
```

产物在 `<输出目录>/<短码>/`：`video.mp4`、`cover.jpg`、`meta.json`、`frames/frame_01~03.jpg`。一条大约 5～10 秒。

按退出码处理：

| 退出码 | 含义 | 怎么办 |
| --- | --- | --- |
| 0 | 成功 | 往下交付 |
| 1 | 链接无效、网络错误、文件超过大小上限或校验失败 | 把报错原样告诉用户 |
| 2 | 没有配置登录态（只存了元数据和封面） | 走「登录」一节后重跑 |
| 3 | 登录态失效 | 走「登录」一节后重跑，不要反复重试 |
| 4 | 这条链接拿不到视频（已删除、纯图文等） | 如实告诉用户 |

**交付前必须亲眼看一帧**：打开 `frames/frame_02.jpg`，确认画面正常，内容和 `meta.json` 里的 `description` 对得上。花屏、黑屏或对不上就不要交付。然后把 `video.mp4` 交给用户，附上作者、文案第一句、时长和分辨率。

只要文案和封面、不要视频时加 `--meta-only`，这种情况不需要登录态。

## 批量

把链接写进一个文本文件，每行一个（`#` 开头和空行会跳过，重复的只跑一次）：

```bash
<skill_dir>/scripts/sph_batch.sh <清单.txt> --out <输出目录> -- --save-frames
```

- 严格串行，每条之间随机等 5～10 秒，任意 60 秒内最多开始 6 条。不要并发，不要自己写循环替代它。
- 已经有 `video.mp4` 的短码会跳过，所以中断后重跑同一条命令就能续上。
- 遇到退出码 3 会立刻停下。重新登录后重跑。
- 每条的结果记在 `<输出目录>/batch.tsv`。脚本退出码：0 全部成功，1 有失败的，3 登录态失效，5 磁盘空间不足。
- `--` 后面的参数原样传给 `sph`。

## 登录

登录态是用户自己的元宝账号，取得它需要用户参与。两种方式，任选其一。

**方式一：用户手动复制（最稳）。** 请用户在自己的浏览器里登录元宝网页端，打开开发者工具的 Network 面板，随便点开一个发往元宝的请求，复制请求头里完整的 `Cookie`，然后自己保存到文件：

```bash
mkdir -p ~/.config/sph-download && umask 077 && cat > ~/.config/sph-download/yuanbao.cookie
# 粘贴后按 Ctrl-D
```

**方式二：脚本用手机验证码登录（适合没有图形界面的机器）。** 需要 Node.js 22+ 和 Chrome 或 Chromium。

1. 后台启动（手机号由用户提供；在容器里或以 root 运行时加 `YB_CHROME_NO_SANDBOX=1`）：
   ```bash
   YB_PHONE=<手机号> nohup node <skill_dir>/scripts/yb_sms_login.mjs > /tmp/yb_login.log 2>&1 &
   ```
2. 等 `~/.config/sph-download/NEED_CODE` 出现，这表示短信已发出。**这时才**向用户要验证码（几分钟就过期，不要提前要）。
3. 把验证码写进去：
   ```bash
   (umask 077; printf %s <验证码> > ~/.config/sph-download/sms.code)
   ```
4. 等日志出现「登录成功」。脚本读到验证码后会自己删掉 `sms.code`。
5. 日志出现「重发」时向用户要新的验证码再写一次；出现「错误」时把那一行告诉用户，改用方式一。

可以先用 `YB_PHONE=<手机号> node <skill_dir>/scripts/yb_sms_login.mjs --check` 检查环境，它不会发短信。

## 可选：文字稿

加 `--transcribe` 会多出 `transcript.txt`。它用本机的 `whisper` 命令；也可以用环境变量 `SPH_TRANSCRIBE_CMD` 指定任意转写命令，`{audio}` 是输入的 16kHz 单声道 FLAC，`{out}` 是要写出的文本文件。转写失败不影响视频。交付文字稿前自己读一遍。

## 规矩

- 不要打印、回显或记录登录态的值：不要 `cat` Cookie 文件，不要把它放进命令行参数、日志或对话。
- 验证码只写进 `sms.code`，不要留在别处。
- 不要提高频率，不要并发。
- 磁盘紧张时输出到临时目录，交付后删掉 `video.mp4`，只留 `meta.json`。
- 向别人介绍这个工具时讲原理即可。

## 配置

| 环境变量 | 作用 | 默认 |
| --- | --- | --- |
| `SPH_STATE_DIR` | 状态目录（登录态文件、登录握手文件） | `~/.config/sph-download` |
| `SPH_COOKIE_FILE` | 登录态文件路径（`sph` 也可用 `--cookie-file`） | `<状态目录>/yuanbao.cookie` |
| `SPH_YUANBAO_COOKIE` | 直接提供登录态内容，优先于文件 | 无 |
| `SPH_MAX_MB` | 单个视频大小上限（MiB） | 200 |
| `SPH_TRANSCRIBE_CMD` | 自定义转写命令模板 | 无 |
| `SPH_BIN` | 批处理使用的 `sph` 路径 | 与 `sph_batch.sh` 同目录 |
