from __future__ import annotations

from dataclasses import dataclass, field, replace
import random

from bot.data.artifact_affixes import ArtifactAffixEntry, get_artifact_affix_definition, get_default_burn_atk_pct
from bot.data.spirits import (
    LINGYONG_CLEANSE_STACKS_PER_DEBUFF,
    LINGYONG_NORMAL_STACK_CAP,
    LINGYONG_STACK_CAP,
    LINGYONG_TRUE_DAMAGE_ATK_CAP_PCT,
    LINGYONG_TRUE_DAMAGE_PER_STACK_PCT,
    SpiritPowerEntry,
)
from bot.utils.formatters import clamp, format_big_number


def _roll(rolls: dict[str, int | float], key: str, default: int = 0) -> int:
    return int(rolls.get(key, default))


@dataclass(slots=True)
class CombatantSnapshot:
    name: str
    atk: int
    defense: int
    agility: int
    max_hp: int
    title: str = ""
    fate_name: str = ""
    affixes: tuple[ArtifactAffixEntry, ...] = ()
    spirit_power: SpiritPowerEntry | None = None
    realm_index: int = 1
    damage_dealt_basis_points: int = 0
    damage_taken_basis_points: int = 0
    damage_reduction_basis_points: int = 0
    versus_higher_realm_damage_basis_points: int = 0
    base_resilience: int = 0  # 境界基础韧性 %（0-36），机制性必杀与灵涌真伤豁免


@dataclass(slots=True)
class ActionLog:
    round_no: int
    actor_name: str
    target_name: str
    dodged: bool
    critical: bool
    damage: int
    target_hp_after: int
    text: str | None = None
    shield_after: int | None = None


@dataclass(slots=True)
class BattleResult:
    challenger_won: bool
    winner_name: str
    loser_name: str
    rounds: int
    reached_round_limit: bool
    logs: list[ActionLog]
    challenger_max_hp: int
    defender_max_hp: int
    challenger_hp_after: int
    defender_hp_after: int


class _DamageSource:
    ATTACK = "attack"
    BURN = "burn"
    SPIRIT = "spirit"
    REFLECT = "reflect"
    COUNTER = "counter"


@dataclass(slots=True)
class _StatusEffect:
    name: str
    stacks: int = 1
    duration: int | None = None
    atk_pct: int = 0
    defense_pct: int = 0
    agility_pct: int = 0
    damage_taken_pct: int = 0
    damage_reduction_pct: int = 0
    damage_dealt_pct: int = 0
    heal_received_pct: int = 0
    burn_bonus_pct: int = 0
    burn_pct: int = 0
    remaining_hits: int | None = None
    is_debuff: bool = False
    source: "_CombatState | None" = None
    crit_bonus_pct: int = 0
    crit_damage_pct: int = 0
    dodge_bonus_pct: int = 0
    shield: int = 0
    guarantee_crit: bool = False
    is_relight: bool = False
    bonus_damage: int = 0  # 春生固定追打载体：下次攻击附加固定伤害
    backlash_pct: int = 0  # 裂铠碎盾反伤百分比（仅"裂铠"状态使用，便于多件法宝独立结算）
    cleanseable: bool = True  # 是否可被净化/涤世清除；死兆等特殊标记不可净化
    active_from_round: int = 0  # 狂锋/雷引等延迟增伤从指定回合起进入伤害公式

    def is_active(self) -> bool:
        stacks_ok = self.stacks > 0
        duration_ok = self.duration is None or self.duration > 0
        hits_ok = self.remaining_hits is None or self.remaining_hits > 0
        return stacks_ok and duration_ok and hits_ok


@dataclass(slots=True, frozen=True)
class _DamageProfile:
    """伤害类型描述：决定该笔伤害走哪些计算管线。"""

    can_be_buffed: bool = True   # 是否吃 actor 的增伤（damage_dealt_pct + damage_dealt_basis_points）
    can_be_vulned: bool = True   # 是否吃 target 的承伤（damage_taken_pct + damage_taken_basis_points）
    can_be_reduced: bool = True  # 是否吃 target 的减伤（damage_reduction_pct + damage_reduction_basis_points）
    can_be_shielded: bool = True # 是否被护盾抵挡
    respects_resilience: bool = True  # 是否吃目标的境界基础韧性（仅"机制性必杀类真伤"豁免）


# 正常杀伐伤害：吃增伤、承伤、减伤、护盾、承尘与韧性。
_NORMAL_DAMAGE_PROFILE = _DamageProfile()
# 灼烧 DOT：吃增伤 + 承伤；可被护盾抵挡；不吃减伤
_BURN_DOT_PROFILE = _DamageProfile(can_be_buffed=True, can_be_vulned=True, can_be_reduced=False, can_be_shielded=True)
# 蚀焰引爆：不吃增伤；吃承伤 + 减伤；不暴击；可被护盾抵挡
_SHIYAN_PROFILE = _DamageProfile(can_be_buffed=False, can_be_vulned=True, can_be_reduced=True, can_be_shielded=True)
# 春生固定追打：不吃增伤（已是固定值）；吃承伤 + 减伤 + 护盾
_CHUNSHENG_BONUS_PROFILE = _DamageProfile(can_be_buffed=False, can_be_vulned=True, can_be_reduced=True, can_be_shielded=True)


@dataclass(slots=True)
class _CombatState:
    snapshot: CombatantSnapshot
    hp: int
    statuses: list[_StatusEffect] = field(default_factory=list)
    hits_taken: int = 0
    low_hp_marks: set[int] = field(default_factory=set)
    revive_used: bool = False
    counter_used_round: int = 0
    skip_next_action: bool = False
    spirit_proc_rounds: dict[str, int] = field(default_factory=dict)
    consecutive_crits: int = 0
    first_round: bool = True
    current_round: int = 0
    # v6 战斗重做新增字段
    effective_max_hp: int = 0  # 运行期可变的最大生命（归元词条会提升），0 表示尚未初始化（fallback 至 snapshot.max_hp）
    niepan_revive_count: int = 0  # 涅槃复活累计次数（每次叠加 atk/speed buff）
    huichun_triggered_thresholds: set[int] = field(default_factory=set)  # huichun 已触发的阈值（50/25）
    huyuan_heal_stacks: dict[int, int] = field(default_factory=dict)  # huyuan 单词条治疗叠加层数计数（key 为 affix 在 affixes 元组中的 index）
    is_first_mover: bool = False  # 追风：是否先手（首回合先行动者）
    pending_overkill: int = 0  # 击杀瞬间的溢出余伤；复活检查点由涅槃补上
    dishi_last_round: int = 0  # 涤世上次触发回合（用于 1 回合冷却）
    jueming_mark_stacks: int = 0  # 绝命印记层数（被标记者更容易被绝命斩杀，不可净化）
    roller: random.Random | None = None  # 本场战斗 RNG；独立保存以避免服务实例并发串扰
    zhuifeng_first_attack_pending: bool = True
    xuekuang_lost_hp: int = 0
    xuekuang_base_max_hp: int = 0
    xuekuang_layers: int = 0
    xuekuang_loss_bank: int = 0
    action_attack_damage_dealt: int = 0  # 本次行动普攻类伤害合计（主手 + 破空/天威/裂空/戮厄/碎阙等追击），反棘反弹基数

    def get_max_hp(self) -> int:
        """获取当前最大生命（优先使用 effective_max_hp，未初始化时回落 snapshot）。"""
        return self.effective_max_hp if self.effective_max_hp > 0 else self.snapshot.max_hp


