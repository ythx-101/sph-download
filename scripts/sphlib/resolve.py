"""视频号分享链接解析：链接 -> 元数据 + 视频地址。

只用 Python 标准库，只做网络请求，不读写磁盘，方便嵌进命令行以外的程序（比如服务端）。

原理（两步）：
  1. 视频号的公开预览页本身可以匿名取到文案、作者、发布时间、互动数和封面，但不给视频地址。
  2. 元宝网页端支持「解析视频号分享链接」。带上你自己的元宝登录态请求一次，
     拿到一个临时访问凭据，再用它向预览页的同一个接口请求，就能得到可直接播放的视频地址。
这个思路来自开源项目 ltaoo/wx_channels_download，本文件是独立的 Python 实现。
"""

import dataclasses
import datetime
import html
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import auth

PREVIEW_HOST = "https://channels.weixin.qq.com"
PREVIEW_FEED_API = PREVIEW_HOST + "/finder-preview/api/feed/get_feed_info"
YUANBAO_HOST = "https://yuanbao.tencent.com"
YUANBAO_PARSE_API = YUANBAO_HOST + "/api/weixin/get_parse_result"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 20

# 元宝侧表示「未登录 / 会话过期」的业务错误码
_AUTH_ERROR_CODES = {"20000", "401", "1001", "20036"}


class SphError(RuntimeError):
    """解析失败的基类。"""


class InvalidLinkError(SphError):
    """输入里找不到视频号分享链接或短码。"""


class LoginRequiredError(SphError):
    """没有配置登录态。"""


class LoginExpiredError(SphError):
    """登录态缺字段、失效或过期。异常文本不包含 Cookie 的值。"""


class VideoUnavailableError(SphError):
    """链接有效，但拿不到视频（已删除、仅图文、对方限制等）。"""


@dataclasses.dataclass
class ResolveResult:
    short_id: str
    share_url: str
    meta: dict                      # 可直接写成 meta.json 的元数据
    video_url: str | None = None    # 有登录态且解析成功时才有；有时效，拿到后尽快下载
    cover_url: str | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_share_id(text: str) -> str:
    """从分享短链、预览页链接、带链接的一段分享文字或裸短码里取出短码。"""
    text = (text or "").strip()
    m = (
        re.search(r"weixin\.qq\.com/sph/([A-Za-z0-9_-]+)", text)
        or re.search(r"channels\.weixin\.qq\.com/finder-preview/pages/sph\?[^#\s]*?\bid=([A-Za-z0-9_-]+)", text)
        or re.search(r"[?&]id=([A-Za-z0-9_-]+)", text)
    )
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{6,32}", text):
        return text

    url_match = re.search(r"https?://[^\s]+", text)
    if url_match:
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            opener.open(
                urllib.request.Request(url_match.group(0), headers={"User-Agent": USER_AGENT}),
                timeout=HTTP_TIMEOUT,
            )
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") or ""
            m2 = re.search(r"(?:/sph/|[?&]id=)([A-Za-z0-9_-]+)", loc)
            if m2:
                return m2.group(1)
        except (urllib.error.URLError, OSError):
            pass
    raise InvalidLinkError(f"无法从输入中解析出视频号短码: {text!r}")


def share_url_of(short_id: str) -> str:
    return f"https://weixin.qq.com/sph/{short_id}"


def parse_count(val):
    """把 '255'、'1.2万'、'10万+' 这类展示用的数字转成整数；转不了就原样返回。"""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().rstrip("+")
    if not s:
        return 0
    try:
        if s.endswith("万"):
            return int(round(float(s[:-1]) * 10000))
        if s.endswith("亿"):
            return int(round(float(s[:-1]) * 100000000))
        return int(s.replace(",", ""))
    except ValueError:
        return val


def _strip_html(text) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<br\s*/?>", " ", str(text), flags=re.I)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return re.sub(r"\s+", " ", html.unescape(cleaned)).strip()


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.load(resp)


