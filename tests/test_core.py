import json
import threading
from pathlib import Path

import pytest
import requests

from streamgrab.download import choose_format, sniff_image
from streamgrab.engine import (assigned_json, downloader, parse_douyin_detail,
                               parse_xhs_state, session_for)
from streamgrab.models import (DownloadSpec, LoginOptions, Media, UserError,
                               extract_url, platform_for, qualities_for, safe_name, seconds)


@pytest.mark.parametrize("raw,expected", [("12.5", 12.5), ("01:30.5", 90.5), ("1:02:03", 3723), ("", None), ("0", 0)])
def test_time_formats(raw, expected):
    assert seconds(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "NaN", "Infinity", "1:60", "2:99:01", "abc", "1e8"])
def test_invalid_times(raw):
    with pytest.raises(UserError):
        seconds(raw)


def test_share_text_retains_security_token():
    assert extract_url("分享笔记 https://www.xiaohongshu.com/explore/abc?xsec_token=123%2Babc&xsec_source=pc_share，快来看看") == "https://www.xiaohongshu.com/explore/abc?xsec_token=123%2Babc&xsec_source=pc_share"
    assert platform_for("https://bilibili.com.attacker.example/a") == "其他平台"
    assert extract_url("BV1xx411c79H").endswith("BV1xx411c79H")


def test_xhs_body_images_exclude_recommendations_and_avatars():
    note = {"noteId": "abc", "title": "图集", "type": "normal", "user": {"nickname": "作者", "avatar": "https://img.example/avatar.jpg"},
            "imageList": [{"urlDefault": "https://img.example/1.jpg"}, {"urlDefault": "https://img.example/2.jpg"}]}
    page = "window.__INITIAL_STATE__=" + json.dumps({"note": {"noteDetailMap": {"abc": {"note": note}}}, "recommended": {"url": "https://img.example/recommend.jpg"}}) + "; window.other={};"
    media = parse_xhs_state(page, "https://www.xiaohongshu.com/explore/abc")
    assert len(media.images) == 2
    assert not media.has_video
    assert media.images[1].url.endswith("2.jpg")
    with pytest.raises(UserError):
        parse_xhs_state(page, "https://www.xiaohongshu.com/explore/def")


def test_embedded_json_undefined_and_braces_inside_strings():
    result = assigned_json('window.__INITIAL_STATE__={"text":"a } [ b", "x":undefined};bad();', r"window\.__INITIAL_STATE__\s*=")
    assert result == {"text": "a } [ b", "x": None}


def test_douyin_photo_order_and_music():
    media = parse_douyin_detail({"aweme_id": "123", "images": [{"url_list": ["https://i.example/1.jpg"]}, {"url_list": ["https://i.example/2.jpg"]}],
                                "music": {"play_url": {"url_list": ["https://m.example/a.mp3"]}}}, "https://www.douyin.com/note/123")
    assert not media.has_video and media.has_audio
    assert [a.url for a in media.images] == ["https://i.example/1.jpg", "https://i.example/2.jpg"]


def test_only_real_resolution_and_no_explicit_watermark(tmp_path):
    info = {"id": "1", "title": "example", "formats": [
        {"format_id": "portrait", "url": "https://v.example/1080", "width": 1080, "height": 1920, "vcodec": "h264", "acodec": "aac"},
        {"format_id": "720", "url": "https://v.example/720", "width": 720, "height": 1280, "vcodec": "h264", "acodec": "aac"},
        {"format_id": "marked", "url": "https://v.example/4k", "width": 2160, "height": 3840, "vcodec": "h264", "format_note": "Download video, watermarked"},
        {"format_id": "audio", "url": "https://v.example/a", "vcodec": "none", "acodec": "aac"}]}
    assert [q.key for q in qualities_for(info)] == ["best", "1080", "720"]
    media = Media("https://www.douyin.com/video/1", "抖音", "example", "1", info=info)
    selected, _ = choose_format(DownloadSpec(media, tmp_path, quality="720"))
    assert {f["format_id"] for f in selected["formats"]} == {"720", "audio"}
    with pytest.raises(UserError):
        choose_format(DownloadSpec(media, tmp_path, quality="2160"))


def test_cookie_scope_and_input_file_unchanged(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    content = "# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t2147483647\tSESSDATA\ttest-secret\n"
    cookie_file.write_text(content, encoding="utf-8")
    with downloader(LoginOptions(cookie_file=str(cookie_file)), "https://www.bilibili.com/video/1") as ydl:
        with session_for(ydl, "https://www.bilibili.com/") as session:
            good = session.prepare_request(requests.Request("GET", "https://api.bilibili.com/a"))
            bad = session.prepare_request(requests.Request("GET", "https://example.org/a"))
            assert "SESSDATA=test-secret" in good.headers["Cookie"]
            assert "Cookie" not in bad.headers
    assert cookie_file.read_text(encoding="utf-8") == content


def test_pasted_cookie_for_shortlink_scoped_to_destination():
    with downloader(LoginOptions(raw_cookie="session=test-only"), "https://v.douyin.com/abc/") as ydl:
        with session_for(ydl, "https://v.douyin.com/abc/") as session:
            good = session.prepare_request(requests.Request("GET", "https://www.douyin.com/video/1"))
            cdn = session.prepare_request(requests.Request("GET", "https://cdn.example.org/a.mp4"))
            assert "session=test-only" in good.headers["Cookie"]
            assert "Cookie" not in cdn.headers


def test_time_outside_duration_rejected(tmp_path):
    media = Media("https://example.org/v.mp4", "其他", "x", "1", duration=10)
    with pytest.raises(UserError):
        DownloadSpec(media, tmp_path, start=8, end=12).validate()
    with pytest.raises(UserError):
        DownloadSpec(media, tmp_path, start=4, end=4).validate()


def test_reject_html_masquerading_as_picture():
    with pytest.raises(UserError):
        sniff_image(b"<!doctype html>please login", "image/jpeg")


def test_safe_windows_names():
    assert safe_name("../../A:B?*") == "_.._A_B__"
    assert safe_name("CON.txt").startswith("_")
