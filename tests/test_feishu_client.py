# -*- coding: utf-8 -*-
import pytest
from feishu_meeting_tool.feishu_client import FeishuClient


def test_extract_minute_token():
    url1 = "https://feishu.cn/minutes/obcn1234567890abcdef"
    url2 = "https://bytedance.feishu.cn/minutes/obcn_test_token_999"
    token_direct = "obcn_direct_token"

    assert FeishuClient.extract_minute_token(url1) == "obcn1234567890abcdef"
    assert FeishuClient.extract_minute_token(url2) == "obcn_test_token_999"
    assert FeishuClient.extract_minute_token(token_direct) == "obcn_direct_token"


def test_feishu_client_init():
    client = FeishuClient(
        app_id="cli_test123",
        app_secret="sec_test456",
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
    )
    assert client.app_id == "cli_test123"
    assert client.app_secret == "sec_test456"
    assert client.webhook_url == "https://open.feishu.cn/open-apis/bot/v2/hook/test"
