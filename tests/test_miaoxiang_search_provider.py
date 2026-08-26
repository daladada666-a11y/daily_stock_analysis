# -*- coding: utf-8 -*-
"""Tests for the Miaoxiang news-search provider wiring (PR #2248)."""

from __future__ import annotations

from src.search_service import SearchService


def _service(**overrides) -> SearchService:
    kwargs = {"searxng_base_urls": ["http://searxng.local:8080"], "mx_apikey": "mx-test-key"}
    kwargs.update(overrides)
    return SearchService(**kwargs)


class TestCapabilityProbe:
    def test_mx_only_config_enables_search_capability(self, monkeypatch):
        """仅配置 MX_APIKEY 时,大盘复盘等入口不得把搜索能力判定为未启用。"""
        from src.config import Config

        config = Config(
            anspire_api_keys=[], bocha_api_keys=[], minimax_api_keys=[],
            tavily_api_keys=[], brave_api_keys=[], serpapi_keys=[],
            searxng_base_urls=[], searxng_public_instances_enabled=False,
            mx_apikey="mx-test-key",
        )
        assert config.has_search_capability_enabled() is True

    def test_no_search_config_disables_capability(self, monkeypatch):
        from src.config import Config

        config = Config(
            anspire_api_keys=[], bocha_api_keys=[], minimax_api_keys=[],
            tavily_api_keys=[], brave_api_keys=[], serpapi_keys=[],
            searxng_base_urls=[], searxng_public_instances_enabled=False,
            mx_apikey=None,
        )
        assert config.has_search_capability_enabled() is False


class TestSubprocessKwargs:
    def test_constructor_kwargs_carry_mx_apikey(self):
        """bounded 题材搜索在子进程中重建 SearchService 时必须带上 mx_apikey。"""
        service = _service()
        kwargs = service._constructor_kwargs
        assert kwargs["mx_apikey"] == "mx-test-key"

        rebuilt = SearchService(**kwargs)
        names = [p.name for p in rebuilt._providers]
        assert "Miaoxiang" in names

    def test_provider_sits_after_searxng(self):
        service = _service()
        names = [p.name for p in service._providers]
        assert names.index("SearXNG") < names.index("Miaoxiang")

    def test_no_mx_apikey_no_provider(self):
        service = _service(mx_apikey=None)
        assert all(p.name != "Miaoxiang" for p in service._providers)


class TestNewsPayloadContract:
    """news-search 响应契约:公开文档形态(trunk/单层 data)与实测形态(content/双层 data)都必须可用。"""

    PUBLIC_SHAPE = {
        "status": 0,
        "message": "",
        "data": {
            "llmSearchResponse": {
                "data": [
                    {
                        "title": "关于增持股份的公告",
                        "trunk": "控股股东增持公司股份触及1%整数倍",
                        "date": "2026-08-19 18:00",
                        "informationType": "ANNOUNCEMENT",
                    },
                ]
            }
        },
    }

    LIVE_SHAPE = {
        "status": 0,
        "message": "",
        "data": {"data": {"llmSearchResponse": {"data": [
            {
                "title": "中报净利润发布",
                "content": "2026年中报净利润7258万元",
                "date": "2026-08-17",
                "informationType": "NEWS",
            },
        ]}}},
    }

    def _provider(self):
        from src.search_service import MiaoxiangSearchProvider
        return MiaoxiangSearchProvider(["mx-test-key"])

    def test_public_documented_shape_with_trunk(self, monkeypatch):
        provider = self._provider()
        monkeypatch.setattr(
            "src.search_service.requests.post",
            lambda *a, **k: _FakeResponse(self.PUBLIC_SHAPE),
        )
        r = provider.search("测试股份 最新消息", max_results=5)
        assert r.success and len(r.results) == 1
        assert "增持" in r.results[0].snippet  # 正文来自 trunk
        assert r.results[0].published_date == "2026-08-19"

    def test_live_verified_shape_with_content(self, monkeypatch):
        provider = self._provider()
        monkeypatch.setattr(
            "src.search_service.requests.post",
            lambda *a, **k: _FakeResponse(self.LIVE_SHAPE),
        )
        r = provider.search("测试股份 最新消息", max_results=5)
        assert r.success and "净利润" in r.results[0].snippet

    def test_error_status_fails_open(self, monkeypatch):
        provider = self._provider()
        monkeypatch.setattr(
            "src.search_service.requests.post",
            lambda *a, **k: _FakeResponse({"status": 1001, "message": "鉴权失败"}),
        )
        r = provider.search("测试股份", max_results=5)
        assert not r.success and "1001" in (r.error_message or "")

    def test_empty_items_fails_open(self, monkeypatch):
        provider = self._provider()
        monkeypatch.setattr(
            "src.search_service.requests.post",
            lambda *a, **k: _FakeResponse({"status": 0, "data": {"llmSearchResponse": {"data": []}}}),
        )
        r = provider.search("测试股份", max_results=5)
        assert not r.success


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload
