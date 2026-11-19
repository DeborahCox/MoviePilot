"""
下载链到 qBittorrent SDK 的分类与自动管理参数回归。
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.chain.download import DownloadChain
from app.domain.context import Context, MediaInfo, MusicInfo
from app.modules.qbittorrent import QbittorrentModule
from app.modules.qbittorrent.qbittorrent import Qbittorrent
from app.schemas.context import MediaInfo as SchemaMediaInfo
from app.schemas.context import TorrentInfo as SchemaTorrentInfo
from app.schemas.types import MediaType


def _download_api_module():
    """
    端点模块依赖 Python 3.14 的延迟注解语义；本地低版本仅跳过端点用例。
    """
    if sys.version_info < (3, 14):
        pytest.skip("下载端点模块需要 Python 3.14 的延迟注解语义")
    from app.api.endpoints import download as download_api

    return download_api


@pytest.fixture
def qb_download():
    """
    执行真实下载链、模块和客户端逻辑，仅替换 SDK 与提交后的结算。
    """
    torrent_hash = "a" * 40
    server = Qbittorrent.__new__(Qbittorrent)
    server.qbc = Mock()
    server.qbc.torrents_add.return_value = {"added_torrent_ids": [torrent_hash]}
    server._sequentail = False
    server._first_last_piece = False
    server._force_resume = False
    server.delete_torrents_tag = Mock(return_value=True)
    server.get_content_layout = Mock(return_value="Original")
    module = QbittorrentModule.__new__(QbittorrentModule)
    module.get_instance = Mock(return_value=server)
    module.normalize_path = Mock(side_effect=lambda path, _name: path.as_posix())
    chain = DownloadChain.__new__(DownloadChain)
    chain.run_module = lambda method, **kwargs: getattr(module, method)(**kwargs)
    chain._settle_accepted_download = Mock()
    chain._record_rejected_download = Mock()
    return chain, server, torrent_hash


@pytest.mark.parametrize("auto_manage", [True, False])
@pytest.mark.parametrize(
    "media_type,media_source,music_type,category,expected_category",
    [
        (MediaType.MUSIC, "doubanmusic", "album", "", "音乐"),
        (MediaType.MUSIC, "musicbrainz", "recording", "", "音乐"),
        (MediaType.MUSIC, None, "album", "", "音乐"),
        (MediaType.MUSIC, None, "artist", "", "音乐"),
        (MediaType.MUSIC, "doubanmusic", "album", "音乐/古典", "音乐/古典"),
        (MediaType.MOVIE, "douban", None, "外语电影", "外语电影"),
        (MediaType.TV, "tmdb", None, "国产剧", "国产剧"),
        (MediaType.MOVIE, "tmdb", None, "", None),
        (MediaType.TV, "tmdb", None, "", None),
    ],
)
def test_download_category_reaches_qbittorrent_sdk(
    qb_download, auto_manage, media_type, media_source, music_type, category, expected_category,
):
    """
    音乐缺分类时兜底，已有分类优先，自动管理关闭时仍显式使用指定路径。
    """
    chain, server, torrent_hash = qb_download
    server._category = auto_manage
    if media_type == MediaType.MUSIC:
        media = MusicInfo(
            media_source=media_source,
            media_id="1001" if media_source else None,
            music_type=music_type,
            title="示例专辑",
            album_type="Album",
            metadata_category="Album",
            library_category=category,
        )
    else:
        media = MediaInfo(
            type=media_type, media_source=media_source, media_id="1001",
            title="示例影视", library_category=category,
        )
    original_media = media.to_dict()
    prepared = SimpleNamespace(
        torrent_content=f"magnet:?xt=urn:btih:{torrent_hash}",
        torrent=SimpleNamespace(site_cookie=""),
        download_dir=Path("/downloads/requested"),
        media=media,
        site_downloader=None,
    )

    result, _message = chain._submit_prepared_download(
        prepared=prepared, context=Context(media_info=media), episodes=None,
        channel=None, source="Manual", downloader="qb", userid=None, username=None,
        label=None, custom_words=None, governance=None,
    )

    assert result == torrent_hash
    server.qbc.torrents_add.assert_called_once()
    submitted = server.qbc.torrents_add.call_args.kwargs
    assert submitted["category"] == (expected_category if auto_manage else None)
    assert submitted["use_auto_torrent_management"] is bool(auto_manage and expected_category)
    assert submitted["save_path"] == "/downloads/requested"
    assert media.to_dict() == original_media
    chain._settle_accepted_download.assert_called_once()
    chain._record_rejected_download.assert_not_called()


def test_manual_category_overrides_downloader_category_without_touching_media(qb_download):
    """
    手动分类只覆盖提交给下载器的值，媒体目录分类保持原样。
    """
    chain, server, torrent_hash = qb_download
    server._category = True
    media = MediaInfo(
        type=MediaType.MOVIE, media_source="douban", media_id="1001",
        title="示例影视", library_category="外语电影",
    )
    original_media = media.to_dict()
    prepared = SimpleNamespace(
        torrent_content=f"magnet:?xt=urn:btih:{torrent_hash}",
        torrent=SimpleNamespace(site_cookie=""),
        download_dir=Path("/downloads/requested"),
        media=media,
        site_downloader=None,
    )

    result, _message = chain._submit_prepared_download(
        prepared=prepared, context=Context(media_info=media), episodes=None,
        channel=None, source="Manual", downloader="qb", userid=None, username=None,
        label=None, custom_words=None, governance=None,
        download_category="  精选电影  ",
    )

    assert result == torrent_hash
    submitted = server.qbc.torrents_add.call_args.kwargs
    assert submitted["category"] == "精选电影"
    assert media.to_dict() == original_media


def test_download_endpoint_forwards_custom_category(monkeypatch):
    """
    含媒体信息的下载接口把自定义分类交给下载链。
    """
    download_api = _download_api_module()
    chain = Mock()
    chain.download_single.return_value = "task-1"
    monkeypatch.setattr(download_api, "DownloadChain", lambda: chain)

    response = download_api.download(
        media_in=SchemaMediaInfo(type="电影", title="示例影视"),
        torrent_in=SchemaTorrentInfo(title="示例影视.2024", category="movie"),
        downloader="qb",
        save_path="/downloads/requested",
        download_category="精选电影",
        current_user=SimpleNamespace(name="tester"),
    )

    assert response.success is True
    assert chain.download_single.call_args.kwargs["download_category"] == "精选电影"


def test_add_endpoint_forwards_custom_category(monkeypatch):
    """
    不含媒体信息的下载接口同样传递自定义分类。
    """
    download_api = _download_api_module()
    chain = Mock()
    chain.download_single.return_value = "task-2"
    media_chain = Mock()
    media_chain.recognize_by_meta.return_value = MediaInfo(type=MediaType.MOVIE, title="示例影视")
    monkeypatch.setattr(download_api, "DownloadChain", lambda: chain)
    monkeypatch.setattr(download_api, "MediaChain", lambda: media_chain)

    response = download_api.add(
        torrent_in=SchemaTorrentInfo(title="示例影视.2024", category="movie"),
        download_category="精选电影",
        current_user=SimpleNamespace(name="tester"),
    )

    assert response.success is True
    assert chain.download_single.call_args.kwargs["download_category"] == "精选电影"
