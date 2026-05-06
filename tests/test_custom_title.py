from __future__ import annotations

import pytest

from bot.data.realms import get_stage
from bot.views.panel import _CUSTOM_TITLE_PATTERN


def _set_stage(character, realm_key: str, stage_key: str) -> None:
    stage = get_stage(realm_key, stage_key)
    character.realm_key = stage.realm_key
    character.realm_index = stage.realm_index
    character.stage_key = stage.stage_key
    character.stage_index = stage.stage_index


@pytest.mark.asyncio
async def test_custom_title_overrides_system_title(session_factory, services) -> None:
    """自定义尊号优先级高于系统称号 (独断万古等)。"""
    async with session_factory() as session:
        result = await services.character.get_or_create_character(session, 2001, "九霄子")
        character = result.character
        _set_stage(character, "dujie", "early")
        # 模拟登顶论道榜首 -> 系统会判定为「独断万古」
        character.current_ladder_rank = 1
        await session.commit()

        title, _, _ = await services.ranking.get_titles(session, character)
        assert title == "独断万古"

        # 设置自定义尊号后应优先显示
        character.custom_title = "青冥剑主"
        await session.commit()
        title2, _, _ = await services.ranking.get_titles(session, character)
        assert title2 == "青冥剑主"


@pytest.mark.asyncio
async def test_custom_title_overrides_default_title(session_factory, services) -> None:
    """无系统称号时，自定义尊号也应覆盖系统默认判定。

    构造两个角色：A 是当前测试主角，B 在所有维度都强于 A，
    确保 A 拿不到任何系统称号 (含「同境界第一人」)。
    """
    async with session_factory() as session:
        # B 角色（更强）：渡劫期、战力榜首、塔顶、法宝最强
        b_res = await services.character.get_or_create_character(session, 2099, "强者")
        b_char = b_res.character
        _set_stage(b_char, "dujie", "early")
        b_char.historical_highest_floor = 999
        b_char.cultivation = 9_999_999

        # A 角色（主角）：渡劫期但各维度都不是第一
        a_res = await services.character.get_or_create_character(session, 2002, "白鹿")
        a_char = a_res.character
        _set_stage(a_char, "dujie", "early")
        a_char.historical_highest_floor = 1
        a_char.cultivation = 100
        a_char.current_ladder_rank = 999  # 避开 ladder 判定
        b_char.current_ladder_rank = 1
        await session.commit()

        title, _, _ = await services.ranking.get_titles(session, a_char)
        assert title == "未立尊号"

        a_char.custom_title = "鹿野闲人"
        await session.commit()
        title2, _, _ = await services.ranking.get_titles(session, a_char)
        assert title2 == "鹿野闲人"


@pytest.mark.asyncio
async def test_custom_title_blank_does_not_override(session_factory, services) -> None:
    """空白/None 自定义尊号不应影响系统判定。"""
    async with session_factory() as session:
        result = await services.character.get_or_create_character(session, 2003, "残雪")
        character = result.character
        _set_stage(character, "dujie", "early")
        character.current_ladder_rank = 2  # 万战称尊
        character.custom_title = "   "  # 全空白
        await session.commit()

        title, _, _ = await services.ranking.get_titles(session, character)
        assert title == "万战称尊"


def test_custom_title_pattern_accepts_valid() -> None:
    """合法字符：中文 / 英文 / 数字。"""
    assert _CUSTOM_TITLE_PATTERN.match("青冥剑主")
    assert _CUSTOM_TITLE_PATTERN.match("LordX")
    assert _CUSTOM_TITLE_PATTERN.match("剑主007")
    assert _CUSTOM_TITLE_PATTERN.match("Sword剑")


def test_custom_title_pattern_rejects_invalid() -> None:
    """非法字符：空格 / 标点 / emoji / 特殊符号。"""
    assert not _CUSTOM_TITLE_PATTERN.match("青冥 剑主")  # 空格
    assert not _CUSTOM_TITLE_PATTERN.match("青冥·剑主")  # 中点
    assert not _CUSTOM_TITLE_PATTERN.match("青冥剑主!")  # 标点
    assert not _CUSTOM_TITLE_PATTERN.match("剑主🗡️")  # emoji
    assert not _CUSTOM_TITLE_PATTERN.match("")  # 空


@pytest.mark.asyncio
async def test_apply_custom_title_full_flow(session_factory, services, monkeypatch) -> None:
    """端到端测试 apply_custom_title：境界校验 + 字符校验 + 一次性 + 写入。"""
    from bot.views import panel as panel_module

    # 构造一个最小化的 bot mock，只暴露 apply_custom_title 需要的属性
    class FakeBot:
        def __init__(self, sf, char_svc) -> None:
            self.session_factory = sf
            self.character_service = char_svc

    async with session_factory() as session:
        result = await services.character.get_or_create_character(session, 2004, "云隐")
        character = result.character
        _set_stage(character, "yuanying", "early")  # 元婴期，未达渡劫
        await session.commit()

    bot = FakeBot(session_factory, services.character)

    # 1) 境界不足
    ok, msg, _ = await panel_module.apply_custom_title(bot, 2004, "云隐道君")
    assert ok is False
    assert "渡劫" in msg

    # 升到渡劫
    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 2004)
        _set_stage(character, "dujie", "early")
        await session.commit()

    # 2) 非法字符
    ok2, msg2, _ = await panel_module.apply_custom_title(bot, 2004, "云隐 道君")
    assert ok2 is False
    assert "中文" in msg2 or "英文" in msg2

    # 3) 空白
    ok3, msg3, _ = await panel_module.apply_custom_title(bot, 2004, "   ")
    assert ok3 is False
    assert "不可为空" in msg3

    # 4) 超长 (>12 字)
    ok4, msg4, _ = await panel_module.apply_custom_title(bot, 2004, "字" * 13)
    assert ok4 is False
    assert "12" in msg4

    # 5) 合法设置成功
    ok5, msg5, broadcasts = await panel_module.apply_custom_title(bot, 2004, "云隐道君")
    assert ok5 is True
    assert "云隐道君" in msg5
    assert any("云隐道君" in b for b in broadcasts)

    # 6) 验证落库
    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 2004)
        assert character.custom_title == "云隐道君"

    # 7) 一次性：再次设置应被拒绝
    ok7, msg7, _ = await panel_module.apply_custom_title(bot, 2004, "新尊号")
    assert ok7 is False
    assert "不可再改" in msg7
