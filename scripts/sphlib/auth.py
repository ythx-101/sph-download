"""登录态（元宝网页端 Cookie）的位置约定与读取。任何函数都不打印、不返回到日志里 Cookie 的值。"""

import os

ENV_COOKIE_VALUE = "SPH_YUANBAO_COOKIE"   # 直接放 Cookie 字符串
ENV_COOKIE_FILE = "SPH_COOKIE_FILE"       # Cookie 文件路径
ENV_STATE_DIR = "SPH_STATE_DIR"           # 状态目录（Cookie 文件、登录脚本的握手文件）

REQUIRED_FIELDS = ("hy_user", "hy_token")


def state_dir() -> str:
    """状态目录，默认 ~/.config/sph-download，可用环境变量 SPH_STATE_DIR 改。"""
    custom = os.environ.get(ENV_STATE_DIR, "").strip()
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "sph-download")


def cookie_file(cli_path: str | None = None) -> str:
    """Cookie 文件路径。优先级：命令行参数 > SPH_COOKIE_FILE > <状态目录>/yuanbao.cookie。"""
    for cand in (cli_path, os.environ.get(ENV_COOKIE_FILE, "")):
        if cand and cand.strip():
            return os.path.abspath(os.path.expanduser(cand.strip()))
    return os.path.join(state_dir(), "yuanbao.cookie")


def parse_cookie(cookie_str: str) -> dict:
    """把 Cookie 请求头字符串拆成字典。"""
    out = {}
    for item in (cookie_str or "").split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def load_cookie(cli_path: str | None = None) -> tuple[str | None, str]:
    """返回 (cookie 字符串或 None, 来源描述)。来源描述只含路径或变量名，不含值。"""
    env_val = os.environ.get(ENV_COOKIE_VALUE, "").strip()
    if env_val and not cli_path:
        return env_val, f"环境变量 {ENV_COOKIE_VALUE}"
    path = cookie_file(cli_path)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            val = f.read().strip()
        if val:
            return val, path
    return None, path


def missing_fields(cookie_str: str) -> list:
    """返回缺少的必需字段名列表（空列表表示齐全）。"""
    cmap = parse_cookie(cookie_str)
    return [k for k in REQUIRED_FIELDS if not cmap.get(k)]
