from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from bot.models.character import Character
from bot.services.combat_service import BattleResult, CombatService
from bot.utils.time_utils import ensure_shanghai, now_shanghai, today_shanghai

if TYPE_CHECKING:
    from bot.services.character_service import CharacterService
    from bot.services.sect_service import SectService


FACTION_NAMES = {
    "neutral": "中立",
    "righteous": "正道",
    "demonic": "魔道",
}

INFAMY_BY_REALM = {
    "lianqi": 50,
    "zhuji": 100,
    "jiedan": 200,
    "yuanying": 400,
    "huashen": 650,
    "lianxu": 950,
    "heti": 1350,
    "dacheng": 1500,
    "dujie": 2000,
    "weixian": 3000,
}

ROBBERY_SOUL_STEAL_BASIS_POINTS = 1000
ROBBERY_DEFEATED_SOUL_STEAL_BASIS_POINTS = 100
ROBBERY_LINGSHI_STEAL_BASIS_POINTS = 1000
ROBBERY_DEFEATED_LINGSHI_STEAL_BASIS_POINTS = 100

ROBBERY_BONUS_LINGSHI_BY_REALM: dict[str, int] = {
    "lianqi": 20,
    "zhuji": 50,
    "jiedan": 120,
    "yuanying": 250,
    "huashen": 400,
    "lianxu": 600,
    "heti": 900,
    "dacheng": 1300,
    "dujie": 2000,
    "weixian": 3000,
}
"""劫掠成功时系统额外注入的灵石 bonus（按目标境界）。"""

BOUNTY_LINGSHI_MULTIPLIER = 5
"""悬赏讨伐成功后每点 bounty_soul 兑换灵石的倍率。"""

REALM_LOOT_STEP_PCT = 30
"""大境界每差 1 层，战利品 ±30%。低于自己 4 层起为 0。"""


@dataclass(slots=True)
class FactionActionResult:
    success: bool
    message: str
    battle: BattleResult | None
    soul_delta: int = 0
    lingshi_delta: int = 0
    luck_delta: int = 0
    virtue_delta: int = 0
    infamy_delta: int = 0
    bounty_delta: int = 0
    target_name: str = ""
    same_faction_halved: bool = False
    defeated_penalty_applied: bool = False


@dataclass(slots=True)
class FactionTarget:
    character_id: int
    display_name: str
    faction_name: str
    realm_display: str
    luck: int
    soul: int
    bounty_soul: int


