"""离线单元测试：只测不联网的纯函数。运行：python3 -m unittest discover -s tests -v"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts"))

from sphlib import auth, media, resolve  # noqa: E402


class ParseShareIdTest(unittest.TestCase):
    def test_accepted_forms(self):
        inputs = [
            "https://weixin.qq.com/sph/AbC123x",
            "https://channels.weixin.qq.com/finder-preview/pages/sph?id=AbC123x",
            "看看这个视频 https://weixin.qq.com/sph/AbC123x 很不错",
            "AbC123x",
        ]
        for text in inputs:
            with self.subTest(text=text):
                self.assertEqual(resolve.parse_share_id(text), "AbC123x")

    def test_invalid_text(self):
        with self.assertRaises(resolve.InvalidLinkError):
            resolve.parse_share_id("随便一句话")


class ParseCountTest(unittest.TestCase):
    def test_values(self):
        cases = [("255", 255), ("1.2万", 12000), ("10万+", 100000), (None, 0), ("1,234", 1234)]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(resolve.parse_count(raw), expected)


class CookieTest(unittest.TestCase):
    def test_missing_fields(self):
        self.assertEqual(auth.missing_fields("hy_user=a; hy_token = b"), [])
        self.assertEqual(auth.missing_fields("hy_user=a"), ["hy_token"])

    def test_parse_cookie(self):
        self.assertEqual(auth.parse_cookie("a=1; b=2=3"), {"a": "1", "b": "2=3"})

    def test_cookie_file_priority(self):
        env = {"SPH_COOKIE_FILE": "/tmp/from_env.cookie", "SPH_STATE_DIR": "/tmp/state_dir"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(auth.cookie_file("/tmp/from_cli.cookie"), "/tmp/from_cli.cookie")
            self.assertEqual(auth.cookie_file(), "/tmp/from_env.cookie")
        with mock.patch.dict(os.environ, {"SPH_STATE_DIR": "/tmp/state_dir"}):
            os.environ.pop("SPH_COOKIE_FILE", None)
            self.assertEqual(auth.cookie_file(), os.path.join("/tmp/state_dir", "yuanbao.cookie"))


class BuildMetaTest(unittest.TestCase):
    def test_build_meta(self):
        feed = {
            "data": {
                "feedInfo": {"description": "文案", "createtime": 1700000000, "likeCountFmt": "1.2万"},
                "authorInfo": {"nickname": "作者"},
            }
        }
        meta = resolve.build_meta("AbC123x", feed)
        self.assertEqual(meta["author"], "作者")
        self.assertEqual(meta["interactions"]["like"], 12000)
        self.assertEqual(meta["interactions"]["fav"], 0)
        self.assertTrue(meta["create_time_iso"].endswith("+08:00"))


class SummarizeVideoTest(unittest.TestCase):
    def test_with_audio_and_rounding(self):
        info = {
            "format": {"duration": "9.87654"},
            "streams": [
                {"codec_type": "video", "width": 720, "height": 1280, "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        s = media.summarize_video(info)
        self.assertEqual(s["duration"], 9.877)
        self.assertEqual((s["width"], s["height"], s["audio_codec"]), (720, 1280, "aac"))

    def test_no_audio(self):
        info = {"format": {"duration": "1"}, "streams": [{"codec_type": "video", "codec_name": "h264"}]}
        self.assertIsNone(media.summarize_video(info)["audio_codec"])


if __name__ == "__main__":
    unittest.main()