class CombatService:
    max_rounds = 35

    _RANDOM_DEBUFF_NAMES = ("蔓咒", "破步", "创伤", "灼烧")
    _RANDOM_BUFF_NAMES = ("增伤", "减伤", "身法", "杀伐")
    _DAMAGE_AFFIX_IDS = frozenset(
        {
            "ningshen", "zhoufu", "juling", "shigu", "zhuiming", "duanyue", "kuangfeng",
            "dengxiao", "zhenguan", "zhengheng", "yazhen", "liechuang", "suoling", "jifeng",
            "fenjie", "fengxing", "tianwei", "leiyin", "liekong", "tanshi", "tongming",
        }
    )
    _PENDING_STRIKE_STATUS_NAMES = frozenset({"涤世·净化", "春生·追击", "风刃", "碎阙"})
    _STATUS_STACK_CAPS = {
        "灵势": 10,
        "掠影·破步": 5,
        "创伤": 5,
        "破步": 4,
        "疾锋": 3,
        "天威": 6,
        "风行": 5,
        "风遁": 10,
        "登霄": 8,
        "追猎": 12,
        "夺锋": 5,
        "死兆": 3,
        "破封灵势": 10,
        "幻步": 3,
        "咒缚": 7,
        "蔓咒": 7,
    }

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def create_combatant(
        self,
        name: str,
        atk: int,
        defense: int,
        agility: int,
        *,
        title: str = "",
        fate_name: str = "",
        affixes: tuple[ArtifactAffixEntry, ...] | list[ArtifactAffixEntry] = (),
        spirit_power: SpiritPowerEntry | None = None,
        realm_index: int = 1,
        damage_dealt_basis_points: int = 0,
        damage_taken_basis_points: int = 0,
        damage_reduction_basis_points: int = 0,
        versus_higher_realm_damage_basis_points: int = 0,
        base_resilience: int = 0,
    ) -> CombatantSnapshot:
        return CombatantSnapshot(
            name,
            atk,
            defense,
            agility,
            defense * 10,
            title,
            fate_name,
            tuple(affixes),
            spirit_power,
            realm_index,
            damage_dealt_basis_points,
            damage_taken_basis_points,
            damage_reduction_basis_points,
            versus_higher_realm_damage_basis_points,
            base_resilience,
        )

    def run_battle(
        self,
        challenger: CombatantSnapshot,
        defender: CombatantSnapshot,
        *,
        scene_tags: tuple[str, ...] = (),
        rng: random.Random | None = None,
    ) -> BattleResult:
        roller = rng or self.rng
        scene = set(scene_tags)
        challenger_state = _CombatState(challenger, challenger.max_hp)
        challenger_state.effective_max_hp = challenger.max_hp
        challenger_state.roller = roller
        defender_state = _CombatState(defender, defender.max_hp)
        defender_state.effective_max_hp = defender.max_hp
        defender_state.roller = roller
        logs: list[ActionLog] = []

        logs.extend(self._trigger_battle_start(1, challenger_state, scene))
        logs.extend(self._trigger_battle_start(1, defender_state, scene))
        logs.extend(self._revive_checkpoint(1, challenger_state, defender_state))
        first, second = self._determine_order(challenger_state, defender_state, roller)
        first.is_first_mover = True
        for round_no in range(1, self.max_rounds + 1):
            challenger_state.current_round = round_no
            defender_state.current_round = round_no
            logs.extend(self._trigger_round_start(round_no, first, second, roller, scene))
            logs.extend(self._revive_checkpoint(round_no, challenger_state, defender_state))
            if challenger_state.hp > 0 and defender_state.hp > 0:
                logs.extend(self._trigger_round_start(round_no, second, first, roller, scene))
                logs.extend(self._revive_checkpoint(round_no, challenger_state, defender_state))
            if challenger_state.hp <= 0 or defender_state.hp <= 0:
                return self._build_result(challenger_state, defender_state, round_no, False, logs)

            for actor, target in ((first, second), (second, first)):
                if actor.hp <= 0 or target.hp <= 0:
                    continue
                before_action_logs, can_act = self._trigger_before_action(round_no, actor, target)
                logs.extend(before_action_logs)
                if can_act:
                    logs.extend(self._resolve_action(round_no, actor, target, roller, scene))
                logs.extend(self._revive_checkpoint(round_no, challenger_state, defender_state))
                if challenger_state.hp <= 0 or defender_state.hp <= 0:
                    return self._build_result(challenger_state, defender_state, round_no, False, logs)

            logs.extend(self._trigger_round_end(round_no, challenger_state, defender_state, roller))
            logs.extend(self._revive_checkpoint(round_no, challenger_state, defender_state))
            if challenger_state.hp <= 0 or defender_state.hp <= 0:
                return self._build_result(challenger_state, defender_state, round_no, False, logs)
            self._decay_statuses(challenger_state)
            self._decay_statuses(defender_state)

        return self._build_result(challenger_state, defender_state, self.max_rounds, True, logs)

    def _build_result(
        self,
        challenger: _CombatState,
        defender: _CombatState,
        rounds: int,
        reached_round_limit: bool,
        logs: list[ActionLog],
    ) -> BattleResult:
        challenger_won = challenger.hp > 0 and defender.hp <= 0
        if challenger_won:
            winner_name = challenger.snapshot.name
            loser_name = defender.snapshot.name
        else:
            winner_name = defender.snapshot.name
            loser_name = challenger.snapshot.name
        return BattleResult(
            challenger_won,
            winner_name,
            loser_name,
            rounds,
            reached_round_limit,
            logs,
            challenger.get_max_hp(),
            defender.get_max_hp(),
            challenger.hp,
            defender.hp,
        )

    def _determine_order(
        self,
        challenger: _CombatState,
        defender: _CombatState,
        roller: random.Random,
    ) -> tuple[_CombatState, _CombatState]:
        challenger_agi = self._current_agility(challenger)
        defender_agi = self._current_agility(defender)
        if challenger_agi > defender_agi:
            return challenger, defender
        if defender_agi > challenger_agi:
            return defender, challenger
        return (challenger, defender) if roller.random() < 0.5 else (defender, challenger)

    def _resolve_action(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        roller: random.Random,
        scene: set[str],
    ) -> list[ActionLog]:
        logs: list[ActionLog] = []
        actor.action_attack_damage_dealt = 0
        used_attack_bonuses = [
            status
            for status in actor.statuses
            if status.is_active()
            and status.remaining_hits is not None
            and (
                status.damage_dealt_pct
                or status.crit_bonus_pct
                or status.crit_damage_pct
                or status.guarantee_crit
                or status.dodge_bonus_pct
            )
        ]
        dodge_rate = self._dodge_rate(target, actor)
        force_hit = self._zhuifeng_force_hit(actor)
        if force_hit:
            actor.zhuifeng_first_attack_pending = False
        if not force_hit and roller.random() < dodge_rate:
            logs.append(ActionLog(round_no, actor.snapshot.name, target.snapshot.name, True, False, 0, target.hp))
            logs.extend(self._trigger_on_dodge(round_no, target, actor, scene))
            self._consume_attack_bonuses(actor, used_attack_bonuses)
            # 风遁：闪避时叠层（由神通处理）
            logs.extend(self._trigger_spirit_on_dodge(round_no, target))
            self._consume_break_spirit(round_no, actor, logs)
            return logs

        # 被命中时只消散 1 层风遁（保留叠层流派的可玩性）
        self._reduce_wind_stacks(target, 1)

        had_wound_before_attack = self._status_count(target, "创伤") > 0
        damage = self._current_atk(actor)
        crit_rate = clamp(0.20 * (self._current_agility(actor) / max(self._current_agility(target), 1)) + (self._crit_bonus_pct(actor) + self._target_crit_bonus_pct(actor, target)) / 100, 0.10, 0.90)
        if self._has_guarantee_crit(actor):
            critical = True
        else:
            critical = roller.random() < crit_rate
        if critical:
            crit_multiplier = 1.5 + 0.5 * damage / max(damage + self._current_defense(target), 1) + (self._crit_damage_bonus_pct(actor) + self._target_crit_damage_bonus_pct(actor, target)) / 100
            damage = int(damage * crit_multiplier)

        before_attack_bonus = self._before_attack_bonus_pct(
            actor,
            target,
            scene,
            had_wound_before_attack=had_wound_before_attack,
        )
        damage = int(damage * (1 + before_attack_bonus / 100))
        damage = int(damage * (1 + self._damage_dealt_pct(actor) / 100))
        damage = int(damage * (1 + self._damage_taken_pct(target) / 100))
        effective_reduction = max(0, self._damage_reduction_pct(target) - self._pierce_pct(actor, scene, target))
        damage = max(1, int(damage * max(0.05, 1 - (effective_reduction / 100))))
        damage = int(damage * (1 + self._spirit_damage_bonus_pct(actor, target, before_attack_bonus) / 100))
        damage = int(damage * (1 + actor.snapshot.damage_dealt_basis_points / 10_000))
        if target.snapshot.realm_index > actor.snapshot.realm_index:
            damage = int(damage * (1 + actor.snapshot.versus_higher_realm_damage_basis_points / 10_000))
        damage = int(damage * (1 + target.snapshot.damage_taken_basis_points / 10_000))
        damage = max(1, int(damage * max(0.05, 1 - (target.snapshot.damage_reduction_basis_points / 10_000))))

        had_damage_reduction = self._has_damage_reduction_status(target)
        damage = self._apply_chenchen(target, damage)
        nested_hit_logs: list[ActionLog] = []
        attack_log = ActionLog(
            round_no,
            actor.snapshot.name,
            target.snapshot.name,
            False,
            critical,
            0,
            target.hp,
        )
        logs.append(attack_log)
        actual_damage = self._apply_damage(
            target,
            damage,
            actor=actor,
            round_no=round_no,
            logs=nested_hit_logs,
            scene=scene,
            can_be_shielded=True,
            cause=attack_log,
            can_fengdun_dodge=False,
        )
        target.hits_taken += 1
        actor.action_attack_damage_dealt += actual_damage
        self._consume_hit_reduction_statuses(target)
        logs.extend(nested_hit_logs)
        logs.extend(self._trigger_on_hit(round_no, actor, target, actual_damage, roller, scene))
        if critical:
            actor.consecutive_crits += 1
            logs.extend(self._trigger_on_crit(round_no, actor, target, actual_damage, roller, scene))
        else:
            actor.consecutive_crits = 0
        logs.extend(self._trigger_on_be_hit(round_no, target, scene))
        logs.extend(
            self._trigger_spirit_on_hit(
                round_no,
                actor,
                target,
                actual_damage,
                roller,
                source=_DamageSource.ATTACK,
                scene=scene,
            )
        )
        logs.extend(self._trigger_lingyong_strike(round_no, actor, target, scene))
        logs.extend(
            self._trigger_spirit_on_be_hit(
                round_no,
                actor,
                target,
                actual_damage,
                roller,
                source=_DamageSource.ATTACK,
                had_damage_reduction=had_damage_reduction,
            )
        )

        power = actor.snapshot.spirit_power
        debuff_layers = self._debuff_count(target)
        if power is not None and power.power_id == "luejie" and actor.hp > 0 and target.hp > 0 and debuff_layers >= 5:
            followup_raw = max(1, self._current_atk(actor) * _roll(power.rolls, "per_debuff_pct", 0) // 100)
            nested: list[ActionLog] = []
            cause = self._effect_log(
                round_no,
                target,
                f"{actor.snapshot.name} 的戮厄锁定 {debuff_layers} 层负面。",
                actor_name=actor.snapshot.name,
            )
            nested.append(cause)
            followup_actual = self._apply_typed_damage(
                target,
                followup_raw,
                _NORMAL_DAMAGE_PROFILE,
                actor=actor,
                round_no=round_no,
                logs=nested,
                scene=scene,
                cause=cause,
            )
            actor.action_attack_damage_dealt += followup_actual
            if followup_actual > 0 or any(log is not cause for log in nested):
                logs.extend(nested)

        # 反棘：行动收尾时按本次普攻类伤害合计反弹一次（元素/特殊/反制伤害不参与）
        target_power = target.snapshot.spirit_power
        if (
            target_power is not None
            and target_power.power_id == "fanji"
            and actor.hp > 0
            and actor.action_attack_damage_dealt > 0
        ):
            reflect_damage = max(1, actor.action_attack_damage_dealt * target_power.rolls["reflect_pct"] // 100)
            reflect_damage = min(actor.hp, reflect_damage)
            reflect_nested: list[ActionLog] = []
            reflect_cause = self._effect_log(
                round_no,
                actor,
                f"{target.snapshot.name} 的反棘回卷而出。",
                actor_name=target.snapshot.name,
            )
            reflect_nested.append(reflect_cause)
            self._apply_damage(
                actor,
                reflect_damage,
                actor=target,
                round_no=round_no,
                logs=reflect_nested,
                cause=reflect_cause,
            )
            logs.extend(reflect_nested)

        # 春生·追击 + 涤世·净化：命中后按 bonus_damage 走 _CHUNSHENG_BONUS_PROFILE 施加固定追打伤害（不吃增伤、吃承伤+减伤+护盾）
        bonus_followups = [s for s in actor.statuses if s.name in ("春生·追击", "涤世·净化") and s.is_active() and s.bonus_damage > 0]
        if bonus_followups and actor.hp > 0 and target.hp > 0:
            for s in bonus_followups:
                nested: list[ActionLog] = []
                label = "春生回返一击" if s.name == "春生·追击" else "涤世净化之力倾泻"
                cause = self._effect_log(round_no, target, f"{actor.snapshot.name} {label}。", actor_name=actor.snapshot.name)
                nested.append(cause)
                bonus_actual = self._apply_typed_damage(
                    target, s.bonus_damage, _CHUNSHENG_BONUS_PROFILE, actor=actor, round_no=round_no, logs=nested, cause=cause
                )
                if bonus_actual > 0 or any(log is not cause for log in nested):
                    logs.extend(nested)
                s.remaining_hits = 0
            actor.statuses = self._active_statuses(actor)
        # 风行：攻击后消耗全部层数并回复生命（伤害加成已在 _before_attack_bonus_pct 中计算）
        logs.extend(self._consume_fengxing_stacks(round_no, actor, scene))
        self._consume_break_spirit(round_no, actor, logs)
        self._consume_attack_bonuses(actor, used_attack_bonuses)
        return logs

    def _trigger_before_action(self, round_no: int, state: _CombatState, opponent: _CombatState | None = None) -> tuple[list[ActionLog], bool]:
        logs: list[ActionLog] = []
        seal = self._remove_one_status_by_name(state, "封禁行动")
        if seal is not None:
            logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 经脉被封，本次行动被截断。", actor_name=seal.source.snapshot.name if seal.source else None))
            return (logs, False)
        if not state.skip_next_action:
            return (logs, True)
        state.skip_next_action = False
        logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 灵机一滞，此回合行动被封断。"))
        return (logs, False)

    def _trigger_battle_start(self, round_no: int, state: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in state.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "lueying":
                    self._add_status(state, _StatusEffect("掠影", agility_pct=_roll(entry.rolls, "agi_pct", 0)))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 展开掠影，整场身法提高 {entry.rolls['agi_pct']}%。"))
                case "guben":
                    shield_amount = max(1, state.get_max_hp() * _roll(entry.rolls, "shield_pct", 0) // 100)
                    self._add_status(state, _StatusEffect("固本", shield=shield_amount, cleanseable=False))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 凝起固本护盾，抵消 {shield_amount} 伤害（免疫净化）。"))
                case "xianji":
                    agi_pct = _roll(entry.rolls, "agi_pct", 0)
                    dodge_bonus = _roll(entry.rolls, "dodge_bonus_pct", 0)
                    self._add_status(state, _StatusEffect("先机", agility_pct=agi_pct))
                    if dodge_bonus > 0:
                        self._add_status(state, _StatusEffect("先机·闪", dodge_bonus_pct=dodge_bonus, duration=2))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 先机独占，起手快人一步。"))
                case "guiyuan":
                    # 归元：开局永久提高最大生命（属性预加），同步治疗等量生命
                    bonus = max(1, state.snapshot.max_hp * _roll(entry.rolls, "max_hp_pct", 0) // 100)
                    self._modify_max_hp(state, bonus, also_heal=True)
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 归元充盈，最大生命提高 {bonus}（同步补满至新上限）。"))
                case "huyuan":
                    # 护元：开局叠加 start_stacks 层生息（不计入 huyuan 自身的治疗叠加上限）
                    start_stacks = _roll(entry.rolls, "start_stacks", 1)
                    for _ in range(start_stacks):
                        self._add_status(state, _StatusEffect("生息"))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 护元运转，开局叠加 {start_stacks} 层生息。"))
        # 器灵 battle_start 钩子：在词条 battle_start 之后触发，便于联动聚灵等词条
        logs.extend(self._trigger_spirit_battle_start(round_no, state))
        return logs

    def _trigger_spirit_battle_start(self, round_no: int, state: _CombatState) -> list[ActionLog]:
        logs: list[ActionLog] = []
        power = state.snapshot.spirit_power
        if power is None:
            return logs
        match power.power_id:
            case "xuanjia":
                if state.spirit_proc_rounds.get("xuanjia_battle_start"):
                    return logs
                state.spirit_proc_rounds["xuanjia_battle_start"] = 1
                bonus = max(0, state.snapshot.max_hp * _roll(power.rolls, "def_pct", 0) // 100)
                if bonus > 0:
                    self._modify_max_hp(state, bonus, also_heal=True)
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 玄甲护体，最大生命提高 {bonus}。"))
            case "fengdun":
                if state.spirit_proc_rounds.get("fengdun_battle_start"):
                    return logs
                state.spirit_proc_rounds["fengdun_battle_start"] = 1
                self._add_status(state, _StatusEffect("风遁·起", dodge_bonus_pct=10, cleanseable=False))
                logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 风遁起势，整场闪避率提高 10%。"))
            case "lingyong":
                start_stacks = max(0, power.rolls.get("start_stacks", 0))
                if start_stacks <= 0:
                    return logs
                # 灵势 atk_pct 优先取自聚灵词条 rolls；若无聚灵词条，则灵势仅作为 lingyong 增伤计数器（atk_pct=0）
                juling_atk_pct = 0
                for entry in state.snapshot.affixes:
                    if entry.affix_id == "juling":
                        juling_atk_pct = _roll(entry.rolls, "atk_pct", 0)
                        break
                for _ in range(start_stacks):
                    if self._status_count(state, "灵势") >= self._status_stack_cap("灵势", state):
                        break
                    self._add_status(state, _StatusEffect("灵势", atk_pct=juling_atk_pct))
                logs.append(
                    self._effect_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 的灵涌器灵牵起灵势，战斗开始即获得 {start_stacks} 层灵势。",
                    )
                )
        return logs

    def _trigger_round_start(self, round_no: int, state: _CombatState, opponent: _CombatState, roller: random.Random, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        # 养元 / 续命 回合开始钩子（多件可叠加，依词条顺序执行）
        for entry in state.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            if entry.affix_id == "yangyuan" and state.hp > 0:
                heal_pct = _roll(entry.rolls, "heal_pct", 0)
                if heal_pct > 0:
                    healed = self._heal(state, heal_pct, logs=logs)
                    if healed > 0:
                        logs.append(self._hp_change_log(round_no, state, f"{state.snapshot.name} 养元运转，回复 {format_big_number(healed)} 点生命，余血 {format_big_number(state.hp)}。"))
            elif entry.affix_id == "xuming" and state.hp > 0:
                cost = _roll(entry.rolls, "cost_stacks", 1)
                consumed = self._consume_shengxi(state, cost)
                if consumed > 0:
                    heal_per_stack = _roll(entry.rolls, "heal_per_stack", 0)
                    heal_pct = consumed * heal_per_stack
                    if heal_pct > 0:
                        healed = self._heal(state, heal_pct, logs=logs)
                        logs.append(self._hp_change_log(round_no, state, f"{state.snapshot.name} 续命发动，消耗 {consumed} 层生息回复 {format_big_number(healed)} 点生命，余血 {format_big_number(state.hp)}。"))
        for entry in state.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "juling":
                    if self._status_count(state, "灵势") >= self._status_stack_cap("灵势", state):
                        continue
                    self._add_status(state, _StatusEffect("灵势", atk_pct=_roll(entry.rolls, "atk_pct", 0)))
                    current_layers = self._status_count(state, "灵势")
                    if current_layers <= LINGYONG_NORMAL_STACK_CAP:
                        self._add_status(state, _StatusEffect("聚灵通明", damage_dealt_pct=_roll(entry.rolls, "late_damage_pct", 8)))
                    logs.append(
                        self._effect_log(
                            round_no,
                            state,
                            f"{state.snapshot.name} 的聚灵凝成第 {current_layers} 层灵势。",
                        )
                    )
                case "jinghua":
                    stacks = _roll(entry.rolls, "stacks", 1)
                    if not self._has_debuff(state):
                        continue
                    had_burn = self._has_burn(state)
                    burn_source = self._burn_source(state)
                    removed_count = 0
                    for _ in range(stacks):
                        if self._remove_one_debuff(state) is not None:
                            removed_count += 1
                    if removed_count > 0:
                        followups = self._trigger_cleanse_followups(round_no, state, removed_count, opponent)
                        logs.append(
                            self._effect_log(
                                round_no,
                                state,
                                f"{state.snapshot.name} 的净华洗去 {removed_count} 层杂念。",
                            )
                        )
                        logs.extend(followups)
                        logs.extend(self._maybe_trigger_burn_exhausted(burn_source or opponent, state, had_burn=had_burn, round_no=round_no, roller=roller))
                case "qingxin":
                    stacks = _roll(entry.rolls, "stacks", 1)
                    if not self._has_debuff(state):
                        continue
                    had_burn = self._has_burn(state)
                    burn_source = self._burn_source(state)
                    removed_count = 0
                    for _ in range(stacks):
                        if self._remove_one_debuff(state) is not None:
                            removed_count += 1
                    if removed_count > 0:
                        followups = self._trigger_cleanse_followups(round_no, state, removed_count, opponent)
                        heal_pct = _roll(entry.rolls, "heal_pct", 0)
                        healed = self._heal(state, heal_pct, logs=logs)
                        logs.append(
                            self._hp_change_log(
                                round_no,
                                state,
                                f"{state.snapshot.name} 清心净念，洗去 {removed_count} 层杂念，回复 {format_big_number(healed)} 点生命，余血 {format_big_number(state.hp)}。",
                            )
                        )
                        logs.extend(followups)
                        logs.extend(self._maybe_trigger_burn_exhausted(burn_source or opponent, state, had_burn=had_burn, round_no=round_no, roller=roller))
                case "huisheng":
                    stacks = _roll(entry.rolls, "stacks", 1)
                    ally_pct = _roll(entry.rolls, "ally_pct", 35)
                    target_for_debuff = opponent if roller.random() < ally_pct / 100 else state
                    debuff_name = roller.choice(self._RANDOM_DEBUFF_NAMES)
                    if debuff_name == "灼烧":
                        self._apply_burn_to_target(
                            target_for_debuff,
                            state,
                            stacks=stacks,
                            per_stack_pct=self._zhuohun_burn_pct(state),
                            round_no=round_no,
                            logs=logs,
                        )
                    else:
                        for _ in range(stacks):
                            self._add_status(target_for_debuff, self._create_debuff_by_name(debuff_name, state))
                    target_label = "敌方" if target_for_debuff is opponent else "自身"
                    logs.append(
                        self._effect_log(
                            round_no,
                            state,
                            f"{state.snapshot.name} 的秽生发作，为{target_label}附加 {stacks} 层{debuff_name}。",
                        )
                    )
                case "lingyi":
                    stacks = _roll(entry.rolls, "stacks", 1)
                    ally_pct = _roll(entry.rolls, "ally_pct", 35)
                    target_for_buff = state if roller.random() < ally_pct / 100 else opponent
                    buff_name = roller.choice(self._RANDOM_BUFF_NAMES)
                    for _ in range(stacks):
                        self._add_status(target_for_buff, self._create_buff_by_name(buff_name))
                    target_label = "自身" if target_for_buff is state else "敌方"
                    logs.append(
                        self._effect_log(
                            round_no,
                            state,
                            f"{state.snapshot.name} 的灵溢涌动，为{target_label}附加 {stacks} 层{buff_name}。",
                        )
                    )
                case "zhoufu":
                    reduce_down_pct = _roll(entry.rolls, "reduce_down_pct", 3)
                    max_stacks_zhoufu = _roll(entry.rolls, "max_stacks", 7)
                    if self._status_count(opponent, "咒缚") < max_stacks_zhoufu:
                        self._add_status(opponent, _StatusEffect("咒缚", damage_taken_pct=reduce_down_pct, is_debuff=True, source=state))
                    self._add_curse_seal(opponent, state, 1)
                    total_zf = self._status_count(opponent, "咒缚")
                    logs.append(
                        self._effect_log(
                            round_no,
                            opponent,
                            f"{state.snapshot.name} 的咒缚缠上 {opponent.snapshot.name}（咒缚 {total_zf}/{max_stacks_zhoufu}，咒印 +1）。",
                            actor_name=state.snapshot.name,
                        )
                    )
        # 先机首回合结束后清除标记
        if round_no > 1:
            state.first_round = False
        # 器灵神通 round_start 钩子（窃道等）
        logs.extend(self._trigger_spirit_round_start(round_no, state, opponent, roller, scene))
        return logs

    def _trigger_on_hit(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        actual_damage: int,
        roller: random.Random,
        scene: set[str],
    ) -> list[ActionLog]:
        logs: list[ActionLog] = []
        had_burn = self._has_burn(target)
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            proc_pct = entry.rolls.get("proc_pct")
            match entry.affix_id:
                case "ningshen":
                    if self._status_count(actor, "灵势") >= self._status_stack_cap("灵势", actor):
                        continue
                    self._add_status(actor, _StatusEffect("灵势", atk_pct=_roll(entry.rolls, "atk_pct", 0)))
                    logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 凝神聚意，获得 1 层灵势。"))
                case "lueying":
                    drain_pct = _roll(entry.rolls, "drain_pct", 0)
                    if drain_pct <= 0:
                        continue
                    # 最多吸取 5 层（drain_pct * 5 总百分比上限）
                    if self._status_count(target, "掠影·破步") >= 5:
                        continue
                    self._add_status(target, _StatusEffect("掠影·破步", agility_pct=-drain_pct, is_debuff=True, source=actor))
                    self._add_status(actor, _StatusEffect("掠影·疾", agility_pct=drain_pct))
                    logs.append(self._effect_log(round_no, target, f"{actor.snapshot.name} 的掠影扰乱步法，{target.snapshot.name} 身法被汲取。", actor_name=actor.snapshot.name))
                case "shigu":
                    if proc_pct is None or roller.random() > proc_pct / 100 or self._status_count(target, "创伤") >= 5:
                        continue
                    self._add_status(
                        target,
                        _StatusEffect(
                            "创伤",
                            damage_taken_pct=_roll(entry.rolls, "vuln_pct", 0),
                            heal_received_pct=-_roll(entry.rolls, "heal_down_pct", 5),
                            is_debuff=True,
                            source=actor,
                        ),
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的蚀骨刻下创伤，{target.snapshot.name} 受疗与抗性下降。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "zhuohun":
                    burn_stacks = _roll(entry.rolls, "burn_stacks", 2)
                    burn_atk_pct = _roll(entry.rolls, "burn_atk_pct", 25)
                    self._apply_burn_to_target(
                        target,
                        actor,
                        stacks=burn_stacks,
                        per_stack_pct=burn_atk_pct,
                        round_no=round_no,
                        logs=logs,
                    )
                case "zhenpo":
                    if proc_pct is None or roller.random() > proc_pct / 100:
                        continue
                    if self._status_count(target, "破步") >= 4:
                        # 满层后震慑：消耗全部破步，附加 1 层封禁行动
                        target.statuses = [s for s in target.statuses if s.name != "破步"]
                        self._add_action_seal(target, actor, 1)
                        logs.append(
                            self._effect_log(
                                round_no,
                                target,
                                f"{actor.snapshot.name} 的震魄引爆破步，震慑之力令 {target.snapshot.name} 下次行动被封断。",
                                actor_name=actor.snapshot.name,
                            )
                        )
                    else:
                        self._add_status(
                            target,
                            _StatusEffect("破步", agility_pct=-_roll(entry.rolls, "agi_down_pct", 0), is_debuff=True, source=actor),
                        )
                        logs.append(
                            self._effect_log(
                                round_no,
                                target,
                                f"{actor.snapshot.name} 的震魄压住身法，{target.snapshot.name} 获得 1 层破步。",
                                actor_name=actor.snapshot.name,
                            )
                        )
                case "manzhou":
                    atk_down_pct = _roll(entry.rolls, "atk_down_pct", 4)
                    max_stacks_mz = _roll(entry.rolls, "max_stacks", 7)
                    if self._status_count(target, "蔓咒") < max_stacks_mz:
                        self._add_status(
                            target,
                            _StatusEffect("蔓咒", atk_pct=-atk_down_pct, is_debuff=True, source=actor),
                        )
                    self._add_curse_seal(target, actor, 1)
                    mz_layers = self._status_count(target, "蔓咒")
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的蔓咒缠身，{target.snapshot.name} 杀伐被削（蔓咒 {mz_layers}/{max_stacks_mz}，咒印 +1）。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "liechuang":
                    if target.hp * 100 > target.get_max_hp() * 60 and self._status_count(target, "创伤") <= 0:
                        continue
                    if self._status_count(target, "创伤") < 5:
                        self._add_status(
                            target,
                            _StatusEffect("创伤", heal_received_pct=-_roll(entry.rolls, "heal_down_pct", 5), is_debuff=True, source=actor),
                        )
                    logs.append(self._effect_log(round_no, target, f"{actor.snapshot.name} 撕开裂创，{target.snapshot.name} 的续航被压制。", actor_name=actor.snapshot.name))
                case "suoling":
                    if self._positive_status_count(target) <= 0:
                        continue
                    stacks = _roll(entry.rolls, "stacks", 1)
                    buff_pct = _roll(entry.rolls, "buff_pct", 5)
                    removed_count = 0
                    for _ in range(stacks):
                        if self._remove_one_positive_status(target) is not None:
                            removed_count += 1
                    if removed_count > 0:
                        # 绝命印记：效果被清除时，绝命持有者的对手获得印记
                        followups = self._trigger_cleanse_followups(round_no, target, removed_count, actor)
                        for _ in range(removed_count):
                            self._add_status(actor, _StatusEffect("锁灵", damage_dealt_pct=buff_pct, duration=2))
                        followups.extend(self._trigger_on_effect_lost_to_enemy(round_no, actor, target, removed_count))
                        logs.append(
                            self._effect_log(
                                round_no,
                                target,
                                f"{actor.snapshot.name} 的锁灵打散 {removed_count} 层灵机，自身增伤提升 {removed_count * buff_pct}%。",
                                actor_name=actor.snapshot.name,
                            )
                        )
                        logs.extend(followups)
                case "jifeng":
                    if self._status_count(actor, "疾锋") >= 3:
                        continue
                    self._add_status(actor, _StatusEffect("疾锋", agility_pct=_roll(entry.rolls, "agi_pct", 0), damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0)))
                    layers = self._status_count(actor, "疾锋")
                    logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 疾锋加身（{layers}/3），速攻势头更盛。"))
                case "yujin":
                    # 余烬：灼烧层数归零时重燃，on_hit 不再处理
                    pass
                case "fenxin":
                    if not had_burn:
                        continue
                    max_stacks = _roll(entry.rolls, "max_stacks", 6)
                    if self._status_count(target, "焚心") >= max_stacks:
                        continue
                    self._add_status(
                        target,
                        _StatusEffect(
                            "焚心",
                            atk_pct=-_roll(entry.rolls, "atk_down_pct", 0),
                            agility_pct=-_roll(entry.rolls, "agi_down_pct", 0),
                            is_debuff=True,
                            source=actor,
                        ),
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的焚心烙下印记，{target.snapshot.name} 杀伐与身法俱损。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "fenjie":
                    if not had_burn:
                        continue
                    max_stacks = _roll(entry.rolls, "max_stacks", 6)
                    if self._status_count(target, "焚劫") >= max_stacks:
                        continue
                    self._add_status(
                        target,
                        _StatusEffect(
                            "焚劫",
                            damage_taken_pct=_roll(entry.rolls, "vuln_pct", 0),
                            heal_received_pct=-_roll(entry.rolls, "heal_down_pct", 0),
                            is_debuff=True,
                            source=actor,
                        ),
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的焚劫缠身，{target.snapshot.name} 承伤增加且续航下降。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "jinhuo":
                    # 烬火：攻击灼烧目标时，按概率消耗目标 1 层正面状态，将其转化为 N 层灼烧
                    if not self._has_burn(target):
                        continue
                    if self._positive_status_count(target) <= 0:
                        continue
                    proc_pct = _roll(entry.rolls, "proc_pct", 0)
                    if roller.random() > proc_pct / 100:
                        continue
                    removed = self._remove_one_positive_status(target)
                    if removed is None:
                        continue
                    # 反噬 + 绝命印记：敌方正面效果被烬火消耗后触发
                    followups = self._trigger_cleanse_followups(round_no, target, 1, actor)
                    followups.extend(self._trigger_on_effect_lost_to_enemy(round_no, actor, target, 1))
                    gain = _roll(entry.rolls, "burn_stacks_gain", 1)
                    burn_pct = self._zhuohun_burn_pct(actor)
                    self._apply_burn_to_target(
                        target,
                        actor,
                        stacks=gain,
                        per_stack_pct=burn_pct,
                        round_no=round_no,
                        logs=logs,
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 烬火吞噬 {target.snapshot.name} 的「{removed.name}」，化为 {gain} 层灼烧。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                    logs.extend(followups)
                case "tianwei":
                    if self._status_count(actor, "天威") < 6:
                        self._add_status(actor, _StatusEffect("天威", crit_bonus_pct=_roll(entry.rolls, "crit_pct", 0), crit_damage_pct=_roll(entry.rolls, "crit_damage_pct", 0)))
                        logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 天威蓄势，威压渐成。"))
                case "shiyin":
                    if roller.random() > _roll(entry.rolls, "proc_pct", 0) / 100:
                        continue
                    curse_count = self._curse_seal_count(target)
                    if curse_count > 0:
                        drain_per_mark = _roll(entry.rolls, "drain_per_mark_pct", 2)
                        drain_pct = curse_count * drain_per_mark
                        healed = self._heal(actor, drain_pct, logs=logs)
                        if healed > 0:
                            logs.append(self._hp_change_log(round_no, actor, f"{actor.snapshot.name} 噬取咒印之力，吸取 {format_big_number(healed)} 点生命（{curse_count} 层咒印），余血 {format_big_number(actor.hp)}。"))
                        else:
                            logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 噬取咒印之力（{curse_count} 层咒印）。"))
                    else:
                        self._add_curse_seal(target, actor, 1)
                        logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 的噬印刻下第一缕咒印，{target.snapshot.name} 获得 1 层咒印。"))
                case "leiyin":
                    if target.hp <= 0:
                        continue
                    added = self._add_target_leihen(target, actor, 1)
                    if added > 0:
                        layers = self._target_leihen_count(target)
                        logs.append(
                            self._effect_log(
                                round_no,
                                target,
                                f"{actor.snapshot.name} 的雷引烙下 1 层雷殛（{layers}）。",
                                actor_name=actor.snapshot.name,
                            )
                        )
        return logs

    def _trigger_on_crit(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        actual_damage: int,
        roller: random.Random,
        scene: set[str],
    ) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "kuangfeng":
                    self._add_status(
                        actor,
                        _StatusEffect(
                            "狂锋",
                            damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0),
                            remaining_hits=1,
                            active_from_round=round_no + 1,
                        ),
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            actor,
                            f"{actor.snapshot.name} 借暴击激起狂锋，下一次出手伤害提高 {entry.rolls['damage_pct']}%。",
                        )
                    )
                case "huanbu":
                    # 暴击后消散 1 层幻步
                    for s in actor.statuses:
                        if s.is_active() and s.name == "幻步":
                            self._consume_status_stack(actor, s)
                            logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 暴击之势震荡步法，幻步消散 1 层。"))
                            break
                case "leiyin":
                    if target.hp <= 0:
                        continue
                    added = self._add_target_leihen(target, actor, 2)
                    if added > 0:
                        layers = self._target_leihen_count(target)
                        logs.append(
                            self._effect_log(
                                round_no,
                                target,
                                f"{actor.snapshot.name} 暴击引雷，追加 {added} 层雷殛（{layers}）。",
                                actor_name=actor.snapshot.name,
                            )
                        )
                case "tianwei":
                    if self._status_count(actor, "天威") < 6:
                        self._add_status(
                            actor,
                            _StatusEffect(
                                "天威",
                                crit_bonus_pct=_roll(entry.rolls, "crit_pct", 0),
                                crit_damage_pct=_roll(entry.rolls, "crit_damage_pct", 0),
                            ),
                        )
                        logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 天威加身，暴击率与杀势同涨。"))
                    if self._status_count(actor, "天威") >= 6 and target.hp > 0:
                        actor.statuses = [s for s in actor.statuses if s.name != "天威"]
                        burst_damage = max(1, self._current_atk(actor) * _roll(entry.rolls, "burst_damage_pct", 180) // 100)
                        nested: list[ActionLog] = []
                        wounds = self._add_wound(target, actor, _roll(entry.rolls, "wound_stacks", 2))
                        cause = self._effect_log(round_no, target, f"{actor.snapshot.name} 天威压顶，并刻下 {wounds} 层创伤。", actor_name=actor.snapshot.name)
                        nested.append(cause)
                        burst_actual = self._apply_typed_damage(
                            target, burst_damage, _NORMAL_DAMAGE_PROFILE, actor=actor, round_no=round_no, logs=nested, cause=cause
                        )
                        actor.action_attack_damage_dealt += burst_actual
                        logs.extend(nested)
                case "pokong":
                    if target.hp <= 0:
                        continue
                    damage_ratio = _roll(entry.rolls, "damage_ratio_pct", 0)
                    if self._total_shield(target) > 0 or self._has_damage_reduction_status(target) or self._positive_status_count(target) > 0:
                        damage_ratio += _roll(entry.rolls, "guard_bonus_pct", 0)
                    extra_damage = max(1, self._current_atk(actor) * damage_ratio // 100)
                    nested: list[ActionLog] = []
                    cause = self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 破空追击",
                        actor_name=actor.snapshot.name,
                    )
                    nested.append(cause)
                    extra_actual = self._apply_typed_damage(
                        target, extra_damage, _NORMAL_DAMAGE_PROFILE, actor=actor, round_no=round_no, logs=nested, cause=cause
                    )
                    actor.action_attack_damage_dealt += extra_actual
                    stripped = 0
                    if _roll(entry.rolls, "guard_bonus_pct", 0) > 0:
                        removed = self._remove_one_positive_status(target)
                        stripped = 1 if removed is not None else 0
                    followups: list[ActionLog] = []
                    if stripped > 0:
                        followups.extend(self._trigger_cleanse_followups(round_no, target, stripped, actor))
                        followups.extend(self._trigger_on_effect_lost_to_enemy(round_no, actor, target, stripped))
                    if stripped > 0 and cause.text:
                        reason = f"{self._strip_hp_suffix(cause.text)[:-1]}，打散 {stripped} 层正面。"
                        cause.text = self._append_hp_suffix(
                            reason, cause.damage, cause.target_hp_after, cause.shield_after
                        )
                    if extra_actual > 0 or stripped > 0 or any(log is not cause for log in nested):
                        logs.extend(nested)
                    logs.extend(followups)
                case "liekong":
                    if target.hp <= 0 or self._target_leihen_count(target) <= 0:
                        continue
                    extra_pct = _roll(entry.rolls, "extra_damage_pct", 0)
                    nested: list[ActionLog] = []
                    cause = self._effect_log(round_no, target, f"{actor.snapshot.name} 裂空撕开雷殛。", actor_name=actor.snapshot.name)
                    nested.append(cause)
                    extra_actual = self._apply_typed_damage(
                        target,
                        max(1, self._current_atk(actor) * extra_pct // 100),
                        _NORMAL_DAMAGE_PROFILE,
                        actor=actor,
                        round_no=round_no,
                        logs=nested,
                        cause=cause,
                    )
                    actor.action_attack_damage_dealt += extra_actual
                    if extra_actual > 0 or any(log is not cause for log in nested):
                        logs.extend(nested)
        # 器灵普通攻击暴击钩子（追风等）
        logs.extend(self._trigger_spirit_on_crit(round_no, actor, target, actual_damage, roller))
        return logs

    def _trigger_on_be_hit(self, round_no: int, target: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in target.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "yazhen":
                    if target.hits_taken > 3:
                        continue
                    self._add_status(target, _StatusEffect("压阵", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0), remaining_hits=1))
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{target.snapshot.name} 的压阵被激发，下一次出手伤害提高 {_roll(entry.rolls, 'damage_pct', 0)}%。",
                        )
                    )
                case "cangbi":
                    if not self._spirit_ready(target, "cangbi", round_no):
                        continue
                    self._mark_spirit_triggered(target, "cangbi", round_no)
                    self._add_status(target, _StatusEffect("守势", damage_reduction_pct=_roll(entry.rolls, "reduce_pct", 0), remaining_hits=1))
                    logs.append(self._effect_log(round_no, target, f"{target.snapshot.name} 藏壁成势，获得 1 层守势。"))
                case "jifeng":
                    # 受击时失去 1 层疾锋
                    for s in target.statuses:
                        if s.is_active() and s.name == "疾锋":
                            self._consume_status_stack(target, s)
                            logs.append(self._effect_log(round_no, target, f"{target.snapshot.name} 受击一震，疾锋减弱。"))
                            break
        return logs

    def _trigger_on_dodge(self, round_no: int, dodger: _CombatState, attacker: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in dodger.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "fengxing":
                    if self._status_count(dodger, "风行") >= 5:
                        continue
                    self._add_status(dodger, _StatusEffect("风行"))
                    layers = self._status_count(dodger, "风行")
                    logs.append(self._effect_log(round_no, dodger, f"{dodger.snapshot.name} 身法如风，获得 1 层风行（{layers}/5）。"))
                case "huanbu":
                    current_stacks = self._status_count(dodger, "幻步")
                    dodge_pct = _roll(entry.rolls, "dodge_pct", 0)
                    counter_pct = _roll(entry.rolls, "counter_pct", 0)
                    if current_stacks >= 3:
                        # 满 3 层闪避时触发反击，不叠层
                        counter_damage = max(1, self._current_atk(dodger) * counter_pct // 100)
                        nested: list[ActionLog] = []
                        cause = self._effect_log(
                            round_no,
                            attacker,
                            f"{dodger.snapshot.name} 幻步虚影一闪，反击 {attacker.snapshot.name}。",
                            actor_name=dodger.snapshot.name,
                        )
                        nested.append(cause)
                        self._apply_typed_damage(
                            attacker,
                            counter_damage,
                            _NORMAL_DAMAGE_PROFILE,
                            actor=dodger,
                            round_no=round_no,
                            logs=nested,
                            cause=cause,
                        )
                        self._consume_break_spirit(round_no, dodger, nested)
                        logs.extend(nested)
                    else:
                        self._add_status(dodger, _StatusEffect("幻步", dodge_bonus_pct=dodge_pct))
                        layers = current_stacks + 1
                        logs.append(self._effect_log(round_no, dodger, f"{dodger.snapshot.name} 幻步叠至第 {layers} 层，灵动闪避。"))
        return logs

    def _trigger_spirit_on_dodge(self, round_no: int, dodger: _CombatState) -> list[ActionLog]:
        power = dodger.snapshot.spirit_power
        if power is None or power.power_id != "fengdun":
            return []
        if self._status_count(dodger, "风遁") >= 10:
            return []
        self._add_status(dodger, _StatusEffect("风遁", damage_dealt_pct=power.rolls["per_wind_pct"], agility_pct=power.rolls["agi_boost_pct"]))
        layers = self._status_count(dodger, "风遁")
        logs = [self._effect_log(round_no, dodger, f"{dodger.snapshot.name} 风遁叠至第 {layers} 层，攻势与身法同涨。")]
        # 满 5 层授予「风刃」：下次攻击必定暴击 +50% 伤害
        if layers >= 5 and self._status_count(dodger, "风刃") == 0:
            self._add_status(
                dodger,
                _StatusEffect("风刃", guarantee_crit=True, damage_dealt_pct=50, remaining_hits=1),
            )
            logs.append(self._effect_log(round_no, dodger, f"{dodger.snapshot.name} 风遁满盈，凝出一缕风刃，下击必中要害。"))
        return logs

    def _trigger_spirit_on_crit(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        actual_damage: int,
        roller: random.Random,
    ) -> list[ActionLog]:
        power = actor.snapshot.spirit_power
        if power is None:
            return []
        logs: list[ActionLog] = []
        if power.power_id == "zhuifeng" and actor.is_first_mover:
            if self._status_count(actor, "追猎") < 12:
                agility_pct = _roll(power.rolls, "r1_agility_pct", 0)
                self._add_status(actor, _StatusEffect("追猎", agility_pct=agility_pct, cleanseable=False))
                layers = self._status_count(actor, "追猎")
                logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 暴击得势，追猎叠至 {layers}/12 层。"))
        return logs

    def _clear_wind_stacks(self, state: _CombatState) -> None:
        state.statuses = [s for s in state.statuses if s.name != "风遁"]

    def _reduce_wind_stacks(self, state: _CombatState, count: int) -> None:
        """命中时削减风遁层数（保留剩余层数延续流派）。"""
        if count <= 0:
            return
        removed = 0
        new_statuses: list[_StatusEffect] = []
        for status in state.statuses:
            if status.name != "风遁" or removed >= count:
                new_statuses.append(status)
                continue
            take = min(status.stacks, count - removed)
            status.stacks -= take
            removed += take
            if status.stacks > 0:
                new_statuses.append(status)
        state.statuses = new_statuses

    def _target_leihen_count(self, state: _CombatState) -> int:
        """目标身上「雷殛」层数（按目标视角统计）。"""
        return sum(s.stacks for s in self._active_statuses(state) if s.name == "雷殛")

    def _add_target_leihen(self, target: _CombatState, actor: _CombatState, stacks: int = 1) -> int:
        if stacks <= 0:
            return 0
        self._add_status(target, _StatusEffect("雷殛", stacks=stacks, is_debuff=True, source=actor))
        return stacks

    def _consume_target_leihen(self, state: _CombatState, amount: int) -> int:
        remaining = amount
        for status in list(self._active_statuses(state)):
            if remaining <= 0:
                break
            if status.name != "雷殛":
                continue
            take = min(status.stacks, remaining)
            status.stacks -= take
            remaining -= take
            if status.stacks <= 0:
                state.statuses.remove(status)
        return amount - remaining

    def _trigger_leifa(self, round_no: int, actor: _CombatState, target: _CombatState, roller: random.Random) -> list[ActionLog]:
        power = actor.snapshot.spirit_power
        if power is None or power.power_id != "leifa":
            return []
        cost = max(1, _roll(power.rolls, "cost_stacks", 3))
        if self._target_leihen_count(target) < cost:
            return []
        rods = [combatant for combatant in (actor, target) if self._target_leihen_count(combatant) > 0]
        consumed = self._consume_target_leihen(target, cost)
        lo = max(1, _roll(power.rolls, "strikes_min", 1))
        hi = max(lo, _roll(power.rolls, "strikes_max", lo))
        strikes = roller.randint(lo, hi) if hasattr(roller, "randint") else lo + int(roller.random() * (hi - lo + 1))
        burst_pct = _roll(power.rolls, "burst_pct", 0)
        logs: list[ActionLog] = [
            self._effect_log(
                round_no,
                target,
                f"{actor.snapshot.name} 雷罚引动雷殛，消耗 {consumed} 层，唤出 {strikes} 次小型雷劫。",
                actor_name=actor.snapshot.name,
            )
        ]
        for _ in range(strikes):
            live_rods = [combatant for combatant in rods if combatant.hp > 0]
            if not live_rods:
                break
            victim = roller.choice(live_rods) if hasattr(roller, "choice") else live_rods[int(roller.random() * len(live_rods))]
            nested: list[ActionLog] = []
            cause = self._effect_log(
                round_no,
                victim,
                f"小型雷劫劈向 {victim.snapshot.name}。",
                actor_name=actor.snapshot.name,
            )
            nested.append(cause)
            burst_damage = max(1, self._current_atk(actor) * burst_pct // 100)
            self._apply_damage(
                victim,
                burst_damage,
                respects_resilience=False,
                actor=actor,
                round_no=round_no,
                logs=nested,
                cause=cause,
            )
            logs.extend(nested)
            if actor.hp <= 0 or target.hp <= 0:
                break
        if target.hp > 0:
            self._add_target_leihen(target, actor, 1)
            layers = self._target_leihen_count(target)
            logs.append(
                self._effect_log(
                    round_no,
                    target,
                    f"雷罚余威在 {target.snapshot.name} 身上留下 1 层雷殛（{layers}）。",
                    actor_name=actor.snapshot.name,
                )
            )
        return logs

    def _threshold_will_log(self, target: _CombatState, threshold: int, scene: set[str]) -> bool:
        if threshold in (50, 25):
            if threshold in target.huichun_triggered_thresholds:
                return False
            return any(
                entry.affix_id == "huichun" and self._scene_matches(entry, scene)
                for entry in target.snapshot.affixes
            )
        if threshold != 30 or threshold in target.low_hp_marks:
            return False
        return any(
            entry.affix_id == "liekai" and self._scene_matches(entry, scene)
            for entry in target.snapshot.affixes
        )

    def _trigger_low_hp_threshold(
        self,
        round_no: int,
        target: _CombatState,
        threshold: int,
        scene: set[str],
    ) -> list[ActionLog]:
        logs: list[ActionLog] = []
        max_hp = target.get_max_hp()
        if threshold in (50, 25):
            if threshold in target.huichun_triggered_thresholds:
                return logs
            target.huichun_triggered_thresholds.add(threshold)
            for entry in target.snapshot.affixes:
                if entry.affix_id != "huichun" or not self._scene_matches(entry, scene):
                    continue
                healed = self._heal(target, _roll(entry.rolls, "heal_pct", 0), logs=logs)
                stacks = _roll(entry.rolls, "shengxi_stacks", 2)
                for _ in range(stacks):
                    self._add_status(target, _StatusEffect("生息"))
                logs.append(self._hp_change_log(round_no, target, f"{target.snapshot.name} 的回春发动（生命降至 {threshold}%），回复 {format_big_number(healed)} 点生命并叠加 {stacks} 层生息，余血 {format_big_number(target.hp)}。"))
            return logs

        if threshold != 30 or threshold in target.low_hp_marks:
            return logs
        target.low_hp_marks.add(threshold)
        for entry in target.snapshot.affixes:
            if entry.affix_id != "liekai" or not self._scene_matches(entry, scene):
                continue
            shield_pct = _roll(entry.rolls, "shield_pct", 30)
            shield_amount = max(1, max_hp * shield_pct // 100)
            backlash_pct = _roll(entry.rolls, "backlash_pct", 50)
            self._add_status(target, _StatusEffect("裂铠", shield=shield_amount, backlash_pct=backlash_pct))
            logs.append(
                self._effect_log(
                    round_no,
                    target,
                    f"{target.snapshot.name} 的裂铠展开！获得 {shield_amount} 点护盾，受击反噬来犯之敌。",
                )
            )
        return logs

    def _trigger_round_end(
        self,
        round_no: int,
        challenger: _CombatState,
        defender: _CombatState,
        roller: random.Random,
    ) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for state in (challenger, defender):
            if state.hp <= 0:
                continue
            for entry in state.snapshot.affixes:
                if entry.affix_id == "dengxiao" and self._status_count(state, "登霄") < 8:
                    self._add_status(state, _StatusEffect("登霄", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0)))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 登霄势涨，后期威势更盛。"))
            # 灼烧：层数 = 本回合结算次数，每层各打一包；不扣层
            for status in list(state.statuses):
                if state.hp <= 0:
                    break
                if status not in state.statuses or not status.is_active() or status.burn_pct <= 0 or status.name != "灼烧":
                    continue
                stacks = status.stacks
                if stacks <= 0:
                    continue
                source_atk = self._current_atk(status.source) if status.source is not None else state.snapshot.atk
                raw_damage = max(1, int(source_atk * status.burn_pct / 100))
                fenmai = status.source is not None and status.source.snapshot.spirit_power is not None and status.source.snapshot.spirit_power.power_id == "fenmai"
                shisheng = status.source is not None and status.source.snapshot.spirit_power is not None and status.source.snapshot.spirit_power.power_id == "shisheng"
                source_hp_before = status.source.hp if shisheng else 0
                fenmai_shred = 0
                pending_cause: ActionLog | None = None
                pending_damage = 0
                pending_avoid: ActionLog | None = None
                pending_avoid_kind: str | None = None
                pending_avoid_count = 0

                def flush_avoid() -> None:
                    nonlocal pending_avoid, pending_avoid_kind, pending_avoid_count
                    if pending_avoid is None:
                        return
                    if pending_avoid_count > 1:
                        name = state.snapshot.name
                        if pending_avoid_kind == "xuanjia":
                            pending_avoid.text = f"{name} 的玄甲连挡 {pending_avoid_count} 次灼烧。"
                        elif pending_avoid_kind == "fengdun":
                            text = pending_avoid.text or ""
                            if "叠至第" in text:
                                suffix = text.split("叠至第", 1)[1]
                                pending_avoid.text = f"{name} 以风遁连闪 {pending_avoid_count} 次灼烧，叠至第{suffix}"
                            else:
                                pending_avoid.text = f"{name} 以风遁连闪 {pending_avoid_count} 次灼烧。"
                    logs.append(pending_avoid)
                    pending_avoid = None
                    pending_avoid_kind = None
                    pending_avoid_count = 0

                for _ in range(stacks):
                    if state.hp <= 0:
                        break
                    cause = self._effect_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 受 {stacks} 层灼烧侵蚀。",
                        actor_name=status.source.snapshot.name if status.source is not None else None,
                    )
                    nested: list[ActionLog] = []
                    actual_damage = self._apply_typed_damage(
                        state,
                        raw_damage,
                        _BURN_DOT_PROFILE,
                        actor=status.source,
                        cause=cause,
                        round_no=round_no,
                        logs=nested,
                    )
                    extra = [log for log in nested if not (log.text and "噬生吞回血气" in log.text)]
                    if actual_damage <= 0:
                        pending_cause = None
                        pending_damage = 0
                        kind = None
                        primary = extra[0] if extra else None
                        rest = extra[1:] if extra else []
                        text = primary.text if primary is not None else ""
                        if text and "以风遁闪开本次伤害" in text:
                            kind = "fengdun"
                        elif text and "完全格挡本次伤害" in text:
                            kind = "xuanjia"
                        if kind is None or primary is None:
                            flush_avoid()
                            # 护盾吸收了本次灼烧（造成 0 伤害）时仍保留战报，显示护盾吸收结果
                            if primary is None and cause.damage == 0 and cause.shield_after is not None:
                                logs.append(cause)
                            logs.extend(extra)
                            continue
                        if pending_avoid is not None and pending_avoid_kind != kind:
                            flush_avoid()
                        pending_avoid = primary
                        pending_avoid_kind = kind
                        pending_avoid_count = pending_avoid_count + 1 if pending_avoid_kind == kind else 1
                        if rest:
                            flush_avoid()
                            logs.extend(rest)
                        continue
                    flush_avoid()
                    if pending_cause is None:
                        pending_cause = cause
                        pending_damage = cause.damage
                        logs.append(cause)
                    else:
                        pending_damage += cause.damage
                        pending_cause.damage = pending_damage
                        pending_cause.target_hp_after = cause.target_hp_after
                        pending_cause.shield_after = cause.shield_after
                        if pending_cause.text:
                            pending_cause.text = self._append_hp_suffix(
                                self._strip_hp_suffix(pending_cause.text),
                                pending_damage,
                                pending_cause.target_hp_after,
                                pending_cause.shield_after,
                            )
                    if extra:
                        pending_cause = None
                        pending_damage = 0
                        logs.extend(extra)
                    if fenmai:
                        before_max = state.get_max_hp()
                        self._modify_max_hp(state, -actual_damage, also_heal=False)
                        fenmai_shred += before_max - state.get_max_hp()
                flush_avoid()
                if shisheng and status.source is not None:
                    healed = status.source.hp - source_hp_before
                    if healed > 0:
                        logs.append(
                            self._hp_change_log(
                                round_no,
                                status.source,
                                f"{status.source.snapshot.name} 借噬生吞回血气，回复了 {format_big_number(healed)} 点生命，余血 {format_big_number(status.source.hp)}。",
                            )
                        )
                if fenmai_shred > 0:
                    logs.append(
                        self._hp_change_log(
                            round_no,
                            state,
                            f"{state.snapshot.name} 的生机被焚脉削去 {format_big_number(fenmai_shred)} 点上限，余血 {format_big_number(state.hp)}。",
                        )
                    )
        # 蔓咒增殖：回合结束时若目标有蔓咒，自动叠加 1 层（最多 7 层）
        max_manzhou_stacks = 7
        for state, opponent in ((challenger, defender), (defender, challenger)):
            if state.hp <= 0 or opponent.hp <= 0:
                continue
            manzhou_count = self._status_count(opponent, "蔓咒")
            if manzhou_count > 0:
                # 从 state 的词条中获取蔓咒的降杀伐数值
                manzhou_atk_down = 4  # fallback
                for entry in state.snapshot.affixes:
                    if entry.affix_id == "manzhou":
                        manzhou_atk_down = _roll(entry.rolls, "atk_down_pct", 4)
                        break
                if manzhou_count < max_manzhou_stacks:
                    self._add_status(opponent, _StatusEffect("蔓咒", atk_pct=-manzhou_atk_down, is_debuff=True, source=state))
                self._add_curse_seal(opponent, state, 1)
                logs.append(
                    self._effect_log(
                        round_no,
                        opponent,
                        f"{opponent.snapshot.name} 的蔓咒扩散至 {min(manzhou_count + 1, max_manzhou_stacks)}/{max_manzhou_stacks} 层，并滋生 1 层咒印。",
                        actor_name=state.snapshot.name,
                    )
                )
        # 器灵神通 round_end 钩子（涤世等）
        for state, opponent in ((challenger, defender), (defender, challenger)):
            if state.hp > 0 and opponent.hp > 0:
                logs.extend(self._trigger_spirit_round_end(round_no, state, opponent, roller))
        # 绝命结算：回合结束时消耗咒印凝死兆并立刻结算层数伤害
        for state, opponent in ((challenger, defender), (defender, challenger)):
            if state.hp > 0 and opponent.hp > 0:
                logs.extend(self._settle_jueming_marks(round_no, state, opponent))
        return logs

    def _consume_burn_stacks(self, state: _CombatState, amount: int) -> int:
        remaining = max(0, amount)
        consumed = 0
        for status in list(state.statuses):
            if remaining <= 0:
                break
            if status.name != "灼烧" or not status.is_active() or status.burn_pct <= 0:
                continue
            take = min(status.stacks, remaining)
            status.stacks -= take
            remaining -= take
            consumed += take
            if status.stacks <= 0:
                state.statuses.remove(status)
        return consumed

    def _maybe_trigger_burn_exhausted(
        self,
        actor: _CombatState | None,
        target: _CombatState,
        *,
        had_burn: bool,
        round_no: int,
        roller: random.Random | None = None,
    ) -> list[ActionLog]:
        if not had_burn or self._has_burn(target) or target.hp <= 0 or actor is None:
            return []
        return self._trigger_on_burn_consumed(actor, target, round_no=round_no, roller=roller or actor.roller or self.rng)

    def _trigger_shiyan_explodes(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        roller: random.Random,
    ) -> list[ActionLog]:
        power = actor.snapshot.spirit_power
        if power is None or power.power_id != "shiyan" or target.hp <= 0:
            return []
        cost = max(1, _roll(power.rolls, "cost_stacks", 10))
        per_burn_pct = power.rolls.get("per_burn_pct", 25)
        wound_stacks = power.rolls.get("wound_stacks", 1)
        logs: list[ActionLog] = []
        had_burn = self._has_burn(target)
        while target.hp > 0 and self._burn_stacks(target) >= cost:
            consumed = self._consume_burn_stacks(target, cost)
            if consumed <= 0:
                break
            base_damage = max(1, int(self._current_atk(actor) * consumed * per_burn_pct / 100))
            nested: list[ActionLog] = []
            cause = self._effect_log(
                round_no,
                target,
                f"{actor.snapshot.name} 的蚀焰倾泻而出，焚野翻涌。",
                actor_name=actor.snapshot.name,
            )
            nested.append(cause)
            self._apply_typed_damage(
                target, base_damage, _SHIYAN_PROFILE, actor=actor, round_no=round_no, logs=nested, cause=cause
            )
            logs.extend(nested)
            if wound_stacks > 0 and target.hp > 0:
                add_wounds = self._add_wound(target, actor, wound_stacks)
                if add_wounds > 0:
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"蚀焰焚痕未消，{target.snapshot.name} 附加 {add_wounds} 层创伤。",
                            actor_name=actor.snapshot.name,
                        )
                    )
        logs.extend(self._maybe_trigger_burn_exhausted(actor, target, had_burn=had_burn, round_no=round_no, roller=roller))
        return logs

    def _trigger_spirit_on_hit(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        actual_damage: int,
        roller: random.Random,
        *,
        source: str,
        scene: set[str],
    ) -> list[ActionLog]:
        power = actor.snapshot.spirit_power
        if power is None:
            return []

        logs: list[ActionLog] = []

        # 蚀焰：命中后每消耗 n 层引爆一次，可连爆；不吃 ≥6 / 冷却。
        if power.power_id == "shiyan" and source == _DamageSource.ATTACK and target.hp > 0:
            logs.extend(self._trigger_shiyan_explodes(round_no, actor, target, roller))
        if power.power_id == "fenmai" and source == _DamageSource.ATTACK and target.hp > 0:
            gain = max(1, _roll(power.rolls, "burn_stacks", 1))
            burn_pct = self._zhuohun_burn_pct(actor)
            self._apply_burn_to_target(
                target,
                actor,
                stacks=gain,
                per_stack_pct=burn_pct,
                round_no=round_no,
                logs=logs,
            )
        if source == _DamageSource.ATTACK:
            if power.power_id == "jinmai" and target.hp > 0:
                disrupt_layers = self._status_count(target, "破步") + self._status_count(target, "创伤")
                proc_pct = _roll(power.rolls, "proc_pct", 0) + disrupt_layers * _roll(power.rolls, "per_disrupt_pct", 1)
                if proc_pct > 0 and roller.random() < proc_pct / 100:
                    self._add_action_seal(target, actor, 1)
                    break_spirit_gained = self._status_count(target, "破封灵势") < 10
                    if break_spirit_gained:
                        self._add_status(target, _StatusEffect("破封灵势", atk_pct=10, cleanseable=False))
                    break_spirit_text = "获得 1 层破封灵势" if break_spirit_gained else "破封灵势已达上限"
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的禁脉贯入经络，{target.snapshot.name} 下一次行动被封，{break_spirit_text}。",
                            actor_name=actor.snapshot.name,
                        )
                    )
            if power.power_id == "wanzhou" and target.hp > 0:
                added = _roll(power.rolls, "curse_on_hit", 1)
                extra_pct = _roll(power.rolls, "extra_curse_pct", 0)
                if extra_pct > 0 and roller.random() < extra_pct / 100:
                    added += 1
                self._add_curse_seal(target, actor, added)
                logs.append(self._effect_log(round_no, target, f"{actor.snapshot.name} 万咒落印，{target.snapshot.name} 获得 {added} 层咒印。", actor_name=actor.snapshot.name))
                threshold = _roll(power.rolls, "burst_threshold", 3)
                if self._curse_seal_count(target) >= threshold:
                    logs.extend(self._trigger_wanzhou_burst(round_no, actor, target, roller))
            if power.power_id == "leifa" and target.hp > 0:
                logs.extend(self._trigger_leifa(round_no, actor, target, roller))

        if actual_damage <= 0:
            return logs

        if source != _DamageSource.ATTACK:
            return logs

        if power.power_id == "duofeng" and target.hp > 0 and self._has_debuff(target) and self._status_count(actor, "夺锋") < 5:
            self._add_status(actor, _StatusEffect("夺锋", atk_pct=power.rolls["atk_pct"], agility_pct=power.rolls["agi_pct"]))
            self._add_status(target, _StatusEffect("夺锋", atk_pct=-power.rolls["atk_pct"], agility_pct=-power.rolls["agi_pct"], is_debuff=True, source=actor))
            logs.append(
                self._effect_log(
                    round_no,
                    target,
                    f"{actor.snapshot.name} 借夺锋摄其锐气，杀伐与身法此消彼长。",
                    actor_name=actor.snapshot.name,
                )
            )

        if power.power_id == "suijue" and target.hp > 0 and (self._status_count(target, "守势") > 0 or self._positive_status_count(target) >= 2):
            damage_pct = power.rolls.get("damage_pct", 0)
            stacks = power.rolls.get("stacks", 2)
            nested: list[ActionLog] = []
            cause = self._effect_log(
                round_no,
                target,
                f"{actor.snapshot.name} 的碎阙破开守势。",
                actor_name=actor.snapshot.name,
            )
            nested.append(cause)
            extra_damage = self._apply_typed_damage(
                target,
                max(1, self._current_atk(actor) * damage_pct // 100),
                _NORMAL_DAMAGE_PROFILE,
                actor=actor,
                round_no=round_no,
                logs=nested,
                scene=scene,
                cause=cause,
            )
            actor.action_attack_damage_dealt += extra_damage
            stripped = 0
            for _ in range(stacks):
                if self._remove_one_positive_status(target) is not None:
                    stripped += 1
            followups: list[ActionLog] = []
            if stripped > 0:
                for _ in range(stripped):
                    self._add_status(actor, _StatusEffect("碎阙", damage_dealt_pct=damage_pct // 2 or 1, remaining_hits=1))
                followups.extend(self._trigger_on_effect_lost_to_enemy(round_no, actor, target, stripped))
                followups.extend(self._trigger_cleanse_followups(round_no, target, stripped, actor))
                if cause.text:
                    reason = f"{self._strip_hp_suffix(cause.text)[:-1]}，震散 {stripped} 层正面效果。"
                    cause.text = self._append_hp_suffix(
                        reason, cause.damage, cause.target_hp_after, cause.shield_after
                    )
            if extra_damage > 0 or stripped > 0 or any(log is not cause for log in nested):
                logs.extend(nested)
            logs.extend(followups)

        return logs

    def _trigger_spirit_on_be_hit(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        actual_damage: int,
        roller: random.Random,
        *,
        source: str,
        had_damage_reduction: bool = False,
    ) -> list[ActionLog]:
        power = target.snapshot.spirit_power
        if power is None or actual_damage <= 0:
            return []

        logs: list[ActionLog] = []
        if power.power_id == "guifeng" and source == _DamageSource.ATTACK and target.hp > 0 and actor.hp > 0 and target.counter_used_round != round_no:
            proc_pct = power.rolls["proc_pct"] + (15 if target.hp * actor.get_max_hp() < actor.hp * target.get_max_hp() else 0)
            if roller.random() <= (proc_pct / 100):
                target.counter_used_round = round_no
                counter_damage = max(1, self._current_atk(target) * power.rolls["damage_pct"] // 100)
                nested: list[ActionLog] = []
                cause = self._effect_log(
                    round_no,
                    actor,
                    f"{target.snapshot.name} 借归锋逆起一击。",
                    actor_name=target.snapshot.name,
                )
                nested.append(cause)
                counter_damage = self._apply_typed_damage(
                    actor,
                    min(actor.hp, counter_damage),
                    _NORMAL_DAMAGE_PROFILE,
                    actor=target,
                    round_no=round_no,
                    logs=nested,
                    cause=cause,
                )
                self._consume_break_spirit(round_no, target, nested)
                logs.extend(nested)

        if power.power_id == "huajing" and source == _DamageSource.ATTACK and target.hp > 0 and had_damage_reduction:
            healed = self._heal_by_damage(target, actual_damage, power.rolls["convert_pct"], logs=logs)
            if healed > 0:
                logs.append(self._hp_change_log(round_no, target, f"{target.snapshot.name} 运转化劲，借承伤回转了 {format_big_number(healed)} 点生命，余血 {format_big_number(target.hp)}。"))

        return logs

    def _trigger_spirit_revive(self, round_no: int, state: _CombatState) -> list[ActionLog]:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "niepan" or state.hp > 0:
            return []
        logs: list[ActionLog] = []
        while state.hp <= 0:
            cost = max(1, int(power.rolls.get("cost_stacks", 6)))
            # 必须有足够生息才能复活
            consumed = self._consume_shengxi(state, cost)
            if consumed < cost:
                # 生息不足，彻底倒地；残留余伤一并作废
                state.pending_overkill = 0
                return logs
            revive_pct = max(1, int(power.rolls.get("revive_hp_pct", 30)))
            max_hp = state.get_max_hp()
            heal_amount = max(1, int(max_hp * revive_pct / 100))
            state.hp = min(max_hp, heal_amount)
            state.niepan_revive_count += 1
            # 累加 atk_pct + agility_pct 永久 buff（duration=None，hits=None）
            atk_bonus = max(0, int(power.rolls.get("per_revive_atk_pct", 0)))
            speed_bonus = max(0, int(power.rolls.get("per_revive_speed_pct", 0)))
            if atk_bonus > 0 or speed_bonus > 0:
                self._add_status(state, _StatusEffect("涅槃·余烬", atk_pct=atk_bonus, agility_pct=speed_bonus))
            logs.append(
                self._hp_change_log(
                    round_no,
                    state,
                    f"{state.snapshot.name} 涅槃再起（第 {state.niepan_revive_count} 次），消耗 {cost} 层生息回复 {format_big_number(heal_amount)} 点生命，余血 {format_big_number(state.hp)}；杀伐 +{atk_bonus}%、身法 +{speed_bonus}%（持续生效）。",
                )
            )
            # 余伤追身：击杀当下的溢出伤害在复活后继续结算，可能连环再杀、直至生息耗尽或余伤打完
            if state.pending_overkill > 0:
                leftover = state.pending_overkill
                applied = min(state.hp, leftover)
                state.hp -= applied
                state.pending_overkill = leftover - applied
                logs.append(
                    self._hp_change_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 涅槃余烬未稳，余劲追身补上 {format_big_number(applied)} 点余伤，余血 {format_big_number(state.hp)}。",
                    )
                )
        return logs

    def _revive_checkpoint(self, round_no: int, *states: _CombatState) -> list[ActionLog]:
        """Resolve both combatants' revives after a complete effect chain."""
        logs: list[ActionLog] = []
        for state in states:
            logs.extend(self._trigger_spirit_revive(round_no, state))
        return logs

    def _consume_shengxi(self, state: _CombatState, amount: int) -> int:
        """事务式消耗 amount 层生息；层数不足时不做任何消耗。"""
        if amount <= 0 or self._status_count(state, "生息") < amount:
            return 0
        remaining = amount
        for status in list(state.statuses):
            if remaining <= 0:
                break
            if status.name != "生息" or not status.is_active():
                continue
            take = min(status.stacks, remaining)
            status.stacks -= take
            remaining -= take
            if status.stacks <= 0:
                state.statuses.remove(status)
        return amount

    def _before_attack_bonus_pct(
        self,
        actor: _CombatState,
        target: _CombatState,
        scene: set[str],
        *,
        had_wound_before_attack: bool = False,
    ) -> int:
        total = 0
        target_debuff_count = self._debuff_effect_count(target)
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "zhuiming":
                    if target.hp * 100 > target.get_max_hp() * 70:
                        total += _roll(entry.rolls, "damage_pct", 0)
                case "liechuang":
                    if had_wound_before_attack:
                        total += _roll(entry.rolls, "damage_pct", 0)
                case "duanyue":
                    total += min(target_debuff_count * _roll(entry.rolls, "per_debuff_pct", _roll(entry.rolls, "damage_pct", 0)), _roll(entry.rolls, "max_bonus_pct", _roll(entry.rolls, "damage_pct", 0)))
                case "zhenguan":
                    target_has_guard = self._has_damage_reduction_status(target) or self._status_count(target, "守势") > 0
                    actor_hp_pct = actor.hp * 100 // max(1, actor.get_max_hp())
                    target_hp_pct = target.hp * 100 // max(1, target.get_max_hp())
                    if target_has_guard or target_hp_pct > actor_hp_pct:
                        total += _roll(entry.rolls, "damage_pct", 0)
                case "zhengheng":
                    actor_hp_pct = actor.hp * 100 // max(1, actor.get_max_hp())
                    target_hp_pct = target.hp * 100 // max(1, target.get_max_hp())
                    if actor_hp_pct < target_hp_pct:
                        gap = target_hp_pct - actor_hp_pct
                        total += min(_roll(entry.rolls, "damage_pct", 0), max(1, _roll(entry.rolls, "damage_pct", 0) * gap // 100))
                case "jinhuo":
                    if not self._has_burn(target):
                        continue
                    bonus = _roll(entry.rolls, "damage_pct", 0)
                    stacks = self._burn_stacks(target)
                    if stacks > 3:
                        bonus += (stacks - 3) * _roll(entry.rolls, "per_stack_pct", 0)
                    total += bonus
                case "fengxing":
                    stacks = self._status_count(actor, "风行")
                    if stacks > 0:
                        total += _roll(entry.rolls, "damage_pct", 0) * stacks
        return total

    def _damage_dealt_pct(self, state: _CombatState) -> int:
        total = sum(
            status.damage_dealt_pct * status.stacks
            for status in self._active_statuses(state)
            if status.active_from_round <= state.current_round
        )
        # 灵御：战斗前 6 回合每层灵势降低自身造成伤害（拖时间换爆发期）
        power = state.snapshot.spirit_power
        if power is not None and power.power_id == "lingyu" and state.current_round <= 6:
            total -= self._status_count(state, "灵势") * power.rolls.get("self_damage_down_per_stack_pct", 0)
        return total

    def _spirit_damage_bonus_pct(
        self,
        actor: _CombatState,
        target: _CombatState,
        before_attack_bonus: int = 0,
    ) -> int:
        power = actor.snapshot.spirit_power
        if power is None:
            return 0
        match power.power_id:
            case "luejie":
                bonus = self._debuff_count(target) * power.rolls["per_debuff_pct"]
                return min(bonus, power.rolls["max_bonus_pct"])
            case "chengshi":
                affix_count = sum(1 for entry in actor.snapshot.affixes if entry.affix_id in self._DAMAGE_AFFIX_IDS)
                if affix_count < 2:
                    return 0
                return power.rolls["base_pct"] + (affix_count - 2) * power.rolls["per_type_pct"]
            case "lingyong":
                lingshi_layers = min(LINGYONG_NORMAL_STACK_CAP, self._status_count(actor, "灵势"))
                return lingshi_layers * power.rolls.get("per_stack_pct", 0)
            case "zhuying":
                actor_agi = self._current_agility(actor)
                target_agi = self._current_agility(target)
                if actor_agi <= target_agi:
                    return 0
                gap_steps = max(0, ((actor_agi * 100 // max(1, target_agi)) - 100) // 25)
                bonus = power.rolls["damage_pct"] + gap_steps * power.rolls["per_25_pct"]
                return min(bonus, power.rolls["max_bonus_pct"])
            case "zhuifeng":
                return 0
            case _:
                return 0

    def _zhuifeng_phase(self, state: _CombatState) -> str | None:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "zhuifeng" or not state.is_first_mover:
            return None
        return "hunt" if 1 <= state.current_round <= 3 else None

    def _zhuifeng_crit_bonus_pct(self, state: _CombatState) -> int:
        power = state.snapshot.spirit_power
        if power is None or self._zhuifeng_phase(state) is None:
            return 0
        return _roll(power.rolls, "r1_crit_bonus", 0)

    def _zhuifeng_force_hit(self, state: _CombatState) -> bool:
        power = state.snapshot.spirit_power
        return (
            power is not None
            and power.power_id == "zhuifeng"
            and state.is_first_mover
            and state.zhuifeng_first_attack_pending
        )


    def _damage_taken_pct(self, state: _CombatState) -> int:
        return sum(status.damage_taken_pct * status.stacks for status in self._active_statuses(state))

    def _damage_reduction_pct(self, state: _CombatState) -> int:
        total = sum(status.damage_reduction_pct * status.stacks for status in self._active_statuses(state))
        # 灵御：战斗前 6 回合每层灵势提供减伤；第 7 回合起效果消失
        power = state.snapshot.spirit_power
        if power is not None and power.power_id == "lingyu" and state.current_round <= 6:
            total += self._status_count(state, "灵势") * power.rolls.get("reduce_per_stack_pct", 0)
        return total

    def _pierce_pct(self, actor: _CombatState, scene: set[str], target: _CombatState | None = None) -> int:
        total = 0
        dengxiao_layers = self._status_count(actor, "登霄")
        if dengxiao_layers >= 6:
            for entry in actor.snapshot.affixes:
                if entry.affix_id == "dengxiao" and self._scene_matches(entry, scene):
                    total += _roll(entry.rolls, "pierce_pct", 0) * (dengxiao_layers - 5)
        leihen_layers = self._target_leihen_count(target) if target is not None else 0
        if leihen_layers > 0:
            for entry in actor.snapshot.affixes:
                if entry.affix_id == "liekong" and self._scene_matches(entry, scene):
                    step = max(1, _roll(entry.rolls, "per_stacks", 4))
                    total += _roll(entry.rolls, "pierce_pct", 0) * (leihen_layers // step)
        # 透命：通用穿透，无前置条件，多件叠加
        for entry in actor.snapshot.affixes:
            if entry.affix_id == "tongming" and self._scene_matches(entry, scene):
                total += _roll(entry.rolls, "pierce_pct", 0)
        return total

    def _heal_received_pct(self, state: _CombatState) -> int:
        total = sum(status.heal_received_pct * status.stacks for status in self._active_statuses(state))
        power = state.snapshot.spirit_power
        if power is not None and power.power_id == "chunsheng":
            total += _roll(power.rolls, "heal_received_pct", 0)
        if power is not None and power.power_id == "xuanjia":
            total -= _roll(power.rolls, "heal_down_pct", 0)
        return total

    def _record_xuekuang_hp_loss(self, state: _CombatState, amount: int) -> None:
        if amount <= 0:
            return
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "xuekuang":
            return
        if state.xuekuang_base_max_hp <= 0:
            state.xuekuang_base_max_hp = max(1, state.get_max_hp())
        state.xuekuang_lost_hp += amount
        state.xuekuang_loss_bank += amount
        step_pct = max(1, _roll(power.rolls, "loss_step_pct", 10))
        while True:
            step_hp = max(1, state.get_max_hp() * step_pct // 100)
            if state.xuekuang_loss_bank < step_hp:
                break
            state.xuekuang_loss_bank -= step_hp
            state.xuekuang_layers += 1
            self._sync_xuekuang_max_hp(state)

    def _xuekuang_stat_pct(self, state: _CombatState) -> int:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "xuekuang":
            return 0
        return state.xuekuang_layers * _roll(power.rolls, "stat_pct", 0)

    def _sync_xuekuang_max_hp(self, state: _CombatState) -> None:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "xuekuang" or state.xuekuang_base_max_hp <= 0:
            return
        bonus = self._xuekuang_stat_pct(state)
        target = max(1, state.xuekuang_base_max_hp * (100 + bonus) // 100)
        delta = target - state.get_max_hp()
        if delta != 0:
            self._modify_max_hp(state, delta, also_heal=False)

    def _current_atk(self, state: _CombatState) -> int:
        return max(1, int(state.snapshot.atk * (1 + (self._stat_bonus_pct(state, "atk_pct") + self._xuekuang_stat_pct(state)) / 100)))

    def _current_defense(self, state: _CombatState) -> int:
        return max(1, int(state.snapshot.defense * (1 + (self._stat_bonus_pct(state, "defense_pct") + self._xuekuang_stat_pct(state)) / 100)))

    def _current_agility(self, state: _CombatState) -> int:
        return max(1, int(state.snapshot.agility * (1 + (self._stat_bonus_pct(state, "agility_pct") + self._xuekuang_stat_pct(state)) / 100)))

    def _stat_bonus_pct(self, state: _CombatState, field_name: str) -> int:
        total = 0
        lingshi_remaining = LINGYONG_NORMAL_STACK_CAP
        for status in self._active_statuses(state):
            stacks = status.stacks
            if status.name == "灵势":
                # 超出十层仍可被驱散，但只参与灵涌真伤与净化，不放大三维。
                stacks = min(stacks, lingshi_remaining)
                lingshi_remaining -= stacks
            total += getattr(status, field_name) * stacks
        return total

    def _has_debuff(self, state: _CombatState) -> bool:
        return any(status.is_debuff for status in self._active_statuses(state))

    def _debuff_count(self, state: _CombatState) -> int:
        return sum(status.stacks for status in self._active_statuses(state) if status.is_debuff)

    def _debuff_effect_count(self, state: _CombatState) -> int:
        return sum(1 for status in self._active_statuses(state) if status.is_debuff)

    def _positive_status_count(self, state: _CombatState) -> int:
        # 护盾（shield > 0）免疫净化：不计入可净化的正向状态总数
        return sum(status.stacks for status in self._active_statuses(state) if not status.is_debuff and status.shield <= 0 and status.cleanseable)

    def _has_burn(self, state: _CombatState) -> bool:
        return any(status.name == "灼烧" and status.burn_pct > 0 for status in self._active_statuses(state))

    def _burn_source(self, state: _CombatState) -> _CombatState | None:
        for status in self._active_statuses(state):
            if status.name == "灼烧" and status.burn_pct > 0:
                return status.source
        return None

    def _burn_stacks(self, state: _CombatState) -> int:
        return sum(
            status.stacks
            for status in self._active_statuses(state)
            if status.name == "灼烧" and status.burn_pct > 0
        )

    def _zhuohun_burn_pct(self, actor: _CombatState) -> int:
        """附加灼烧时的每层杀伐%：带灼魂词条时用其 roll 值，否则默认灼魂下限。"""
        return next(
            (
                _roll(e.rolls, "burn_atk_pct", get_default_burn_atk_pct())
                for e in actor.snapshot.affixes
                if e.affix_id == "zhuohun"
            ),
            get_default_burn_atk_pct(),
        )

    def _apply_burn_to_target(
        self,
        target: _CombatState,
        actor: _CombatState,
        *,
        stacks: int,
        per_stack_pct: int,
        round_no: int,
        logs: list,
        is_relight: bool = False,
    ) -> None:
        if stacks <= 0:
            return
        existing = next(
            (
                status
                for status in target.statuses
                if status.name == "灼烧" and status.is_active() and status.burn_pct > 0
            ),
            None,
        )
        if existing is None:
            self._add_status(
                target,
                _StatusEffect(
                    "灼烧",
                    stacks=stacks,
                    burn_pct=per_stack_pct,
                    is_debuff=True,
                    source=actor,
                    is_relight=is_relight,
                ),
            )
        else:
            existing.stacks += stacks
            if per_stack_pct > existing.burn_pct:
                existing.burn_pct = per_stack_pct
                existing.source = actor
            if not is_relight:
                existing.is_relight = False
        logs.append(
            self._effect_log(
                round_no,
                target,
                f"{actor.snapshot.name} 灼魂引焰，{target.snapshot.name} 附 {stacks} 层灼烧（每层 {per_stack_pct}% 杀伐）。",
                actor_name=actor.snapshot.name,
            )
        )

    def _trigger_on_burn_consumed(
        self,
        actor: _CombatState,
        target: _CombatState,
        *,
        round_no: int,
        roller: random.Random,
    ) -> list[ActionLog]:
        """灼烧层数归零时触发。当前仅用于余烬词条重燃。"""
        logs: list[ActionLog] = []
        if target.hp <= 0:
            return logs
        for entry in actor.snapshot.affixes:
            affix_def = get_artifact_affix_definition(entry.affix_id)
            if affix_def.trigger != "on_burn_consumed":
                continue
            if entry.affix_id == "yujin":
                proc_pct = _roll(entry.rolls, "proc_pct", 0)
                if roller.random() > proc_pct / 100:
                    continue
                stacks = _roll(entry.rolls, "relight_stacks", 2)
                burn_pct = _roll(entry.rolls, "relight_burn_pct", 20)
                relight_logs: list = []
                self._apply_burn_to_target(
                    target,
                    actor,
                    stacks=stacks,
                    per_stack_pct=burn_pct,
                    round_no=round_no,
                    logs=relight_logs,
                    is_relight=True,
                )
                logs.extend(relight_logs)
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 余烬未熄，{target.snapshot.name} 重新点燃 {stacks} 层灼烧。",
                        actor_name=actor.snapshot.name,
                    )
                )
        return logs

    def _yujin_crit_bonus(
        self,
        actor: _CombatState,
        target: _CombatState,
        scene: set[str],
    ) -> tuple[int, int]:
        if not self._has_burn(target):
            return (0, 0)
        stacks = self._burn_stacks(target)
        crit_rate = 0
        crit_dmg = 0
        for entry in actor.snapshot.affixes:
            if entry.affix_id != "yujin" or not self._scene_matches(entry, scene):
                continue
            crit_rate += _roll(entry.rolls, "crit_per_stack", 0) * stacks
            crit_dmg += _roll(entry.rolls, "crit_dmg_per_stack", 0) * stacks
        return (crit_rate, crit_dmg)

    def _has_damage_reduction_status(self, state: _CombatState) -> bool:
        return any(status.damage_reduction_pct > 0 for status in self._active_statuses(state))

    def _crit_bonus_pct(self, state: _CombatState) -> int:
        total = sum(status.crit_bonus_pct * status.stacks for status in self._active_statuses(state))
        total += self._zhuifeng_crit_bonus_pct(state)
        return total

    def _crit_damage_bonus_pct(self, state: _CombatState) -> int:
        return sum(status.crit_damage_pct * status.stacks for status in self._active_statuses(state))

    def _target_crit_bonus_pct(self, actor: _CombatState, target: _CombatState) -> int:
        return 0

    def _target_crit_damage_bonus_pct(self, actor: _CombatState, target: _CombatState) -> int:
        return 0

    def _dodge_bonus_pct(self, state: _CombatState) -> int:
        return sum(status.dodge_bonus_pct * status.stacks for status in self._active_statuses(state))

    def _has_guarantee_crit(self, state: _CombatState) -> bool:
        return any(status.guarantee_crit for status in self._active_statuses(state))

    def _total_shield(self, state: _CombatState) -> int:
        return sum(status.shield for status in self._active_statuses(state))

    def _status_shield_sum(self, state: _CombatState, name: str) -> int:
        """返回指定名称状态的总护盾量（用于追踪特定护盾消耗）。"""
        return sum(status.shield for status in self._active_statuses(state) if status.name == name)

    def _consume_shield(self, state: _CombatState, damage: int) -> int:
        """消耗护盾吸收伤害，返回剩余穿透伤害。"""
        remaining = damage
        for status in list(state.statuses):
            if remaining <= 0:
                break
            if not status.is_active() or status.shield <= 0:
                continue
            absorbed = min(status.shield, remaining)
            status.shield -= absorbed
            remaining -= absorbed
            if status.shield <= 0:
                state.statuses.remove(status)
        return remaining

    def _consume_shield_and_settle_liekai(
        self,
        target: _CombatState,
        actor: _CombatState | None,
        damage: int,
        round_no: int,
        logs: list[ActionLog] | None,
    ) -> int:
        """护盾消耗 + 裂铠碎盾反噬统一结算（普攻管线 / 蚀焰引爆 / 春生·追击 / 涤世·净化 共用）。

        - 先对 target 的全部"裂铠"状态拍快照（shield_before + backlash_pct）
        - 调用 _consume_shield 扣盾
        - 若 actor 不为 None 且仍存活：
            * 每件存在的裂铠各给 actor 加 1 层绝命印记（受击印记）
            * 每件本次受击中被打穿（shield_before > 0 且 status.shield == 0）的裂铠：
              反噬 backlash_pct% × shield_before 伤害 + actor.jueming_mark_stacks += 3
              + 推一条反噬 ActionLog（前提是 logs 不为 None 且实际伤害 > 0）
        返回穿透伤害（未被护盾吸收的部分）。
        """
        liekai_snapshots = [
            (status, status.shield, status.backlash_pct)
            for status in target.statuses
            if status.name == "裂铠" and status.is_active()
        ]
        if self._total_shield(target) > 0:
            remaining = self._consume_shield(target, damage)
        else:
            remaining = damage
        if actor is None or actor.hp <= 0:
            return remaining
        if liekai_snapshots:
            self._add_curse_seal(actor, target, len(liekai_snapshots))
        # 裂铠反噬走正常杀伐修正，但不再提供 actor 给护盾结算，避免碎盾反伤彼此递归。
        for status, shield_before, backlash_pct in liekai_snapshots:
            if actor.hp <= 0:
                break
            if shield_before <= 0 or status.shield > 0:
                continue
            backlash_damage = max(1, shield_before * (backlash_pct or 50) // 100)
            nested: list[ActionLog] = []
            cause = self._effect_log(
                round_no,
                actor,
                f"{target.snapshot.name} 的裂铠炸裂，碎片反噬 {actor.snapshot.name}，并附加 2 层咒印。",
                actor_name=target.snapshot.name,
            )
            nested.append(cause)
            backlash_actual = self._apply_typed_damage(
                actor,
                backlash_damage,
                _NORMAL_DAMAGE_PROFILE,
                actor=target,
                round_no=round_no,
                logs=nested,
                settle_liekai=False,
                cause=cause,
            )
            self._add_curse_seal(actor, target, 2)
            if (backlash_actual > 0 or any(log is not cause for log in nested)) and logs is not None:
                logs.extend(nested)
        return remaining

    def _status_count(self, state: _CombatState, name: str) -> int:
        return sum(status.stacks for status in self._active_statuses(state) if status.name == name)

    def _consume_break_spirit(self, round_no: int, state: _CombatState, logs: list[ActionLog]) -> None:
        released = self._status_count(state, "破封灵势")
        if released <= 0:
            return
        state.statuses = [status for status in state.statuses if status.name != "破封灵势"]
        logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 真正出手，{released} 层破封灵势随之消散。"))

    def _status_bonus_pct(self, state: _CombatState, name: str, field_name: str) -> int:
        return sum(getattr(status, field_name) * status.stacks for status in self._active_statuses(state) if status.name == name)

    @staticmethod
    def _consume_status_stack(state: _CombatState, status: _StatusEffect) -> None:
        if status.stacks > 1:
            status.stacks -= 1
        elif status in state.statuses:
            state.statuses.remove(status)

    def _remove_one_debuff(self, state: _CombatState) -> _StatusEffect | None:
        debuffs = [status for status in self._active_statuses(state) if status.is_debuff and status.cleanseable]
        if not debuffs:
            return None
        debuffs.sort(key=lambda status: 0 if status.burn_pct > 0 else 1)
        removed = debuffs[0]
        if removed.stacks > 1:
            removed.stacks -= 1
        else:
            state.statuses.remove(removed)
        return removed

    def _remove_one_positive_status(self, state: _CombatState) -> _StatusEffect | None:
        # 护盾（shield > 0）免疫净化
        positives = [status for status in self._active_statuses(state) if not status.is_debuff and status.shield <= 0 and status.cleanseable]
        if not positives:
            return None
        positives.sort(key=lambda status: 0 if status.name in {"灵势", "守势", "登霄"} else 1)
        removed = positives[0]
        if removed.stacks > 1:
            removed.stacks -= 1
        else:
            state.statuses.remove(removed)
        return removed

    def _remove_one_status_by_name(self, state: _CombatState, name: str) -> _StatusEffect | None:
        for status in self._active_statuses(state):
            if status.name == name:
                if status.stacks > 1:
                    status.stacks -= 1
                else:
                    state.statuses.remove(status)
                return status
        return None

    def _add_curse_seal(self, state: _CombatState, source: _CombatState | None, count: int = 1) -> None:
        for _ in range(max(0, count)):
            self._add_status(state, _StatusEffect("咒印", is_debuff=True, source=source))

    def _curse_seal_count(self, state: _CombatState) -> int:
        return self._status_count(state, "咒印")

    def _consume_curse_seal(self, state: _CombatState, count: int) -> int:
        consumed = 0
        for status in list(state.statuses):
            if consumed >= count:
                break
            if status.name == "咒印" and status.is_active():
                take = min(status.stacks, count - consumed)
                status.stacks -= take
                consumed += take
                if status.stacks <= 0:
                    state.statuses.remove(status)
        return consumed

    def _consume_all_curse_seals(self, state: _CombatState) -> int:
        count = self._curse_seal_count(state)
        if count > 0:
            self._consume_curse_seal(state, count)
        return count

    def _death_omen_count(self, state: _CombatState) -> int:
        return self._status_count(state, "死兆")

    def _add_death_omen(self, state: _CombatState, source: _CombatState, heal_down_pct: int) -> None:
        if self._death_omen_count(state) >= 3:
            return
        self._add_status(
            state,
            _StatusEffect("死兆", heal_received_pct=-heal_down_pct, is_debuff=True, source=source, cleanseable=False),
        )

    def _add_action_seal(self, state: _CombatState, source: _CombatState | None, count: int = 1) -> None:
        for _ in range(max(0, count)):
            self._add_status(state, _StatusEffect("封禁行动", is_debuff=True, source=source))

    def _refresh_debuff(
        self,
        state: _CombatState,
        name: str,
        source: _CombatState,
        *,
        heal_received_pct: int = 0,
        duration: int | None = None,
    ) -> None:
        state.statuses = [s for s in state.statuses if not (s.name == name and s.source is source)]
        self._add_status(state, _StatusEffect(name, heal_received_pct=heal_received_pct, duration=duration, is_debuff=True, source=source))

    def _add_wound(self, state: _CombatState, source: _CombatState, count: int, *, damage_taken_pct: int = 5, heal_down_pct: int = 8) -> int:
        added = 0
        for _ in range(max(0, count)):
            if self._status_count(state, "创伤") >= 5:
                break
            self._add_status(state, _StatusEffect("创伤", damage_taken_pct=damage_taken_pct, heal_received_pct=-heal_down_pct, is_debuff=True, source=source))
            added += 1
        return added

    def _add_pobu(self, state: _CombatState, source: _CombatState, count: int, *, agility_down_pct: int = 10) -> int:
        added = 0
        cap = self._status_stack_cap("破步") or 4
        for _ in range(max(0, count)):
            if self._status_count(state, "破步") >= cap:
                break
            self._add_status(state, _StatusEffect("破步", agility_pct=-agility_down_pct, is_debuff=True, source=source))
            added += 1
        return added

    def _trigger_wanzhou_burst(self, round_no: int, actor: _CombatState, target: _CombatState, roller: random.Random) -> list[ActionLog]:
        power = actor.snapshot.spirit_power
        if power is None:
            return []
        consumed = self._consume_all_curse_seals(target)
        if consumed <= 0:
            return []
        rolls_per = _roll(power.rolls, "debuff_rolls_per_curse", 2)
        table = (("灼烧", 28), ("蔓咒", 22), ("破步", 18), ("创伤", 18), ("咒缚", 14), ("雷殛", 10))
        total_weight = sum(weight for _name, weight in table)
        counts: dict[str, int] = {name: 0 for name, _weight in table}
        for _ in range(consumed * rolls_per):
            pick = roller.randint(1, total_weight) if hasattr(roller, "randint") else int(roller.random() * total_weight) + 1
            cursor = 0
            chosen = "灼烧"
            for name, weight in table:
                cursor += weight
                if pick <= cursor:
                    chosen = name
                    break
            counts[chosen] += 1
        logs: list[ActionLog] = [self._effect_log(round_no, target, f"{actor.snapshot.name} 引爆 {consumed} 层咒印，万咒如雨倾落。", actor_name=actor.snapshot.name)]
        if counts["灼烧"]:
            self._apply_burn_to_target(target, actor, stacks=counts["灼烧"], per_stack_pct=self._zhuohun_burn_pct(actor), round_no=round_no, logs=logs)
        # 蔓咒/破步/咒缚受 _STATUS_STACK_CAPS 上限约束，满层后不再附加
        added_manzhou = 0
        for _ in range(counts["蔓咒"]):
            probe = _StatusEffect("蔓咒", atk_pct=-8, is_debuff=True, source=actor)
            if not self._can_receive_status_stack(target, probe):
                break
            self._add_status(target, probe)
            added_manzhou += 1
        added_pobu = self._add_pobu(target, actor, counts["破步"])
        added_wound = self._add_wound(target, actor, counts["创伤"])
        added_zhoufu = 0
        for _ in range(counts["咒缚"]):
            probe = _StatusEffect("咒缚", damage_taken_pct=6, is_debuff=True, source=actor)
            if not self._can_receive_status_stack(target, probe):
                break
            self._add_status(target, probe)
            added_zhoufu += 1
        added_leihen = 0
        for _ in range(counts["雷殛"]):
            added_leihen += self._add_target_leihen(target, actor)
        logs.append(self._effect_log(round_no, target, f"万咒附加：灼烧 {counts['灼烧']}、蔓咒 {added_manzhou}、破步 {added_pobu}、创伤 {added_wound}、咒缚 {added_zhoufu}、雷殛 {added_leihen}。", actor_name=actor.snapshot.name))
        return logs

    def _spirit_ready(self, state: _CombatState, key: str, round_no: int) -> bool:
        return state.spirit_proc_rounds.get(key) != round_no

    def _mark_spirit_triggered(self, state: _CombatState, key: str, round_no: int) -> None:
        state.spirit_proc_rounds[key] = round_no

    def _active_statuses(self, state: _CombatState) -> list[_StatusEffect]:
        return [status for status in state.statuses if status.is_active()]

    def _add_status(self, state: _CombatState, status: _StatusEffect) -> None:
        if status.name == "灵势":
            room = self._status_stack_cap("灵势", state) - self._status_count(state, "灵势")
            if room <= 0:
                return
            status = replace(status, stacks=min(status.stacks, room))
        if self._can_merge_status(status):
            for existing in state.statuses:
                if self._same_stackable_status(existing, status):
                    existing.stacks += status.stacks
                    if not status.is_debuff:
                        self._trigger_on_gain_positive(state, status)
                    return
        state.statuses.append(status)
        if not status.is_debuff:
            self._trigger_on_gain_positive(state, status)

    @staticmethod
    def _can_merge_status(status: _StatusEffect) -> bool:
        return (
            status.is_active()
            and status.duration is None
            and status.remaining_hits is None
            and status.shield == 0
            and status.bonus_damage == 0
            and status.name != "裂铠"
        )

    @classmethod
    def _same_stackable_status(cls, existing: _StatusEffect, status: _StatusEffect) -> bool:
        if not cls._can_merge_status(existing):
            return False
        return existing.source is status.source and all(
            getattr(existing, field_name) == getattr(status, field_name)
            for field_name in _StatusEffect.__dataclass_fields__
            if field_name not in {"stacks", "source"}
        )

    def _dodge_rate(self, defender: _CombatState, attacker: _CombatState | None = None) -> float:
        attacker_agi = self._current_agility(attacker) if attacker is not None else 1
        return clamp(
            0.10 * (self._current_agility(defender) / max(attacker_agi, 1)) + self._dodge_bonus_pct(defender) / 100,
            0.05,
            0.80,
        )

    def _fengdun_try_evade(
        self,
        state: _CombatState,
        actor: _CombatState | None,
        round_no: int,
        logs: list[ActionLog] | None,
    ) -> bool:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "fengdun":
            return False
        roller = state.roller or self.rng
        if roller.random() >= self._dodge_rate(state, actor):
            self._reduce_wind_stacks(state, 1)
            return False
        followups = self._trigger_spirit_on_dodge(round_no, state)
        if logs is not None:
            stack_log = next((log for log in followups if log.text and "风遁叠至第" in log.text), None)
            if stack_log is not None and stack_log.text:
                suffix = stack_log.text.split("风遁叠至第", 1)[1]
                logs.append(
                    self._effect_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 以风遁闪开本次伤害，叠至第{suffix}",
                    )
                )
                followups = [log for log in followups if log is not stack_log]
            else:
                logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 以风遁闪开本次伤害。"))
            logs.extend(followups)
        return True

    def _xuanjia_blocks(
        self,
        state: _CombatState,
        round_no: int,
        logs: list[ActionLog] | None,
    ) -> bool:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "xuanjia":
            return False
        roller = state.roller or self.rng
        if roller.random() >= _roll(power.rolls, "proc_pct", 0) / 100:
            return False
        if logs is not None:
            logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 的玄甲骤然张开，完全格挡本次伤害。"))
        return True

    def _apply_damage(
        self,
        state: _CombatState,
        damage: int,
        *,
        respects_resilience: bool = True,
        actor: _CombatState | None = None,
        round_no: int = 0,
        logs: list[ActionLog] | None = None,
        scene: set[str] | None = None,
        can_be_shielded: bool = False,
        settle_liekai: bool = True,
        cause: ActionLog | None = None,
        can_fengdun_dodge: bool = True,
        can_xuanjia_block: bool = True,
    ) -> int:
        """最底层扣血。
        - respects_resilience=True（默认）：扣减 state.snapshot.base_resilience % 后再扣血。
          普攻、反棘、归锋、追击、灼烧 DOT、春生、蚀焰等所有伤害管线最终都汇聚到这里。
        - respects_resilience=False：豁免境界韧性。机制性必杀与灵涌真伤使用。
        - 绝命死兆伤害走普通伤害管线，由本函数处理。
        - can_fengdun_dodge：风遁持有者对此笔伤害独立闪避。普攻已在 _resolve_action 判过，传 False 避免重复判定。
        - can_xuanjia_block：灵涌命中后的附加真伤不再判定格挡。
        """
        if damage <= 0 or state.hp <= 0:
            return 0
        if can_fengdun_dodge and self._fengdun_try_evade(state, actor, round_no, logs):
            self._attach_or_log_damage(
                round_no, state, 0, logs, actor=actor, cause=cause, replace_cause=True, allow_zero=True
            )
            return 0
        if can_xuanjia_block and self._xuanjia_blocks(state, round_no, logs):
            self._attach_or_log_damage(
                round_no, state, 0, logs, actor=actor, cause=cause, replace_cause=True, allow_zero=True
            )
            return 0
        # 护盾先吸收原始伤害；只有穿透护盾的部分才吃境界韧性。
        remaining = damage
        if can_be_shielded:
            if settle_liekai:
                remaining = self._consume_shield_and_settle_liekai(state, actor, remaining, round_no, logs)
            else:
                remaining = self._consume_shield(state, remaining)
            if remaining <= 0:
                self._attach_or_log_damage(
                    round_no, state, 0, logs, actor=actor, cause=cause, replace_cause=True, allow_zero=True
                )
                return 0
        resilience = max(0, min(95, state.snapshot.base_resilience)) if respects_resilience else 0
        resilience_factor = 100 - resilience

        def resilient_damage(raw_damage: int) -> int:
            if raw_damage <= 0:
                return 0
            return max(1, raw_damage * resilience_factor // 100)

        raw_reaching_hp = 0
        actual_damage = 0
        active_scene = scene or set()
        pending_hp_log = 0
        cause_written = False
        for threshold in (50, 30, 25):
            if threshold in (50, 25):
                already_triggered = threshold in state.huichun_triggered_thresholds
            else:
                already_triggered = threshold in state.low_hp_marks
            if already_triggered:
                continue
            threshold_hp = state.get_max_hp() * threshold // 100
            if state.hp <= threshold_hp:
                if logs is not None:
                    logs.extend(self._trigger_low_hp_threshold(round_no, state, threshold, active_scene))
                else:
                    self._trigger_low_hp_threshold(round_no, state, threshold, active_scene)
                continue
            to_threshold = state.hp - threshold_hp
            target_resilient_damage = resilient_damage(raw_reaching_hp) + to_threshold
            raw_total_needed = (
                1
                if target_resilient_damage == 1
                else (target_resilient_damage * 100 + resilience_factor - 1) // resilience_factor
            )
            raw_to_threshold = raw_total_needed - raw_reaching_hp
            if remaining < raw_to_threshold:
                break
            state.hp = threshold_hp
            actual_damage += to_threshold
            raw_reaching_hp += raw_to_threshold
            remaining -= raw_to_threshold
            pending_hp_log += to_threshold
            interrupt = self._threshold_will_log(state, threshold, active_scene)
            if interrupt:
                self._attach_or_log_damage(
                    round_no, state, pending_hp_log, logs, actor=actor, cause=cause, replace_cause=not cause_written
                )
                pending_hp_log = 0
                cause_written = True
            probe = self._trigger_low_hp_threshold(round_no, state, threshold, active_scene)
            if probe and logs is not None:
                logs.extend(probe)
            if can_be_shielded and remaining > 0:
                if settle_liekai:
                    remaining = self._consume_shield_and_settle_liekai(state, actor, remaining, round_no, logs)
                else:
                    remaining = self._consume_shield(state, remaining)
        total_resilient = resilient_damage(raw_reaching_hp + remaining) - resilient_damage(raw_reaching_hp)
        final_segment = min(state.hp, total_resilient)
        state.hp -= final_segment
        actual_damage += final_segment
        pending_hp_log += final_segment
        if total_resilient > final_segment:
            # 击杀瞬间的溢出余伤暂存：涅槃复活时补上，避免超额伤害被复活无成本吞掉
            state.pending_overkill += total_resilient - final_segment
        self._attach_or_log_damage(
            round_no, state, pending_hp_log, logs, actor=actor, cause=cause, replace_cause=not cause_written
        )
        self._record_xuekuang_hp_loss(state, actual_damage)
        self._sync_xuekuang_max_hp(state)
        if actual_damage > 0 and actor is not None and actor is not state:
            power = actor.snapshot.spirit_power
            if power is not None and power.power_id == "shisheng":
                healed = self._heal_by_damage(actor, actual_damage, _roll(power.rolls, "heal_pct", 0), logs=logs)
                if healed > 0 and logs is not None:
                    logs.append(
                        self._hp_change_log(
                            round_no,
                            actor,
                            f"{actor.snapshot.name} 借噬生吞回血气，回复了 {format_big_number(healed)} 点生命，余血 {format_big_number(actor.hp)}。",
                        )
                    )
        return actual_damage

    def _apply_chenchen(self, state: _CombatState, damage: int) -> int:
        """承尘：单次受到的伤害若超过自身最大生命阈值，溢出部分按 reduction_pct 衰减。

        多件取最优：阈值取所有承尘词条中的 **最低** threshold_pct，衰减取 **最高** reduction_pct。
        命中阈值后衰减仅作用于"超出阈值的部分"，不影响阈值以内的基础伤害。
        """
        if damage <= 0 or state.hp <= 0:
            return damage
        affixes = [
            entry for entry in state.snapshot.affixes
            if entry.affix_id == "chenchen"
        ]
        if not affixes:
            return damage
        best_threshold = min(_roll(entry.rolls, "threshold_pct", 30) for entry in affixes)
        best_reduction = max(_roll(entry.rolls, "reduction_pct", 30) for entry in affixes)
        threshold_damage = max(1, state.get_max_hp() * best_threshold // 100)
        if damage <= threshold_damage:
            return damage
        overflow = damage - threshold_damage
        kept = max(0, overflow * max(0, 100 - best_reduction) // 100)
        return threshold_damage + kept

    def _apply_typed_damage(
        self,
        state: _CombatState,
        raw_damage: int,
        profile: _DamageProfile,
        actor: "_CombatState | None" = None,
        round_no: int = 0,
        logs: list[ActionLog] | None = None,
        scene: set[str] | None = None,
        settle_liekai: bool = True,
        cause: ActionLog | None = None,
    ) -> int:
        """按 profile 对 state 施加伤害。可选地按 actor 走增伤、按 state 走承伤/减伤/护盾。

        - can_be_buffed: 吃 actor 的 damage_dealt_pct + damage_dealt_basis_points
        - can_be_vulned: 吃 state 的 damage_taken_pct + damage_taken_basis_points
        - can_be_reduced: 吃 state 的 damage_reduction_pct + damage_reduction_basis_points（双重 max(0.1, ...) 兜底合并为单次）
        - can_be_shielded: 走 _consume_shield_and_settle_liekai 抵挡（含裂铠反噬）

        Note: actor.hp <= 0 时（DOT 来源已死亡），buff 通道自动失效，退化为裸基础伤害。
        """
        if raw_damage <= 0 or state.hp <= 0:
            return 0
        damage = max(1, int(raw_damage))
        # 增伤通道（actor 死亡时退化为裸打）
        if profile.can_be_buffed and actor is not None and actor.hp > 0:
            buff_pct = self._damage_dealt_pct(actor)
            basis = actor.snapshot.damage_dealt_basis_points
            damage = int(damage * (1 + buff_pct / 100))
            damage = int(damage * (1 + basis / 10_000))
            damage = max(1, damage)
        # 承伤通道
        if profile.can_be_vulned:
            vuln_pct = self._damage_taken_pct(state)
            vuln_basis = state.snapshot.damage_taken_basis_points
            damage = int(damage * (1 + vuln_pct / 100))
            damage = int(damage * (1 + vuln_basis / 10_000))
            damage = max(1, damage)
        # 减伤通道（合并双重 max(0.05, ...) 为单次 max(0.1, ...)）
        if profile.can_be_reduced:
            reduce_pct = self._damage_reduction_pct(state)
            reduce_basis = state.snapshot.damage_reduction_basis_points
            combined = 1 - (reduce_pct / 100) - (reduce_basis / 10_000)
            damage = max(1, int(damage * max(0.1, combined)))
        # 承尘：大额单击溢出减幅（仅对走减伤通道的伤害类型生效，避免叠加于真伤/雷劫）
        if profile.can_be_reduced:
            damage = self._apply_chenchen(state, damage)
        # 护盾抵挡在底层分段扣血时统一处理，使跨 30% 新生成的裂铠能吸收同一笔剩余伤害。
        return self._apply_damage(
            state,
            damage,
            respects_resilience=profile.respects_resilience,
            actor=actor,
            round_no=round_no,
            logs=logs,
            scene=scene or set(),
            can_be_shielded=profile.can_be_shielded,
            settle_liekai=settle_liekai,
            cause=cause,
        )

    def _modify_max_hp(self, state: _CombatState, delta: int, also_heal: bool = True) -> int:
        """运行期改变 effective_max_hp，可选地按 delta 同步治疗（不走 heal_received 加成、不触发 heal_followups）。

        - delta > 0: 提升上限并按等量补血（用于归元类词条）
        - delta < 0: 降低上限并截断当前 hp
        返回实际变更量。
        """
        if delta == 0:
            return 0
        before_max = state.get_max_hp()
        new_max = max(1, before_max + delta)
        state.effective_max_hp = new_max
        if delta > 0 and also_heal:
            state.hp = min(new_max, state.hp + delta)
        elif delta < 0:
            state.hp = min(state.hp, new_max)
        return new_max - before_max

    def _heal(self, state: _CombatState, heal_pct: int, *, logs: list[ActionLog] | None = None) -> int:
        if state.hp <= 0:
            return 0
        heal_pct = max(1, int(heal_pct * max(0.1, 1 + self._heal_received_pct(state) / 100)))
        max_hp = state.get_max_hp()
        amount = max(1, int(max_hp * heal_pct / 100))
        before = state.hp
        state.hp = min(max_hp, state.hp + amount)
        healed = state.hp - before
        self._trigger_heal_followups(state, healed, logs=logs)
        return healed

    def _heal_by_damage(self, state: _CombatState, damage: int, heal_pct: int, *, logs: list[ActionLog] | None = None) -> int:
        if state.hp <= 0 or damage <= 0 or heal_pct <= 0:
            return 0
        amount = max(1, damage * heal_pct // 100)
        amount = max(1, int(amount * max(0.1, 1 + self._heal_received_pct(state) / 100)))
        max_hp = state.get_max_hp()
        before = state.hp
        state.hp = min(max_hp, state.hp + amount)
        healed = state.hp - before
        self._trigger_heal_followups(state, healed, logs=logs)
        return healed

    def _trigger_heal_followups(self, state: _CombatState, healed: int, *, logs: list[ActionLog] | None = None) -> None:
        if healed <= 0:
            return
        power = state.snapshot.spirit_power
        # 春生：固定追打
        if power is not None and power.power_id == "chunsheng":
            convert_pct = power.rolls.get("convert_pct", 0)
            if convert_pct > 0:
                bonus = max(1, int(healed * convert_pct / 100))
                # 用 _StatusEffect.bonus_damage 携带固定追打值（下次攻击命中后以 _CHUNSHENG_BONUS_PROFILE 结算）
                self._add_status(state, _StatusEffect("春生·追击", bonus_damage=bonus, remaining_hits=1))
        # 涅槃：受到治疗时额外叠生息（不计入护元上限）
        if power is not None and power.power_id == "niepan":
            shengxi_bonus = power.rolls.get("heal_shengxi_bonus", 0)
            for _ in range(shengxi_bonus):
                self._add_status(state, _StatusEffect("生息"))
            if shengxi_bonus > 0 and logs is not None:
                logs.append(
                    self._effect_log(
                        state.current_round,
                        state,
                        f"{state.snapshot.name} 涅槃蕴息，叠加 {shengxi_bonus} 层生息（现有 {self._status_count(state, '生息')} 层）。",
                    )
                )
        # 护元（huyuan）：自身受到治疗时按 affix index 独立叠层（不超过 per_battle_cap）
        for index, entry in enumerate(state.snapshot.affixes):
            if entry.affix_id != "huyuan":
                continue
            cap = _roll(entry.rolls, "per_battle_cap", 5)
            current = state.huyuan_heal_stacks.get(index, 0)
            if current < cap:
                state.huyuan_heal_stacks[index] = current + 1
                self._add_status(state, _StatusEffect("生息"))
                if logs is not None:
                    logs.append(
                        self._effect_log(
                            state.current_round,
                            state,
                            f"{state.snapshot.name} 护元纳息，叠加 1 层生息（现有 {self._status_count(state, '生息')} 层）。",
                        )
                    )

    def _trigger_cleanse_followups(self, round_no: int, state: _CombatState, cleansed_layers: int, opponent: _CombatState) -> list[ActionLog]:
        """Called when effects are cleansed. Handles 转机 affix."""
        logs: list[ActionLog] = []
        if cleansed_layers <= 0:
            return logs
        # 转机追伤（单次净化最多计算 max_layers 层）
        for owner in (state, opponent):
            if owner.hp <= 0:
                continue
            for entry in owner.snapshot.affixes:
                if entry.affix_id != "zhuanji":
                    continue
                damage_pct = _roll(entry.rolls, "damage_pct", 0)
                max_layers = _roll(entry.rolls, "max_layers", 4)
                effective = min(cleansed_layers, max_layers)
                enemy = opponent if owner is state else state
                if enemy.hp <= 0:
                    continue
                damage = max(1, self._current_atk(owner) * damage_pct * effective // 100)
                nested: list[ActionLog] = []
                cause = self._effect_log(
                    round_no,
                    enemy,
                    f"{owner.snapshot.name} 的转机发动，追打 {enemy.snapshot.name}。",
                    actor_name=owner.snapshot.name,
                )
                nested.append(cause)
                actual = self._apply_typed_damage(
                    enemy,
                    damage,
                    _NORMAL_DAMAGE_PROFILE,
                    actor=owner,
                    round_no=round_no,
                    logs=nested,
                    cause=cause,
                )
                if actual > 0 or any(log is not cause for log in nested):
                    logs.extend(nested)
        return logs

    def _consume_hit_reduction_statuses(self, state: _CombatState) -> None:
        for status in state.statuses:
            if status.is_active() and status.damage_reduction_pct and status.remaining_hits is not None:
                status.remaining_hits -= 1
        state.statuses = self._active_statuses(state)

    def _consume_fengxing_stacks(self, round_no: int, actor: _CombatState, scene: set[str]) -> list[ActionLog]:
        """攻击后消耗全部风行层数，按每层 heal_pct% 回复生命。"""
        logs: list[ActionLog] = []
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            if entry.affix_id != "fengxing":
                continue
            stacks = self._status_count(actor, "风行")
            if stacks <= 0:
                continue
            heal_pct = _roll(entry.rolls, "heal_pct", 0)
            if heal_pct > 0:
                total_heal = heal_pct * stacks
                healed = self._heal(actor, total_heal, logs=logs)
                logs.append(
                    self._hp_change_log(
                        round_no,
                        actor,
                        f"{actor.snapshot.name} 风行化劲，消耗 {stacks} 层风行回复 {format_big_number(healed)} 点生命，余血 {format_big_number(actor.hp)}。",
                    )
                )
            # 移除全部风行层数
            actor.statuses = [s for s in actor.statuses if s.name != "风行"]
        return logs

    def _consume_attack_bonuses(
        self,
        state: _CombatState,
        statuses: list[_StatusEffect] | None = None,
    ) -> None:
        for status in statuses if statuses is not None else state.statuses:
            if status in state.statuses and status.is_active() and status.remaining_hits is not None and (
                status.damage_dealt_pct
                or status.crit_bonus_pct
                or status.crit_damage_pct
                or status.guarantee_crit
                or status.dodge_bonus_pct
            ):
                status.remaining_hits -= 1
        state.statuses = self._active_statuses(state)

    def _decay_statuses(self, state: _CombatState) -> None:
        for status in state.statuses:
            if status.duration is not None and status.duration > 0:
                status.duration -= 1
        state.statuses = self._active_statuses(state)

    def _scene_matches(self, entry: ArtifactAffixEntry, scene: set[str]) -> bool:
        return get_artifact_affix_definition(entry.affix_id).matches_scene(scene)

    # ── 随机 buff/debuff 工厂 ──────────────────────────────────────

    def _create_debuff_by_name(self, name: str, source: _CombatState) -> _StatusEffect:
        match name:
            case "蔓咒":
                return _StatusEffect("蔓咒", atk_pct=-10, is_debuff=True, source=source)
            case "破步":
                return _StatusEffect("破步", agility_pct=-10, is_debuff=True, source=source)
            case "创伤":
                return _StatusEffect("创伤", damage_taken_pct=5, heal_received_pct=-8, is_debuff=True, source=source)
            case "灼烧":
                return _StatusEffect("灼烧", burn_pct=25, is_debuff=True, source=source)
            case _:
                return _StatusEffect("蔓咒", atk_pct=-10, is_debuff=True, source=source)

    def _create_buff_by_name(self, name: str) -> _StatusEffect:
        match name:
            case "增伤":
                return _StatusEffect("增伤", damage_dealt_pct=10)
            case "减伤":
                return _StatusEffect("减伤", damage_reduction_pct=10)
            case "身法":
                return _StatusEffect("身法", agility_pct=10)
            case "杀伐":
                return _StatusEffect("杀伐", atk_pct=10)
            case _:
                return _StatusEffect("增伤", damage_dealt_pct=10)

    # ── 涤世辅助方法 ───────────────────────────────────────────────

    def _total_effect_stacks(self, state: _CombatState) -> int:
        """Count active cleanseable effect stacks on a combatant."""
        return sum(s.stacks for s in self._active_statuses(state) if s.cleanseable)

    def _unique_effect_names(self, state: _CombatState) -> set[str]:
        return {s.name for s in self._active_statuses(state) if s.cleanseable}

    def _remove_all_status_effects(self, state: _CombatState) -> int:
        """Remove all cleanseable StatusEffects from a combatant. Returns count of removed stacks."""
        count = sum(s.stacks for s in self._active_statuses(state) if s.cleanseable)
        state.statuses = [s for s in state.statuses if not s.cleanseable]
        return count

    # ── 新词条触发钩子 ──────────────────────────────────────────────

    def _trigger_on_gain_positive(self, state: _CombatState, effect: _StatusEffect) -> None:
        """Called when state gains a positive (non-debuff) effect. Handles 贪噬 affix."""
        for entry in state.snapshot.affixes:
            if entry.affix_id != "tanshi":
                continue
            # 贪噬 buff 自身不会触发贪噬（防止无限循环）
            if effect.name == "贪噬":
                continue
            max_stacks = _roll(entry.rolls, "max_stacks", 5)
            if self._status_count(state, "贪噬") >= max_stacks:
                continue
            per_stack = _roll(entry.rolls, "per_stack_pct", 3)
            self._add_status(state, _StatusEffect("贪噬", damage_dealt_pct=per_stack))

    def _trigger_on_effect_lost_to_enemy(self, round_no: int, state: _CombatState, opponent: _CombatState, layers_lost: int) -> list[ActionLog]:
        """Trigger 反噬 when opponent loses positive effects."""
        logs: list[ActionLog] = []
        if layers_lost <= 0 or opponent.hp <= 0:
            return logs
        for entry in state.snapshot.affixes:
            if entry.affix_id != "fanshi":
                continue
            damage_pct = _roll(entry.rolls, "damage_pct", 0)
            damage = max(1, self._current_atk(state) * damage_pct * layers_lost // 100)
            nested: list[ActionLog] = []
            cause = self._effect_log(
                round_no,
                opponent,
                f"{state.snapshot.name} 的反噬发动，{opponent.snapshot.name} 失去 {layers_lost} 层正面效果。",
                actor_name=state.snapshot.name,
            )
            nested.append(cause)
            actual = self._apply_typed_damage(
                opponent,
                damage,
                _NORMAL_DAMAGE_PROFILE,
                actor=state,
                round_no=round_no,
                logs=nested,
                cause=cause,
            )
            if actual > 0 or any(log is not cause for log in nested):
                logs.extend(nested)
        return logs

    # ── 器灵神通 round_start / round_end 钩子 ────────────────────────

    def _is_pending_strike_status(self, status: _StatusEffect) -> bool:
        return status.bonus_damage > 0 or status.name in self._PENDING_STRIKE_STATUS_NAMES

    def _status_stack_cap(self, name: str, state: _CombatState | None = None) -> int | None:
        if name == "灵势" and state is not None:
            power = state.snapshot.spirit_power
            if power is not None and power.power_id == "lingyong":
                return LINGYONG_STACK_CAP
        return self._STATUS_STACK_CAPS.get(name)

    def _can_receive_status_stack(self, state: _CombatState, status: _StatusEffect) -> bool:
        cap = self._status_stack_cap(status.name, state)
        if cap is None:
            return True
        return self._status_count(state, status.name) < cap

    def _copy_status_layer(self, status: _StatusEffect, *, source: _CombatState | None) -> _StatusEffect:
        return replace(status, stacks=1, source=source)

    def _peel_one_stack(self, state: _CombatState, status: _StatusEffect) -> _StatusEffect | None:
        if status not in state.statuses or not status.is_active():
            return None
        peeled = self._copy_status_layer(status, source=status.source)
        if status.stacks > 1:
            status.stacks -= 1
        else:
            state.statuses.remove(status)
        return peeled

    def _trigger_xuekuang_round_start(self, round_no: int, state: _CombatState, scene: set[str] | None = None) -> list[ActionLog]:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "xuekuang" or state.hp <= 0:
            return []
        logs: list[ActionLog] = []
        max_hp = state.get_max_hp()
        burn_pct = max(1, _roll(power.rolls, "burn_pct", 5))
        cost = max(1, max_hp * burn_pct // 100)
        before = state.hp
        actual = min(state.hp, cost)
        state.hp -= actual
        self._record_xuekuang_hp_loss(state, actual)
        if actual > 0:
            active_scene = scene or set()
            for threshold in (50, 30, 25):
                threshold_hp = max_hp * threshold // 100
                if before > threshold_hp and state.hp <= threshold_hp:
                    logs.extend(self._trigger_low_hp_threshold(round_no, state, threshold, active_scene))
        self._sync_xuekuang_max_hp(state)
        bonus = self._xuekuang_stat_pct(state)
        text = f"{state.snapshot.name} 血狂燃精，失去 {format_big_number(actual)} 点生命，余血 {format_big_number(state.hp)}。"
        if bonus > 0:
            text = text[:-1] + f"，三维提高 {bonus}%。"
        logs.append(self._hp_change_log(round_no, state, text, damage=actual))
        return logs

    def _trigger_spirit_round_start(self, round_no: int, state: _CombatState, opponent: _CombatState, roller: random.Random, scene: set[str] | None = None) -> list[ActionLog]:
        """Handle spirit powers that trigger at round start."""
        power = state.snapshot.spirit_power
        if power is None:
            return []
        if power.power_id == "xuekuang":
            return self._trigger_xuekuang_round_start(round_no, state, scene)
        if power.power_id == "lingyong":
            return self._trigger_lingyong_cleanse(round_no, state, opponent, roller)
        if power.power_id != "qiedao":
            return []
        chain_pct = power.rolls.get("chain_pct", 5)
        logs: list[ActionLog] = []
        while True:
            enemy_positives = [
                status
                for status in self._active_statuses(opponent)
                if not status.is_debuff
                and status.shield <= 0
                and status.cleanseable
                and not self._is_pending_strike_status(status)
                and self._can_receive_status_stack(state, status)
            ]
            if enemy_positives:
                stolen = roller.choice(enemy_positives)
                peeled = self._peel_one_stack(opponent, stolen)
                if peeled is None:
                    break
                self._add_status(state, self._copy_status_layer(peeled, source=state))
                logs.append(
                    self._effect_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 的窃道从 {opponent.snapshot.name} 窃取 1 层「{peeled.name}」。",
                        actor_name=state.snapshot.name,
                    )
                )
                logs.extend(self._trigger_on_effect_lost_to_enemy(round_no, state, opponent, 1))
                if roller.randint(0, 99) >= chain_pct:
                    break
                continue
            self_negatives = [
                status
                for status in self._active_statuses(state)
                if status.is_debuff
                and status.cleanseable
                and self._can_receive_status_stack(opponent, status)
            ]
            if self_negatives:
                transferred = roller.choice(self_negatives)
                peeled = self._peel_one_stack(state, transferred)
                if peeled is None:
                    break
                self._add_status(opponent, self._copy_status_layer(peeled, source=state))
                logs.append(
                    self._effect_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 的窃道将 1 层「{peeled.name}」转移至 {opponent.snapshot.name}。",
                        actor_name=state.snapshot.name,
                    )
                )
                if peeled.name == "灼烧":
                    logs.extend(
                        self._maybe_trigger_burn_exhausted(
                            peeled.source or opponent,
                            state,
                            had_burn=True,
                            round_no=round_no,
                            roller=roller,
                        )
                    )
                if roller.randint(0, 99) >= chain_pct:
                    break
                continue
            break
        return logs

    def _trigger_lingyong_cleanse(
        self,
        round_no: int,
        state: _CombatState,
        opponent: _CombatState,
        roller: random.Random,
    ) -> list[ActionLog]:
        if state.hp <= 0 or not self._spirit_ready(state, "lingyong_cleanse", round_no):
            return []
        self._mark_spirit_triggered(state, "lingyong_cleanse", round_no)
        layers = min(LINGYONG_STACK_CAP, self._status_count(state, "灵势"))
        had_burn = self._has_burn(state)
        burn_source = self._burn_source(state)
        removed = 0
        for _ in range(layers // LINGYONG_CLEANSE_STACKS_PER_DEBUFF):
            if self._remove_one_debuff(state) is None:
                break
            removed += 1
        if removed == 0:
            return []
        logs = [
            self._effect_log(
                round_no,
                state,
                f"{state.snapshot.name} 灵涌涤身，以 {layers} 层灵势净化 {removed} 层负面效果。",
            )
        ]
        logs.extend(self._trigger_cleanse_followups(round_no, state, removed, opponent))
        logs.extend(
            self._maybe_trigger_burn_exhausted(
                burn_source or opponent, state, had_burn=had_burn, round_no=round_no, roller=roller
            )
        )
        return logs

    def _trigger_lingyong_strike(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        scene: set[str],
    ) -> list[ActionLog]:
        power = actor.snapshot.spirit_power
        if power is None or power.power_id != "lingyong" or actor.hp <= 0 or target.hp <= 0:
            return []
        layers = min(LINGYONG_STACK_CAP, self._status_count(actor, "灵势"))
        excess = layers - LINGYONG_NORMAL_STACK_CAP
        if excess <= 0:
            return []
        hp_pct = excess * LINGYONG_TRUE_DAMAGE_PER_STACK_PCT
        damage = min(
            target.get_max_hp() * hp_pct // 100,
            actor.snapshot.atk * LINGYONG_TRUE_DAMAGE_ATK_CAP_PCT // 100,
        )
        cause = self._effect_log(
            round_no,
            target,
            f"{actor.snapshot.name} 灵涌贯体，{layers} 层灵势迸发 {hp_pct}% 最大生命真伤。",
            actor_name=actor.snapshot.name,
        )
        logs = [cause]
        # 只由主动普攻命中调用：不重复闪避、不计入反棘基数，护盾和低血线效果仍正常结算。
        self._apply_damage(
            target,
            max(1, damage),
            respects_resilience=False,
            actor=actor,
            round_no=round_no,
            logs=logs,
            scene=scene,
            can_be_shielded=True,
            cause=cause,
            can_fengdun_dodge=False,
            can_xuanjia_block=False,
        )
        return logs

    def _trigger_spirit_round_end(self, round_no: int, state: _CombatState, opponent: _CombatState, roller: random.Random) -> list[ActionLog]:
        """Handle spirit powers that trigger at round end (涤世)."""
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "dishi" or state.hp <= 0:
            return []
        # 1 回合冷却：触发后需跳过 1 回合才能再次触发
        if state.dishi_last_round > 0 and round_no <= state.dishi_last_round + 1:
            return []
        total_stacks = self._total_effect_stacks(state) + self._total_effect_stacks(opponent)
        threshold = power.rolls.get("threshold", 0)
        if total_stacks < threshold:
            return []
        unique_names = self._unique_effect_names(state) | self._unique_effect_names(opponent)
        kind_count = len(unique_names)
        # 反噬：涤世清除前记录敌方正面效果层数，清除后触发反噬回调
        opponent_positive_count_before = self._positive_status_count(opponent)
        had_burn_state = self._has_burn(state)
        had_burn_opponent = self._has_burn(opponent)
        burn_source_state = self._burn_source(state)
        burn_source_opponent = self._burn_source(opponent)
        total_removed = self._remove_all_status_effects(state) + self._remove_all_status_effects(opponent)
        stack_pct = power.rolls.get("stack_pct", 0)
        # 涤世削弱（2026-05-27）：去掉 kind_pct × kind_count 部分，仅按 stack_pct × total_removed 计算
        bonus = max(1, self._current_atk(state) * stack_pct * total_removed // 100)
        self._add_status(state, _StatusEffect("涤世·净化", bonus_damage=bonus, remaining_hits=1))
        logs: list[ActionLog] = []
        if kind_count > 0:
            logs.append(
                self._effect_log(
                    round_no,
                    state,
                    f"{state.snapshot.name} 的涤世发动！清除场上 {kind_count} 种共 {total_removed} 层效果，净化之力凝聚待发。",
                    actor_name=state.snapshot.name,
                )
            )
        # 涤世净化后触发转机（双方各走一遍）
        logs.extend(self._trigger_cleanse_followups(round_no, state, total_removed, opponent))
        logs.extend(self._maybe_trigger_burn_exhausted(burn_source_state or opponent, state, had_burn=had_burn_state, round_no=round_no, roller=roller))
        logs.extend(self._maybe_trigger_burn_exhausted(burn_source_opponent or state, opponent, had_burn=had_burn_opponent, round_no=round_no, roller=roller))
        # 反噬：敌方正面效果被涤世清除后触发
        if opponent_positive_count_before > 0 and opponent.hp > 0:
            logs.extend(self._trigger_on_effect_lost_to_enemy(round_no, state, opponent, opponent_positive_count_before))
        # 记录冷却
        state.dishi_last_round = round_no
        return logs

    def _clear_death_omens(self, state: _CombatState) -> None:
        state.statuses = [status for status in state.statuses if status.name != "死兆"]

    def _settle_jueming_marks(
        self,
        round_no: int,
        state: _CombatState,
        opponent: _CombatState,
        roller: random.Random | None = None,
    ) -> list[ActionLog]:
        """消耗咒印凝成死兆，立刻按当前层数打自身最大生命百分比伤害；满 3 层后清空重叠。"""
        logs: list[ActionLog] = []
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "jueming" or state.hp <= 0 or opponent.hp <= 0:
            return logs
        owner = state.snapshot.name
        omen_cost = _roll(power.rolls, "omen_cost", 8)
        heal_down_pct = _roll(power.rolls, "heal_down_pct", 40)
        hp_pct = _roll(power.rolls, "hp_pct", _roll(power.rolls, "execute_pct", 18))
        if omen_cost <= 0 or self._curse_seal_count(opponent) < omen_cost:
            return logs
        self._consume_curse_seal(opponent, omen_cost)
        self._add_death_omen(opponent, state, heal_down_pct)
        omen_count = self._death_omen_count(opponent)
        if omen_count <= 0:
            return logs
        raw_damage = max(1, state.get_max_hp() * hp_pct * omen_count // 100)
        cause = self._effect_log(
            round_no,
            opponent,
            f"{owner} 炼化 {omen_cost} 层咒印，{opponent.snapshot.name} 凝成第 {omen_count} 层死兆。",
            actor_name=owner,
        )
        logs.append(cause)
        nested: list[ActionLog] = []
        actual = self._apply_typed_damage(
            opponent,
            raw_damage,
            _NORMAL_DAMAGE_PROFILE,
            actor=state,
            round_no=round_no,
            logs=nested,
            cause=cause,
        )
        if actual > 0 or any(log is not cause for log in nested):
            logs.extend(log for log in nested if log is not cause)
        if omen_count >= 3:
            self._clear_death_omens(opponent)
            logs.append(
                self._effect_log(
                    round_no,
                    opponent,
                    f"{opponent.snapshot.name} 三重死兆散尽，重新叠层。",
                    actor_name=owner,
                )
            )
        return logs

    def _effect_log(
        self,
        round_no: int,
        target: _CombatState,
        text: str,
        *,
        actor_name: str | None = None,
        damage: int = 0,
    ) -> ActionLog:
        return ActionLog(
            round_no=round_no,
            actor_name=actor_name or target.snapshot.name,
            target_name=target.snapshot.name,
            dodged=False,
            critical=False,
            damage=damage,
            target_hp_after=target.hp,
            text=text,
        )

    def _hp_change_log(
        self,
        round_no: int,
        target: _CombatState,
        text: str,
        *,
        actor_name: str | None = None,
        damage: int = 0,
    ) -> ActionLog:
        return self._effect_log(round_no, target, text, actor_name=actor_name, damage=damage)

    def _strip_hp_suffix(self, text: str) -> str:
        for marker in ("，造成 ", "，护盾剩余 "):
            idx = text.rfind(marker)
            if idx >= 0:
                text = text[:idx]
        return text if text.endswith("。") else text + "。"

    def _append_hp_suffix(self, text: str, amount: int, hp_after: int, shield_after: int | None = None) -> str:
        base = text[:-1] if text.endswith("。") else text
        line = f"{base}，造成 {format_big_number(amount)} 点伤害，余血 {format_big_number(hp_after)}"
        if shield_after:
            line += f"，护盾剩余 {format_big_number(shield_after)}"
        return line + "。"

    def _attach_or_log_damage(
        self,
        round_no: int,
        target: _CombatState,
        amount: int,
        logs: list[ActionLog] | None,
        *,
        actor: _CombatState | None = None,
        cause: ActionLog | None = None,
        replace_cause: bool = True,
        allow_zero: bool = False,
    ) -> None:
        if logs is None or amount < 0 or (amount == 0 and not allow_zero):
            return
        shield_after = self._total_shield(target)
        if cause is not None:
            if replace_cause:
                cause.damage = amount
                cause.target_hp_after = target.hp
                cause.shield_after = shield_after
                if cause.text:
                    cause.text = self._append_hp_suffix(
                        self._strip_hp_suffix(cause.text), amount, target.hp, shield_after
                    )
                return
            cloned = ActionLog(
                round_no=cause.round_no,
                actor_name=cause.actor_name,
                target_name=cause.target_name,
                dodged=False,
                critical=cause.critical,
                damage=amount,
                target_hp_after=target.hp,
                text=self._append_hp_suffix(
                    f"{cause.actor_name} 的余势未尽。",
                    amount,
                    target.hp,
                    shield_after,
                ),
                shield_after=shield_after,
            )
            logs.append(cloned)
            return
        logs.append(
            self._hp_change_log(
                round_no,
                target,
                self._append_hp_suffix(
                    f"{target.snapshot.name} 受到伤害。",
                    amount,
                    target.hp,
                    shield_after,
                ),
                actor_name=actor.snapshot.name if actor is not None else None,
                damage=amount,
            )
        )
        logs[-1].shield_after = shield_after
