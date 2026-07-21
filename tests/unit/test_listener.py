"""测试 listener 的 mentions 提取逻辑。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.plugins.listener import _extract_mentions


pytestmark = pytest.mark.asyncio


class TestExtractMentions:
    """验证 _extract_mentions 能同时处理真实 at segment 和文本 @昵称。"""

    @pytest.fixture
    def bot(self):
        bot = MagicMock()
        bot.self_id = 3941405113
        bot.get_group_member_info = AsyncMock(return_value={
            "user_id": 1466626219,
            "nickname": "陈哲渊",
            "card": "",
        })
        return bot

    @pytest.fixture
    def make_event(self):
        def _make(segments, plaintext, group_id=1077203419):
            event = MagicMock()
            event.group_id = group_id
            event.get_message.return_value = segments
            event.get_plaintext.return_value = plaintext
            return event
        return _make

    async def test_real_at_segment_resolves_to_nickname(self, bot, make_event):
        seg = MagicMock()
        seg.type = "at"
        seg.data = {"qq": "1466626219"}
        event = make_event([seg], "")

        mentions = await _extract_mentions(bot, event)
        assert mentions == ["陈哲渊"]
        bot.get_group_member_info.assert_awaited_once_with(
            group_id=1077203419, user_id=1466626219, no_cache=False
        )

    async def test_textual_mention_is_extracted(self, bot, make_event):
        event = make_event([], "开启一个午餐事件，@陈哲渊 是参与者")

        mentions = await _extract_mentions(bot, event)
        assert "陈哲渊" in mentions
        # 没有真实 at segment，不应调用群成员接口
        bot.get_group_member_info.assert_not_awaited()

    async def test_bot_self_is_excluded(self, bot, make_event):
        seg = MagicMock()
        seg.type = "at"
        seg.data = {"qq": str(bot.self_id)}
        event = make_event([seg], "")

        mentions = await _extract_mentions(bot, event)
        assert mentions == []

    async def test_at_bot_text_is_excluded(self, bot, make_event):
        event = make_event([], "@bot 你好")

        mentions = await _extract_mentions(bot, event)
        assert mentions == []

    async def test_deduplicates_segment_and_text(self, bot, make_event):
        seg = MagicMock()
        seg.type = "at"
        seg.data = {"qq": "1466626219"}
        event = make_event([seg], "@陈哲渊 是参与者")

        mentions = await _extract_mentions(bot, event)
        assert sorted(mentions) == ["陈哲渊"]