def fetch_public_feed(short_id: str) -> dict:
    """匿名请求公开预览页的数据接口，返回原始 JSON（不含视频地址）。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": USER_AGENT,
        "Origin": PREVIEW_HOST,
        "Referer": f"{PREVIEW_HOST}/finder-preview/pages/sph?id={short_id}",
    }
    return _post_json(PREVIEW_FEED_API, {"baseReq": {"generalToken": ""}, "shortUri": short_id}, headers)


def build_meta(short_id: str, public_feed: dict) -> dict:
    """把公开预览数据整理成稳定的元数据结构。"""
    data = public_feed.get("data") or {}
    feed = data.get("feedInfo") or {}
    author = data.get("authorInfo") or {}
    err = data.get("errMsg") or {}
    ct = feed.get("createtime")
    tz_cn = datetime.timezone(datetime.timedelta(hours=8))
    pics = feed.get("picInfo") or []
    return {
        "id": short_id,
        "share_url": share_url_of(short_id),
        "description": feed.get("description"),
        "author": author.get("nickname"),
        "create_time": ct,
        "create_time_iso": datetime.datetime.fromtimestamp(ct, tz_cn).isoformat() if ct else None,
        "interactions": {
            "like": parse_count(feed.get("likeCountFmt")),
            "fav": parse_count(feed.get("favCountFmt")),
            "forward": parse_count(feed.get("forwardCountFmt")),
            "comment": parse_count(feed.get("commentCountFmt")),
        },
        "pic_count": len(pics),
        "duration": None,
        "cover_file": None,
        "video_file": None,
        "transcript_file": None,
        "video": None,
        "notice": _strip_html(err.get("title")) or None,
    }


def _extract_video_url(feed_info: dict) -> str | None:
    for field in ("h264VideoInfo", "h265VideoInfo"):
        url = (feed_info.get(field) or {}).get("videoUrl")
        if url:
            return url
    return feed_info.get("videoUrl") or None


def fetch_video_url(share_url: str, cookie: str) -> str:
    """带元宝登录态换取临时访问凭据，再向预览接口请求，返回可直接播放的视频地址。"""
    lacking = auth.missing_fields(cookie)
    if lacking:
        raise LoginExpiredError(f"登录态缺少必需字段 {'、'.join(lacking)}，请重新登录元宝网页端")
    cmap = auth.parse_cookie(cookie)

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": YUANBAO_HOST,
        "Referer": YUANBAO_HOST + "/",
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "X-Source": "web",
        "X-Platform": "mac",
        "X-Language": "zh-CN",
        "X-Id": cmap["hy_user"],
        "T-Userid": cmap["hy_user"],
        "Cookie": cookie.strip(),
    }
    try:
        parsed = _post_json(YUANBAO_PARSE_API, {"type": "video_channel_url", "url": share_url, "scene": 1}, headers)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise LoginExpiredError(f"登录态已失效或过期（HTTP {e.code}），请重新登录元宝网页端") from None
        raise SphError(f"解析请求失败（HTTP {e.code}）") from None

    err_obj = parsed.get("error") or {}
    err_code = str(err_obj.get("code") or parsed.get("code") or "")
    if err_code in _AUTH_ERROR_CODES:
        raise LoginExpiredError(f"登录态已失效或过期（错误码 {err_code}），请重新登录元宝网页端")

    data = parsed.get("data") or {}
    playable = data.get("playable_url") or ""
    export_id = data.get("wx_export_id") or ""
    token = ""
    if playable:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(playable).query)
        token = (qs.get("token") or [""])[0]
        export_id = (qs.get("eid") or [export_id])[0]
    if not export_id or not token:
        raise VideoUnavailableError(
            f"没有拿到临时访问凭据（code={parsed.get('code')}, msg={parsed.get('msg')}），这条链接可能不是视频或已失效"
        )

    rid = f"{int(time.time()):x}-" + "".join(random.choice("0123456789abcdef") for _ in range(8))
    page = PREVIEW_HOST + "/finder-preview/pages/feed"
    api_url = f"{PREVIEW_FEED_API}?_rid={rid}&_pageUrl={urllib.parse.quote(page, safe=':')}"
    referer = (
        f"{page}?entry_card_type=48&comment_scene=39&appid=0"
        f"&token={urllib.parse.quote(token, safe='')}"
        f"&entry_scene=0&eid={urllib.parse.quote(export_id, safe='')}"
    )
    feed_resp = _post_json(
        api_url,
        {"baseReq": {"generalToken": token}, "exportId": export_id},
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": PREVIEW_HOST,
            "Referer": referer,
            "User-Agent": USER_AGENT,
        },
    )
    if feed_resp.get("errCode") != 0:
        raise SphError(f"预览接口返回错误: errCode={feed_resp.get('errCode')} {feed_resp.get('errMsg')}")
    data2 = feed_resp.get("data") or {}
    err2 = data2.get("errMsg") or {}
    if err2.get("type") or _strip_html(err2.get("title")):
        raise VideoUnavailableError(
            f"视频不可播放: {_strip_html(err2.get('title'))} {_strip_html(err2.get('content'))}".strip()
        )
    video_url = _extract_video_url(data2.get("feedInfo") or {})
    if not video_url:
        raise VideoUnavailableError("解析成功但没有视频地址（可能是纯图文动态）")
    return video_url


def resolve(link: str, cookie: str | None = None) -> ResolveResult:
    """一步到位：链接 -> ResolveResult。

    cookie 为 None 时只返回公开元数据（video_url 为 None）；
    传了 cookie 而登录态无效时抛 LoginExpiredError。
    """
    short_id = parse_share_id(link)
    share_url = share_url_of(short_id)
    try:
        public_feed = fetch_public_feed(short_id)
    except urllib.error.HTTPError as e:
        raise SphError(f"公开预览接口请求失败（HTTP {e.code}）") from None
    if public_feed.get("errCode") != 0:
        raise SphError(f"公开预览接口返回错误: errCode={public_feed.get('errCode')} {public_feed.get('errMsg')}")

    meta = build_meta(short_id, public_feed)
    feed = (public_feed.get("data") or {}).get("feedInfo") or {}
    cover_url = feed.get("coverUrl") or next((p.get("url") for p in feed.get("picInfo") or [] if p.get("url")), None)
    result = ResolveResult(short_id=short_id, share_url=share_url, meta=meta, cover_url=cover_url)
    if cookie:
        result.video_url = fetch_video_url(share_url, cookie)
    return result