class FactionService:
    robbery_cooldown_minutes = 60
    bounty_hunt_cooldown_minutes = 30

    def __init__(self, character_service: CharacterService, combat_service: CombatService, sect_service: SectService | None = None) -> None:
        self.character_service = character_service
        self.combat_service = combat_service
        self.sect_service = sect_service

    def faction_name(self, faction_key: str) -> str:
        return FACTION_NAMES.get(faction_key, "中立")

    def sync_character_state(self, character: Character) -> None:
        today = today_shanghai()
        if character.faction != "demonic":
            return
        if character.last_bounty_growth_on is None:
            character.last_bounty_growth_on = today
            return
        if character.last_bounty_growth_on >= today:
            return
        days = (today - character.last_bounty_growth_on).days
        daily_growth = max(1, int((character.infamy or 0) // 30))
        character.bounty_soul += daily_growth * days
        character.last_bounty_growth_on = today

    def sync_many(self, characters: list[Character]) -> None:
        for character in characters:
            self.sync_character_state(character)

    def _apply_infamy_gain(self, character: Character, gain: int) -> int:
        """统一的恶名增加入口，同步刷新历史最大值。

        返回实际增加的恶名值（始终等于入参 gain，便于链式赋值）。
        历史最大值用于劫掠时计算"快速回升"的 5% 阶梯加成，
        在讨伐清空 infamy 时不会被清零（这是设计意图）。
        """
        if gain <= 0:
            return 0
        character.infamy = (character.infamy or 0) + gain
        if character.infamy > (character.historical_max_infamy or 0):
            character.historical_max_infamy = character.infamy
        return gain

    def join_faction(self, character: Character, faction_key: str) -> tuple[bool, str]:
        if faction_key not in {"righteous", "demonic"}:
            return False, "此道未立，暂不可入。"
        if character.faction != "neutral":
            return False, "你既已立场分明，此版本暂不可改换阵营。"
        character.faction = faction_key
        character.virtue = 0
        character.infamy = 0
        character.historical_max_infamy = 0
        character.bounty_soul = 0
        if faction_key == "demonic":
            character.last_bounty_growth_on = today_shanghai()
            character.last_highlight_text = "方才堕入魔道，自此行事不再顾后。"
            return True, "你已堕入魔道，自此可行劫掠之举。"
        character.last_highlight_text = "方才投入正道，愿以身讨逆诛邪。"
        return True, "你已归入正道，自此可行悬赏讨伐。"

    def robbery_cooldown_remaining(self, character: Character, *, now=None) -> timedelta | None:
        if character.last_robbery_at is None:
            return None
        current_time = ensure_shanghai(now or now_shanghai())
        ready_at = ensure_shanghai(character.last_robbery_at) + timedelta(minutes=self.robbery_cooldown_minutes)
        if ready_at <= current_time:
            return None
        return ready_at - current_time

    def bounty_hunt_cooldown_remaining(self, character: Character, *, now=None) -> timedelta | None:
        if character.last_bounty_hunt_at is None:
            return None
        current_time = ensure_shanghai(now or now_shanghai())
        ready_at = ensure_shanghai(character.last_bounty_hunt_at) + timedelta(minutes=self.bounty_hunt_cooldown_minutes)
        if ready_at <= current_time:
            return None
        return ready_at - current_time

    def can_rob(self, character: Character, *, now=None) -> tuple[bool, str | None]:
        if character.faction != "demonic":
            return False, "唯有魔道中人，方可行劫掠之举。"
        remaining = self.robbery_cooldown_remaining(character, now=now)
        if remaining is not None:
            minutes = max(1, int(remaining.total_seconds() // 60))
            return False, f"劫掠余波未平，还需再等 {minutes} 分钟。"
        return True, None

    def robbery_defeat_penalty_active(self, character: Character, *, now=None) -> bool:
        if character.last_bounty_defeated_on is None:
            return False
        current_time = ensure_shanghai(now or now_shanghai())
        return character.last_bounty_defeated_on == current_time.date()

    def robbery_stolen_lingshi(self, target_lingshi: int, *, defeated_penalty: bool) -> int:
        if target_lingshi <= 0:
            return 0
        basis_points = (
            ROBBERY_DEFEATED_LINGSHI_STEAL_BASIS_POINTS if defeated_penalty else ROBBERY_LINGSHI_STEAL_BASIS_POINTS
        )
        return min(target_lingshi, max(1, target_lingshi * basis_points // 10_000))

    def robbery_bonus_lingshi(self, target: Character, *, same_faction_halved: bool) -> int:
        bonus = ROBBERY_BONUS_LINGSHI_BY_REALM.get(target.realm_key, 20)
        if same_faction_halved:
            bonus //= 2
        return bonus

    def robbery_bonus_soul(self, target: Character, *, same_faction_halved: bool) -> int:
        bonus_soul = max(2, INFAMY_BY_REALM.get(target.realm_key, 50) // 2)
        if same_faction_halved:
            bonus_soul //= 2
        return bonus_soul

    def robbery_stolen_soul(self, target_soul: int, *, defeated_penalty: bool) -> int:
        if target_soul <= 0:
            return 0
        basis_points = (
            ROBBERY_DEFEATED_SOUL_STEAL_BASIS_POINTS if defeated_penalty else ROBBERY_SOUL_STEAL_BASIS_POINTS
        )
        return min(target_soul, max(1, target_soul * basis_points // 10_000))

    @staticmethod
    def realm_loot_multiplier_pct(actor_realm_index: int, target_realm_index: int) -> int:
        delta = int(target_realm_index or 1) - int(actor_realm_index or 1)
        return max(0, 100 + delta * REALM_LOOT_STEP_PCT)

    def _scale_loot(self, amount: int, actor: Character, target: Character) -> int:
        if amount <= 0:
            return 0
        return amount * self.realm_loot_multiplier_pct(actor.realm_index, target.realm_index) // 100

    def _apply_bounty_on_infamy_gain(self, character: Character) -> int:
        gain = max(0, int(character.infamy or 0) // 10)
        if gain <= 0:
            return 0
        character.bounty_soul = (character.bounty_soul or 0) + gain
        return gain

    def can_bounty_hunt(self, character: Character, *, now=None) -> tuple[bool, str | None]:
        if character.faction != "righteous":
            return False, "唯有正道修士，方可承悬赏而行。"
        remaining = self.bounty_hunt_cooldown_remaining(character, now=now)
        if remaining is not None:
            minutes = max(1, int(remaining.total_seconds() // 60))
            return False, f"方才才出手过一场，还需再等 {minutes} 分钟。"
        return True, None

    def list_bounty_targets(self, characters: list[Character], *, limit: int = 10) -> list[FactionTarget]:
        demonic = [char for char in characters if char.faction == "demonic" and (char.bounty_soul or 0) > 0]
        demonic.sort(key=lambda char: (-(char.bounty_soul or 0), -(char.infamy or 0), char.id))
        return [self._target_view(char) for char in demonic[:limit]]

    def list_robbery_targets(self, characters: list[Character], actor: Character, *, limit: int | None = None) -> list[FactionTarget]:
        targets = [char for char in characters if char.id != actor.id]
        targets.sort(key=lambda char: (char.current_ladder_rank, -char.realm_index, char.id))
        if limit is not None:
            targets = targets[:limit]
        return [self._target_view(char) for char in targets]

    def _target_view(self, character: Character) -> FactionTarget:
        snapshot = self.character_service.build_snapshot(character)
        return FactionTarget(
            character_id=character.id,
            display_name=snapshot.player_name,
            faction_name=snapshot.faction_name,
            realm_display=snapshot.realm_display,
            luck=snapshot.luck,
            soul=snapshot.soul_shards,
            bounty_soul=snapshot.bounty_soul,
        )

    def righteous_title(self, characters: list[Character], character: Character) -> str:
        righteous = [char for char in characters if char.faction == "righteous" and (char.virtue or 0) > 0]
        righteous.sort(key=lambda char: (-(char.virtue or 0), char.id))
        for index, entry in enumerate(righteous[:5], start=1):
            if entry.id != character.id:
                continue
            if index == 1:
                return "正道领袖"
            return "正道巨擘"
        return ""

    def demonic_title(self, characters: list[Character], character: Character) -> str:
        demonic = [char for char in characters if char.faction == "demonic" and (char.infamy or 0) > 0]
        demonic.sort(key=lambda char: (-(char.infamy or 0), char.id))
        for index, entry in enumerate(demonic[:5], start=1):
            if entry.id != character.id:
                continue
            if index == 1:
                return "魔道魁首"
            return "魔道巨擘"
        return ""

    def challenge_bounty(self, hunter: Character, target: Character, *, now=None) -> FactionActionResult:
        self.sync_character_state(hunter)
        self.sync_character_state(target)
        allowed, reason = self.can_bounty_hunt(hunter, now=now)
        if not allowed:
            return FactionActionResult(False, reason or "当前不可讨伐。", None)
        if target.faction != "demonic":
            return FactionActionResult(False, "此人并非魔道，不在悬赏之列。", None)
        if (target.bounty_soul or 0) <= 0:
            return FactionActionResult(False, "此人头上暂无悬赏，不值得此刻出手。", None)

        current_time = ensure_shanghai(now or now_shanghai())
        hunter.last_bounty_hunt_at = current_time
        battle = self.combat_service.run_battle(
            self.character_service.build_combatant(hunter, title=hunter.title),
            self.character_service.build_combatant(target, title=target.title),
            scene_tags=("scene_bounty",),
        )
        if not battle.challenger_won:
            hunter.last_highlight_text = f"方才追剿 {target.player.display_name} 未成。"
            return FactionActionResult(False, "你此番讨伐未能成事，悬赏仍在对方头上。", battle, target_name=target.player.display_name)

        raw_soul = target.bounty_soul or 0
        reward_soul = self._scale_loot(raw_soul, hunter, target)
        reward_lingshi = self._scale_loot(raw_soul * BOUNTY_LINGSHI_MULTIPLIER, hunter, target)
        reward_virtue = self._scale_loot(raw_soul, hunter, target)
        reward_luck = self._scale_loot(10, hunter, target)
        if hunter.artifact is not None and reward_soul > 0:
            hunter.artifact.soul_shards += reward_soul
        if reward_lingshi > 0:
            hunter.lingshi += reward_lingshi
        hunter.virtue += reward_virtue
        hunter.luck += reward_luck
        target.bounty_soul = 0
        # 讨伐成功清空当前恶名（避免被堵门反复刷成资源包）；
        # historical_max_infamy 保留，作为下次再作恶时"快速回升"的依据
        cleared_infamy = target.infamy or 0
        target.infamy = 0
        target.last_bounty_defeated_on = today_shanghai()
        hunter.last_highlight_text = f"方才承悬赏讨伐 {target.player.display_name} 得手。"
        target.last_highlight_text = f"方才被正道修士 {hunter.player.display_name} 讨伐，恶名一笔勾销。"
        _ = cleared_infamy  # 当前不计入战利品，保留变量备扩展
        if self.sect_service is not None:
            self.sect_service.record_task_event(hunter, "faction_bounty_win")
        return FactionActionResult(
            True,
            "讨伐得手，对方悬赏已全部兑现。",
            battle,
            soul_delta=reward_soul,
            lingshi_delta=reward_lingshi,
            luck_delta=reward_luck,
            virtue_delta=reward_virtue,
            bounty_delta=-raw_soul,
            target_name=target.player.display_name,
        )

    def rob(self, robber: Character, target: Character, *, now=None) -> FactionActionResult:
        allowed, reason = self.can_rob(robber, now=now)
        if not allowed:
            return FactionActionResult(False, reason or "当前不可劫掠。", None)
        if robber.id == target.id:
            return FactionActionResult(False, "你还不至于对自己下手。", None)

        current_time = ensure_shanghai(now or now_shanghai())
        robber.last_robbery_at = current_time
        battle = self.combat_service.run_battle(
            self.character_service.build_combatant(robber, title=robber.title),
            self.character_service.build_combatant(target, title=target.title),
            scene_tags=("scene_robbery",),
        )
        same_faction_halved = target.faction == "demonic"
        defeated_penalty_applied = self.robbery_defeat_penalty_active(robber, now=current_time)
        if not battle.challenger_won:
            self._apply_infamy_gain(robber, 3)
            self._apply_bounty_on_infamy_gain(robber)
            robber.last_highlight_text = f"方才劫掠 {target.player.display_name} 失手。"
            return FactionActionResult(
                False,
                "劫掠失手，未得分毫，反倒又添了些恶名。",
                battle,
                infamy_delta=3,
                target_name=target.player.display_name,
                same_faction_halved=same_faction_halved,
                defeated_penalty_applied=defeated_penalty_applied,
            )

        target_soul = (target.artifact.soul_shards or 0) if target.artifact is not None else 0
        stolen_soul = self.robbery_stolen_soul(target_soul, defeated_penalty=defeated_penalty_applied)
        target_lingshi = target.lingshi or 0
        stolen_lingshi = self.robbery_stolen_lingshi(target_lingshi, defeated_penalty=defeated_penalty_applied)
        stolen_luck = max(0, (target.luck or 0) * 15 // 100)
        if same_faction_halved:
            stolen_soul //= 2
            stolen_lingshi //= 2
            stolen_luck //= 2
        stolen_soul = self._scale_loot(stolen_soul, robber, target)
        stolen_lingshi = self._scale_loot(stolen_lingshi, robber, target)
        stolen_luck = self._scale_loot(stolen_luck, robber, target)

        actual_stolen_soul = 0
        if stolen_soul > 0 and target.artifact is not None and robber.artifact is not None:
            take_soul = min(stolen_soul, target.artifact.soul_shards or 0)
            target.artifact.soul_shards -= take_soul
            robber.artifact.soul_shards += take_soul
            actual_stolen_soul = take_soul
        actual_stolen_lingshi = 0
        if stolen_lingshi > 0:
            take_lingshi = min(stolen_lingshi, target.lingshi or 0)
            target.lingshi -= take_lingshi
            robber.lingshi += take_lingshi
            actual_stolen_lingshi = take_lingshi
        if stolen_luck > 0:
            take_luck = min(stolen_luck, target.luck or 0)
            target.luck -= take_luck
            robber.luck += take_luck
            stolen_luck = take_luck

        # 恶名增量 = 基础（按目标境界） + 历史最大恶名 × 5%（快速回升机制）
        # 讨伐清空当前 infamy 但 historical_max_infamy 保留，再次作恶时阶梯式回升
        base_infamy_gain = INFAMY_BY_REALM.get(target.realm_key, 50)
        escalation = int((robber.historical_max_infamy or 0) * 0.05)
        infamy_gain = base_infamy_gain + escalation
        # 系统额外注入器魂和灵石 bonus
        bonus_soul = self._scale_loot(
            self.robbery_bonus_soul(target, same_faction_halved=same_faction_halved),
            robber,
            target,
        )
        actual_bonus_soul = 0
        if bonus_soul > 0 and robber.artifact is not None:
            robber.artifact.soul_shards += bonus_soul
            actual_bonus_soul = bonus_soul
        bonus_lingshi = self._scale_loot(
            self.robbery_bonus_lingshi(target, same_faction_halved=same_faction_halved),
            robber,
            target,
        )
        robber.lingshi += bonus_lingshi
        self._apply_infamy_gain(robber, infamy_gain)
        self._apply_bounty_on_infamy_gain(robber)
        robber.last_highlight_text = f"方才劫掠 {target.player.display_name} 得手。"
        target.last_highlight_text = f"方才遭 {robber.player.display_name} 劫掠。"
        if self.sect_service is not None:
            self.sect_service.record_task_event(robber, "faction_robbery_win")
        return FactionActionResult(
            True,
            "劫掠得手，所夺资源已尽归己身。",
            battle,
            soul_delta=actual_stolen_soul + actual_bonus_soul,
            lingshi_delta=actual_stolen_lingshi + bonus_lingshi,
            luck_delta=stolen_luck,
            infamy_delta=infamy_gain,
            target_name=target.player.display_name,
            same_faction_halved=same_faction_halved,
            defeated_penalty_applied=defeated_penalty_applied,
        )
