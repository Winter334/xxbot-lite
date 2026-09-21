from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

import discord

from bot.data.realms import REALM_BY_KEYS, REALM_STAGES

if TYPE_CHECKING:
    from bot.main import XianBot


logger = logging.getLogger(__name__)
REALM_NAMES = {stage.realm_key: stage.realm_name for stage in REALM_STAGES}
_claim_locks: WeakValueDictionary[tuple[int, int], asyncio.Lock] = WeakValueDictionary()
_UNSAFE_ROLE_PERMISSIONS = discord.Permissions(
    administrator=True,
    manage_guild=True,
    manage_roles=True,
    manage_channels=True,
    kick_members=True,
    ban_members=True,
    moderate_members=True,
    manage_webhooks=True,
    manage_messages=True,
    manage_threads=True,
    manage_events=True,
    manage_expressions=True,
    view_audit_log=True,
    mention_everyone=True,
)


def _resolve_realm_roles(
    roles: list[discord.Role],
    configured_ids: dict[str, int],
    current_realm_key: str,
) -> dict[str, discord.Role]:
    resolved: dict[str, discord.Role] = {}
    configured_role_ids = set(configured_ids.values())
    for realm_key, realm_name in REALM_NAMES.items():
        role_id = configured_ids.get(realm_key)
        if role_id is not None:
            matches = [role for role in roles if role.id == role_id]
        else:
            matches = [
                role for role in roles
                if role.name == realm_name and role.id not in configured_role_ids
            ]
        if len(matches) > 1:
            raise ValueError(f"服务器有多个「{realm_name}」身份组，请管理员通过 REALM_ROLE_IDS 指定身份组 ID。")
        if not matches:
            if realm_key == current_realm_key:
                if role_id is not None:
                    raise ValueError(f"配置的「{realm_name}」身份组在本服务器不存在，请管理员检查 REALM_ROLE_IDS。")
                raise ValueError(f"服务器尚无名为「{realm_name}」的身份组，请管理员检查名称或配置 REALM_ROLE_IDS。")
            continue
        resolved[realm_key] = matches[0]
    if len({role.id for role in resolved.values()}) != len(resolved):
        raise ValueError("多个境界绑定了同一个身份组，请管理员检查 REALM_ROLE_IDS。")
    return resolved


def _role_problem(role: discord.Role) -> str | None:
    name = discord.utils.escape_markdown(role.name)
    if role.is_default() or role.managed:
        return f"「{name}」是默认或托管身份组，不能作为可领取的境界身份组。"
    if role.permissions.value & _UNSAFE_ROLE_PERMISSIONS.value:
        return f"「{name}」带有管理或敏感权限，不能通过境界领取，请管理员改用普通境界身份组。"
    if not role.is_assignable():
        return f"Bot 无法管理「{name}」，请管理员将 Bot 的最高身份组移到所有境界身份组之上。"
    return None


async def _claim_realm_role(bot: XianBot, guild: discord.Guild, user_id: int) -> str:
    async with bot.session_factory() as session:
        character = await bot.character_service.get_character_by_discord_id(session, user_id)
        if character is None:
            return "你尚未踏入仙途，请先使用 /修仙 创建角色。"
        stage = REALM_BY_KEYS.get((character.realm_key, character.stage_key))
        if stage is None:
            return "当前境界尚未开放身份组领取。"

    if guild.me is None or not guild.me.guild_permissions.manage_roles:
        return "Bot 缺少「管理身份组」权限，请联系服务器管理员。"

    roles = await guild.fetch_roles()
    try:
        resolved = _resolve_realm_roles(roles, bot.settings.realm_role_ids, stage.realm_key)
    except ValueError as exc:
        return str(exc)
    target = resolved[stage.realm_key]
    member = await guild.fetch_member(user_id)
    member_role_ids = {role.id for role in member.roles}
    cleanup_role_ids = {role.id for role in resolved.values()} | bot.settings.realm_role_cleanup_ids
    old_roles = [
        role for role in roles
        if role.id in cleanup_role_ids and role.id in member_role_ids and role.id != target.id
    ]
    has_target = target.id in member_role_ids
    for role in [target, *old_roles]:
        problem = _role_problem(role)
        if problem is not None:
            return problem
    if has_target and not old_roles:
        return f"你已拥有当前「{stage.realm_name}」境界身份组，无需重复领取。"

    reason = f"xxbot realm role claim: user={user_id}, realm={stage.realm_key}"
    # 逐个增删身份组，不用整表覆盖，以保留其他机器人或管理员的并发变更。
    if not has_target:
        await member.add_roles(target, reason=reason, atomic=True)
    if old_roles:
        try:
            await member.remove_roles(*old_roles, reason=reason, atomic=True)
        except discord.HTTPException:
            logger.warning("境界身份组清理未完成: guild=%s user=%s", guild.id, user_id, exc_info=True)
            return (
                f"已拥有「{stage.realm_name}」身份组，但旧境界身份组未全部移除。"
                "请管理员检查 Bot 权限和身份组层级后，再次领取。"
            )
    return f"已领取「{stage.realm_name}」境界身份组。"


async def claim_realm_role(bot: XianBot, interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("请在服务器内领取境界身份组，私信中无法领取。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    key = (guild.id, interaction.user.id)
    lock = _claim_locks.setdefault(key, asyncio.Lock())
    async with lock:
        try:
            message = await _claim_realm_role(bot, guild, interaction.user.id)
        except discord.Forbidden:
            logger.warning("境界身份组请求无权限: guild=%s user=%s", guild.id, interaction.user.id, exc_info=True)
            message = "领取未完成：Bot 权限不足，请管理员检查「管理身份组」权限和身份组层级后重试。"
        except discord.NotFound:
            message = "领取未完成：成员或身份组已不存在，请刷新后重试。"
        except discord.HTTPException:
            logger.warning("境界身份组请求失败: guild=%s user=%s", guild.id, interaction.user.id, exc_info=True)
            message = "Discord 暂时未能完成身份组领取，请稍后重试。"

    await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
