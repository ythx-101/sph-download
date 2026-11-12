"""sphlib：视频号分享链接解析库（与命令行分离，方便在别的程序里复用）。

- sphlib.resolve  纯标准库、只做网络解析：链接 -> 元数据 + 视频地址。不读写磁盘，不依赖 ffmpeg。
- sphlib.auth     登录态（元宝网页端 Cookie）的读取与路径约定。
- sphlib.media    下载、ffprobe 校验、抽帧、提取音频（依赖 ffmpeg / ffprobe）。

最小用法：

    from sphlib import auth, resolve
    cookie, _ = auth.load_cookie()
    result = resolve.resolve("https://weixin.qq.com/sph/XXXXXXXXXX", cookie)
    print(result.meta["author"], result.video_url)
"""

__version__ = "0.1.0"
