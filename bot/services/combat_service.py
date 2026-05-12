from __future__ import annotations

from dataclasses import dataclass, field
import random

from bot.data.artifact_affixes import ArtifactAffixEntry, get_artifact_affix_definition
from bot.data.spirits import SpiritPowerEntry
from bot.utils.formatters import clamp


def _roll(rolls: dict[str, int], key: str, default: int = 0) -> int:
    return rolls.get(key, default)


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
    base_resilience: int = 0  # 境界基础韧性 %（0-36），所有伤害均扣，仅"机制性必杀真伤"豁免


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
    duration: int | None = None
    atk_pct: int = 0
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

    def is_active(self) -> bool:
        duration_ok = self.duration is None or self.duration > 0
        hits_ok = self.remaining_hits is None or self.remaining_hits > 0
        return duration_ok and hits_ok


@dataclass(slots=True, frozen=True)
class _DamageProfile:
    """伤害类型描述：决定该笔伤害走哪些计算管线。"""

    can_be_buffed: bool = True   # 是否吃 actor 的增伤（damage_dealt_pct + damage_dealt_basis_points）
    can_be_vulned: bool = True   # 是否吃 target 的承伤（damage_taken_pct + damage_taken_basis_points）
    can_be_reduced: bool = True  # 是否吃 target 的减伤（damage_reduction_pct + damage_reduction_basis_points）
    can_be_shielded: bool = True # 是否被护盾抵挡
    respects_resilience: bool = True  # 是否吃目标的境界基础韧性（仅"机制性必杀类真伤"豁免）


# 灼烧 DOT：吃增伤 + 承伤；不吃减伤、不被护盾抵挡（DOT 穿透守势/护盾）
_BURN_DOT_PROFILE = _DamageProfile(can_be_buffed=True, can_be_vulned=True, can_be_reduced=False, can_be_shielded=False)
# 蚀焰引爆：不吃增伤；吃承伤 + 减伤 + 护盾
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
    block_used_round: int = 0
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

    def get_max_hp(self) -> int:
        """获取当前最大生命（优先使用 effective_max_hp，未初始化时回落 snapshot）。"""
        return self.effective_max_hp if self.effective_max_hp > 0 else self.snapshot.max_hp


class CombatService:
    max_rounds = 20

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
        defender_state = _CombatState(defender, defender.max_hp)
        defender_state.effective_max_hp = defender.max_hp
        logs: list[ActionLog] = []

        logs.extend(self._trigger_battle_start(1, challenger_state, scene))
        logs.extend(self._trigger_battle_start(1, defender_state, scene))
        first, second = self._determine_order(challenger_state, defender_state, roller)

        for round_no in range(1, self.max_rounds + 1):
            challenger_state.current_round = round_no
            defender_state.current_round = round_no
            logs.extend(self._trigger_round_start(round_no, first, second, roller, scene))
            logs.extend(self._trigger_round_start(round_no, second, first, roller, scene))

            for actor, target in ((first, second), (second, first)):
                if actor.hp <= 0 or target.hp <= 0:
                    continue
                before_action_logs, can_act = self._trigger_before_action(round_no, actor)
                logs.extend(before_action_logs)
                if not can_act:
                    continue
                logs.extend(self._resolve_action(round_no, actor, target, roller, scene))
                if challenger_state.hp <= 0 or defender_state.hp <= 0:
                    return self._build_result(challenger_state, defender_state, round_no, False, logs)

            logs.extend(self._trigger_round_end(round_no, challenger_state, defender_state, roller))
            self._decay_statuses(challenger_state)
            self._decay_statuses(defender_state)
            if challenger_state.hp <= 0 or defender_state.hp <= 0:
                return self._build_result(challenger_state, defender_state, round_no, False, logs)

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
            challenger.snapshot.max_hp,
            defender.snapshot.max_hp,
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
        dodge_rate = clamp(0.10 * (self._current_agility(target) / max(self._current_agility(actor), 1)) + self._dodge_bonus_pct(target) / 100, 0.05, 0.75)
        if roller.random() < dodge_rate:
            logs.append(ActionLog(round_no, actor.snapshot.name, target.snapshot.name, True, False, 0, target.hp))
            logs.extend(self._trigger_on_dodge(round_no, target, scene))
            self._consume_attack_bonuses(actor)
            # 风遁：闪避时叠层（由神通处理）
            logs.extend(self._trigger_spirit_on_dodge(round_no, target))
            return logs

        # 被命中时只消散 1 层风遁（保留叠层流派的可玩性）
        self._reduce_wind_stacks(target, 1)

        damage = self._current_atk(actor)
        crit_rate = clamp(0.20 * (self._current_agility(actor) / max(self._current_agility(target), 1)) + self._crit_bonus_pct(actor) / 100, 0.10, 0.90)
        if self._has_guarantee_crit(actor):
            critical = True
            self._consume_guarantee_crit(actor)
        else:
            critical = roller.random() < crit_rate
        if critical:
            crit_multiplier = 1.5 + 0.5 * damage / max(damage + target.snapshot.defense, 1) + self._crit_damage_bonus_pct(actor) / 100
            damage = int(damage * crit_multiplier)

        before_attack_bonus = self._before_attack_bonus_pct(actor, target, scene)
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

        damage, pre_hit_logs = self._trigger_spirit_pre_hit(round_no, target, damage, roller)
        logs.extend(pre_hit_logs)
        had_damage_reduction = self._has_damage_reduction_status(target)
        if self._total_shield(target) > 0:
            damage = self._consume_shield(target, damage)
        actual_damage = self._apply_damage(target, damage)
        target.hits_taken += 1
        # 雷殛标记：受击时每层雷殛额外承受一份轻量真伤（吃韧性），来源神通取自标记 source
        if target.hp > 0:
            leihen_statuses = [s for s in self._active_statuses(target) if s.name == "雷殛"]
            for status in leihen_statuses:
                source = status.source
                if source is None or source.snapshot.spirit_power is None:
                    continue
                if source.snapshot.spirit_power.power_id != "leifa":
                    continue
                mark_pct = source.snapshot.spirit_power.rolls.get("mark_damage_pct", 0)
                if mark_pct <= 0:
                    continue
                base_atk = self._current_atk(actor)
                mark_raw = max(1, base_atk * mark_pct // 100)
                mark_actual = self._apply_damage(target, mark_raw)
                if mark_actual > 0:
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{source.snapshot.name} 的雷殛在 {target.snapshot.name} 身上炸开，追加 {mark_actual} 点雷殛真伤。",
                            actor_name=source.snapshot.name,
                        )
                    )
                if target.hp <= 0:
                    break
        low_hp_after_hit = target.hp
        self._consume_hit_reduction_statuses(target)

        logs.append(ActionLog(round_no, actor.snapshot.name, target.snapshot.name, False, critical, actual_damage, target.hp))
        logs.extend(self._trigger_on_hit(round_no, actor, target, actual_damage, roller, scene))
        if critical:
            actor.consecutive_crits += 1
            logs.extend(self._trigger_on_crit(round_no, actor, target, actual_damage, roller, scene))
        else:
            actor.consecutive_crits = 0
        logs.extend(self._trigger_on_be_hit(round_no, target, scene))
        logs.extend(self._trigger_on_low_hp(round_no, target, low_hp_after_hit, scene))
        logs.extend(
            self._trigger_spirit_on_hit(
                round_no,
                actor,
                target,
                actual_damage,
                roller,
                source=_DamageSource.ATTACK,
            )
        )
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
        logs.extend(self._trigger_spirit_revive(round_no, target))
        logs.extend(self._trigger_spirit_revive(round_no, actor))

        # 春生·追击：命中后按 bonus_damage 走 _CHUNSHENG_BONUS_PROFILE 施加固定追打伤害（不吃增伤、吃承伤+减伤+护盾）
        chunsheng_followups = [s for s in actor.statuses if s.name == "春生·追击" and s.is_active() and s.bonus_damage > 0]
        if chunsheng_followups and target.hp > 0:
            for s in chunsheng_followups:
                bonus_actual = self._apply_typed_damage(target, s.bonus_damage, _CHUNSHENG_BONUS_PROFILE, actor=actor)
                if bonus_actual > 0:
                    logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 春生回返一击，追加 {bonus_actual} 点伤害。", actor_name=actor.snapshot.name))
                s.remaining_hits = 0
            actor.statuses = self._active_statuses(actor)

        if logs:
            logs[0].target_hp_after = target.hp
        self._consume_attack_bonuses(actor)
        return logs

    def _trigger_before_action(self, round_no: int, state: _CombatState) -> tuple[list[ActionLog], bool]:
        if not state.skip_next_action:
            return ([], True)
        state.skip_next_action = False
        return ([self._effect_log(round_no, state, f"{state.snapshot.name} 灵机一滞，此回合行动被封断。")], False)

    def _trigger_battle_start(self, round_no: int, state: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in state.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "lueying":
                    self._add_status(state, _StatusEffect("掠影", agility_pct=_roll(entry.rolls, "agi_pct", 0)))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 展开掠影，整场身法提高 {entry.rolls['agi_pct']}%。"))
                case "zhenmai":
                    self._add_status(state, _StatusEffect("守势", damage_reduction_pct=_roll(entry.rolls, "reduce_pct", 0), remaining_hits=3))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 凝起镇脉，获得 3 层守势。"))
                case "guben":
                    shield_amount = max(1, state.get_max_hp() * _roll(entry.rolls, "shield_pct", 0) // 100)
                    self._add_status(state, _StatusEffect("固本", shield=shield_amount))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 凝起固本护盾，抵消 {shield_amount} 伤害（免疫净化）。"))
                case "xianji":
                    self._add_status(state, _StatusEffect("先机", agility_pct=_roll(entry.rolls, "initiative_pct", 0)))
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
                    if self._status_count(state, "灵势") >= 12:
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
                    max_hp = state.get_max_hp()
                    amount = max(1, int(max_hp * heal_pct / 100))
                    before = state.hp
                    state.hp = min(max_hp, state.hp + amount)
                    healed = state.hp - before
                    if healed > 0:
                        self._trigger_heal_followups(state, healed)
                        logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 养元运转，回复 {healed} 点生命。"))
            elif entry.affix_id == "xuming" and state.hp > 0:
                cost = _roll(entry.rolls, "cost_stacks", 1)
                consumed = self._consume_shengxi(state, cost)
                if consumed > 0:
                    heal_per_stack = _roll(entry.rolls, "heal_per_stack", 0)
                    heal_pct = consumed * heal_per_stack
                    if heal_pct > 0:
                        healed = self._heal(state, heal_pct)
                        logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 续命发动，消耗 {consumed} 层生息回复 {healed} 点生命。"))
        for entry in state.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "juling":
                    if self._status_count(state, "灵势") >= 12:
                        continue
                    self._add_status(state, _StatusEffect("灵势", atk_pct=_roll(entry.rolls, "atk_pct", 0)))
                    current_layers = self._status_count(state, "灵势")
                    # 每层灵势都叠 1 层聚灵通明，强化后期爆发
                    self._add_status(state, _StatusEffect("聚灵通明", damage_dealt_pct=_roll(entry.rolls, "late_damage_pct", 8)))
                    logs.append(
                        self._effect_log(
                            round_no,
                            state,
                            f"{state.snapshot.name} 的聚灵凝成第 {current_layers} 层灵势，杀伐继续攀升。",
                        )
                    )
                case "jinghua":
                    if not self._has_debuff(state) or roller.random() > _roll(entry.rolls, "proc_pct", 0) / 100:
                        continue
                    removed = self._remove_one_debuff(state)
                    self._add_status(state, _StatusEffect("守势", damage_reduction_pct=_roll(entry.rolls, "reduce_pct", 0), remaining_hits=1))
                    self._trigger_cleanse_followups(state)
                    removed_name = removed.name if removed is not None else "杂念"
                    logs.append(
                        self._effect_log(
                            round_no,
                            state,
                            f"{state.snapshot.name} 的净华洗去「{removed_name}」，并凝成 1 层守势。",
                        )
                    )
                case "qingxin":
                    if not self._has_debuff(state) or roller.random() > _roll(entry.rolls, "proc_pct", 0) / 100:
                        continue
                    removed = self._remove_one_debuff(state)
                    self._trigger_cleanse_followups(state)
                    healed = self._heal(state, _roll(entry.rolls, "heal_pct", 0))
                    removed_name = removed.name if removed is not None else "杂念"
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 清心净念，洗去「{removed_name}」，回复 {healed} 点生命。"))
        # 先机首回合结束后清除标记
        if round_no > 1:
            state.first_round = False
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
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            proc_pct = entry.rolls.get("proc_pct")
            match entry.affix_id:
                case "ningshen":
                    if proc_pct is None or roller.random() > proc_pct / 100 or self._status_count(actor, "灵势") >= 8:
                        continue
                    self._add_status(actor, _StatusEffect("灵势", atk_pct=_roll(entry.rolls, "atk_pct", 0)))
                    logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 凝神聚意，获得 1 层灵势。"))
                case "lueying":
                    if proc_pct is None or roller.random() > proc_pct / 100 or self._status_count(target, "破步") >= 4:
                        continue
                    self._add_status(target, _StatusEffect("破步", agility_pct=-_roll(entry.rolls, "agi_down_pct", 0), is_debuff=True, source=actor))
                    logs.append(self._effect_log(round_no, target, f"{actor.snapshot.name} 的掠影扰乱步法，{target.snapshot.name} 破步加深。", actor_name=actor.snapshot.name))
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
                        # 满层后震慑：消耗全部破步，跳过目标下次行动
                        target.statuses = [s for s in target.statuses if s.name != "破步"]
                        target.skip_next_action = True
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
                case "fengfeng":
                    adjusted_proc = (proc_pct or 0) + min(30, self._positive_status_count(target) * 5)
                    if roller.random() > adjusted_proc / 100 or self._status_count(target, "断锋") >= 4:
                        continue
                    self._add_status(
                        target,
                        _StatusEffect("断锋", atk_pct=-_roll(entry.rolls, "atk_down_pct", 0), is_debuff=True, source=actor),
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的封锋压下锋芒，{target.snapshot.name} 获得 1 层断锋。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "liechuang":
                    if target.hp * 100 > target.snapshot.max_hp * 60 and self._status_count(target, "创伤") <= 0:
                        continue
                    if self._status_count(target, "创伤") < 5:
                        self._add_status(
                            target,
                            _StatusEffect("创伤", heal_received_pct=-_roll(entry.rolls, "heal_down_pct", 5), is_debuff=True, source=actor),
                        )
                    logs.append(self._effect_log(round_no, target, f"{actor.snapshot.name} 撕开裂创，{target.snapshot.name} 的续航被压制。", actor_name=actor.snapshot.name))
                case "suoling":
                    if self._positive_status_count(target) <= 0 or roller.random() > _roll(entry.rolls, "proc_pct", 0) / 100:
                        continue
                    removed = self._remove_one_positive_status(target)
                    bonus_damage = self._apply_damage(target, max(1, actual_damage * _roll(entry.rolls, "damage_pct", 0) // 100))
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的锁灵打散「{removed.name if removed else '灵机'}」，追加 {bonus_damage} 点伤害。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "jifeng":
                    if round_no > 3 or self._status_count(actor, "疾锋") >= 3:
                        continue
                    self._add_status(actor, _StatusEffect("疾锋", agility_pct=_roll(entry.rolls, "agi_pct", 0), damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0)))
                    logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 疾锋加身，速攻势头更盛。"))
                case "yujin":
                    # 余烬：触发器已迁移至 on_burn_consumed（灼烧被消耗时重燃），on_hit 不再处理
                    pass
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
                    gain = _roll(entry.rolls, "burn_stacks_gain", 1)
                    burn_pct = next(
                        (
                            _roll(e.rolls, "burn_atk_pct", 30)
                            for e in actor.snapshot.affixes
                            if e.affix_id == "zhuohun"
                        ),
                        30,
                    )
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
                case "duoling":
                    if roller.random() > _roll(entry.rolls, "proc_pct", 0) / 100:
                        continue
                    healed = self._heal(actor, _roll(entry.rolls, "heal_pct", 0))
                    heal_text = f"吸取 {healed} 点生命" if healed > 0 else "汲取生命"
                    if self._status_count(target, "创伤") < 3:
                        self._add_status(
                            target,
                            _StatusEffect("创伤", heal_received_pct=-_roll(entry.rolls, "heal_down_pct", 4), is_debuff=True, source=actor),
                        )
                        heal_text += f"，{target.snapshot.name} 附加创伤"
                    logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 夺灵{heal_text}。"))
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
                    self._add_status(actor, _StatusEffect("狂锋", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0), remaining_hits=1))
                    logs.append(
                        self._effect_log(
                            round_no,
                            actor,
                            f"{actor.snapshot.name} 借暴击激起狂锋，下一次出手伤害提高 {entry.rolls['damage_pct']}%。",
                        )
                    )
                case "tianwei":
                    if self._status_count(actor, "天威") >= 6:
                        continue
                    self._add_status(
                        actor,
                        _StatusEffect(
                            "天威",
                            crit_bonus_pct=_roll(entry.rolls, "crit_pct", 0),
                            crit_damage_pct=_roll(entry.rolls, "crit_damage_pct", 0),
                        ),
                    )
                    logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 天威加身，暴击率与杀势同涨。"))
                case "leiyin":
                    next_damage_pct = _roll(entry.rolls, "next_damage_pct", 0)
                    self._add_status(
                        actor,
                        _StatusEffect("雷引", damage_dealt_pct=next_damage_pct, remaining_hits=1),
                    )
                    actor.spirit_proc_rounds["leiyin_crit_count"] = (
                        actor.spirit_proc_rounds.get("leiyin_crit_count", 0) + 1
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            actor,
                            f"{actor.snapshot.name} 雷引蓄势，下一击伤害提高 {next_damage_pct}%。",
                        )
                    )
                    if actor.spirit_proc_rounds["leiyin_crit_count"] >= 3:
                        actor.spirit_proc_rounds["leiyin_crit_count"] = 0
                        burst_pct = _roll(entry.rolls, "burst_pct", 0)
                        burst_damage = max(1, target.snapshot.max_hp * burst_pct // 100)
                        burst_actual = self._apply_damage(target, burst_damage, respects_resilience=False)
                        if burst_actual > 0:
                            logs.append(
                                self._effect_log(
                                    round_no,
                                    target,
                                    f"{actor.snapshot.name} 雷引三激，唤出小型雷劫，造成 {burst_actual} 点真伤。",
                                    actor_name=actor.snapshot.name,
                                )
                            )
                case "pokong":
                    if target.hp <= 0:
                        continue
                    base_proc = _roll(entry.rolls, "proc_pct", 0)
                    actor_agi = self._current_agility(actor)
                    target_agi = self._current_agility(target)
                    agi_gap_pct = max(0, (actor_agi * 100 // max(1, target_agi)) - 100) // 10
                    total_proc = base_proc + agi_gap_pct * _roll(entry.rolls, "agi_scale_pct", 0)
                    if roller.random() > total_proc / 100:
                        continue
                    extra_damage = self._current_atk(actor)
                    extra_damage = int(extra_damage * (1 + self._damage_dealt_pct(actor) / 100))
                    extra_actual = self._apply_damage(target, max(1, extra_damage))
                    if extra_actual > 0:
                        logs.append(
                            self._effect_log(
                                round_no,
                                target,
                                f"{actor.snapshot.name} 破空追击！追加 {extra_actual} 点伤害。",
                                actor_name=actor.snapshot.name,
                            )
                        )
        # 雷罚神通：暴击追加雷伤
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
                case "zhuanji":
                    if not self._has_debuff(target):
                        continue
                    if self.rng.random() > _roll(entry.rolls, "proc_pct", 0) / 100:
                        continue
                    removed = self._remove_one_debuff(target)
                    self._trigger_cleanse_followups(target)
                    zhuanji_roll = self.rng.randint(0, 2)
                    if zhuanji_roll == 0:
                        self._add_status(target, _StatusEffect("转机", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 15), remaining_hits=1))
                        bonus_desc = "增伤"
                    elif zhuanji_roll == 1:
                        self._add_status(target, _StatusEffect("守势", damage_reduction_pct=_roll(entry.rolls, "reduce_pct", 20), remaining_hits=1))
                        bonus_desc = "守势"
                    else:
                        self._add_status(target, _StatusEffect("转机", agility_pct=_roll(entry.rolls, "agi_pct", 10)))
                        bonus_desc = "身法"
                    removed_name = removed.name if removed is not None else "杂念"
                    logs.append(self._effect_log(round_no, target, f"{target.snapshot.name} 绝处逢生，「{removed_name}」化为{bonus_desc}之势。"))
        return logs

    def _trigger_on_dodge(self, round_no: int, dodger: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in dodger.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "fengxing":
                    if self._status_count(dodger, "风行") >= 5:
                        continue
                    self._add_status(dodger, _StatusEffect("风行", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0), agility_pct=_roll(entry.rolls, "agi_pct", 0)))
                    logs.append(self._effect_log(round_no, dodger, f"{dodger.snapshot.name} 身法如风，获得 1 层风行。"))
                case "huanbu":
                    if self._status_count(dodger, "幻步") >= 3:
                        continue
                    self._add_status(
                        dodger,
                        _StatusEffect(
                            "幻步",
                            dodge_bonus_pct=_roll(entry.rolls, "dodge_pct", 0),
                            crit_bonus_pct=_roll(entry.rolls, "crit_pct", 0),
                            remaining_hits=1,
                        ),
                    )
                    layers = self._status_count(dodger, "幻步")
                    logs.append(self._effect_log(round_no, dodger, f"{dodger.snapshot.name} 幻步叠至第 {layers} 层，灵动闪避兼蓄势暴击。"))
        return logs

    def _trigger_spirit_on_dodge(self, round_no: int, dodger: _CombatState) -> list[ActionLog]:
        power = dodger.snapshot.spirit_power
        if power is None or power.power_id != "fengdun":
            return []
        # 限制最多 8 层
        if self._status_count(dodger, "风遁") >= 8:
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
        if power is None or power.power_id != "leifa" or target.hp <= 0:
            return []
        # 暴击追加雷罚伤害（吃韧性）
        thunder_pct = power.rolls.get("thunder_pct", 0)
        thunder_damage = max(1, actual_damage * thunder_pct // 100)
        thunder_actual = self._apply_damage(target, thunder_damage)
        logs: list[ActionLog] = []
        if thunder_actual > 0:
            logs.append(
                self._effect_log(
                    round_no,
                    target,
                    f"{actor.snapshot.name} 引雷罚天降，追加 {thunder_actual} 点雷伤。",
                    actor_name=actor.snapshot.name,
                )
            )
        if target.hp <= 0:
            return logs
        # 暴击给目标烙下雷殛标记（上限 3 层）
        leihen_layers = self._target_leihen_count(target)
        if leihen_layers < 3:
            self._add_target_leihen(target, actor)
            leihen_layers += 1
            logs.append(
                self._effect_log(
                    round_no,
                    target,
                    f"{actor.snapshot.name} 在 {target.snapshot.name} 身上烙下雷殛（{leihen_layers}/3）。",
                    actor_name=actor.snapshot.name,
                )
            )
        # 雷殛叠满 3 层立即引爆（豁免韧性）并清除全部雷殛
        if leihen_layers >= 3:
            judgment_pct = power.rolls.get("judgment_pct", 0)
            judgment_damage = max(1, target.snapshot.max_hp * judgment_pct // 100)
            judgment_actual = self._apply_damage(target, judgment_damage, respects_resilience=False)
            self._clear_target_leihen(target)
            if judgment_actual > 0:
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 引动雷劫，雷殛尽炸，造成 {judgment_actual} 点雷劫真伤。",
                        actor_name=actor.snapshot.name,
                    )
                )
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
            if status.name == "风遁" and removed < count:
                removed += 1
                continue
            new_statuses.append(status)
        state.statuses = new_statuses

    def _target_leihen_count(self, state: _CombatState) -> int:
        """目标身上「雷殛」层数（按目标视角统计）。"""
        return sum(1 for s in self._active_statuses(state) if s.name == "雷殛")

    def _add_target_leihen(self, target: _CombatState, actor: _CombatState) -> None:
        self._add_status(target, _StatusEffect("雷殛", is_debuff=True, source=actor))

    def _clear_target_leihen(self, target: _CombatState) -> None:
        target.statuses = [s for s in target.statuses if s.name != "雷殛"]

    def _trigger_on_low_hp(self, round_no: int, target: _CombatState, hp_after_hit: int, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        max_hp = target.get_max_hp()
        # 回春：50% 与 25% 各触发一次
        for threshold in (50, 25):
            if threshold in target.huichun_triggered_thresholds:
                continue
            if hp_after_hit * 100 >= max_hp * threshold:
                continue
            target.huichun_triggered_thresholds.add(threshold)
            for entry in target.snapshot.affixes:
                if entry.affix_id != "huichun" or not self._scene_matches(entry, scene):
                    continue
                healed = self._heal(target, _roll(entry.rolls, "heal_pct", 0))
                stacks = _roll(entry.rolls, "shengxi_stacks", 2)
                for _ in range(stacks):
                    self._add_status(target, _StatusEffect("生息"))
                logs.append(self._effect_log(round_no, target, f"{target.snapshot.name} 的回春发动（生命跌破 {threshold}%），回复 {healed} 点生命并叠加 {stacks} 层生息。"))
        if 50 not in target.low_hp_marks and hp_after_hit * 100 < max_hp * 50:
            target.low_hp_marks.add(50)
        if 35 not in target.low_hp_marks and hp_after_hit * 100 < max_hp * 35:
            target.low_hp_marks.add(35)
            for entry in target.snapshot.affixes:
                if entry.affix_id != "buqu" or not self._scene_matches(entry, scene):
                    continue
                healed = self._heal(target, _roll(entry.rolls, "heal_pct", 0))
                self._add_status(target, _StatusEffect("守势", damage_reduction_pct=_roll(entry.rolls, "reduce_pct", 0), remaining_hits=2))
                self._add_status(target, _StatusEffect("不屈", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0), remaining_hits=1))
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{target.snapshot.name} 的不屈发动，回复 {healed} 点生命，并获得守势与反击锋芒。",
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
            # 灼烧合并为单一状态：duration 表层数，每回合按 source.atk × burn_pct% × stacks 结算后扣 1 层
            for status in list(state.statuses):
                if state.hp <= 0:
                    break
                if status not in state.statuses or not status.is_active() or status.burn_pct <= 0 or status.name != "灼烧":
                    continue
                stacks = status.duration or 0
                if stacks <= 0:
                    continue
                source_atk = self._current_atk(status.source) if status.source is not None else state.snapshot.atk
                raw_damage = max(1, int(source_atk * status.burn_pct / 100))
                actual_damage = self._apply_typed_damage(state, raw_damage, _BURN_DOT_PROFILE, actor=status.source)
                if actual_damage <= 0:
                    continue
                burn_logs = [
                    self._effect_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 受 {stacks} 层灼烧侵蚀，损失 {actual_damage} 点生命。",
                        actor_name=status.source.snapshot.name if status.source is not None else None,
                    )
                ]
                if status.source is not None:
                    burn_logs.extend(
                        self._trigger_spirit_on_hit(
                            round_no,
                            status.source,
                            state,
                            actual_damage,
                            roller,
                            source=_DamageSource.BURN,
                        )
                    )
                    burn_logs.extend(
                        self._trigger_spirit_on_be_hit(
                            round_no,
                            status.source,
                            state,
                            actual_damage,
                            roller,
                            source=_DamageSource.BURN,
                        )
                    )
                died_from_burn = state.hp <= 0
                burn_logs.extend(self._trigger_spirit_revive(round_no, state))
                # 灼烧自然烧尽（本次结算后会被 _decay_statuses 扣到 0）→ 触发 on_burn_consumed
                if (
                    not died_from_burn
                    and state.hp > 0
                    and stacks <= 1
                    and status.source is not None
                    and not status.is_relight
                ):
                    burn_logs.extend(
                        self._trigger_on_burn_consumed(
                            status.source, state, round_no=round_no, roller=roller
                        )
                    )
                if burn_logs:
                    burn_logs[0].target_hp_after = state.hp
                logs.extend(burn_logs)
                if died_from_burn:
                    break
        return logs

    def _trigger_spirit_pre_hit(
        self,
        round_no: int,
        target: _CombatState,
        damage: int,
        roller: random.Random,
    ) -> tuple[int, list[ActionLog]]:
        power = target.snapshot.spirit_power
        if power is None or power.power_id != "xuanjia":
            return damage, []
        if target.block_used_round == round_no:
            return damage, []
        proc_pct = power.rolls["proc_pct"] + min(20, self._status_count(target, "守势") * 5)
        if roller.random() > (proc_pct / 100):
            return damage, []
        target.block_used_round = round_no
        reduced = max(0, damage * max(0, 100 - power.rolls["reduce_pct"]) // 100)
        return (
            reduced,
            [
                self._effect_log(
                    round_no,
                    target,
                    f"{target.snapshot.name} 的玄甲骤然张开，本次伤害减免 {power.rolls['reduce_pct']}%。",
                )
            ],
        )

    def _trigger_spirit_on_hit(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        actual_damage: int,
        roller: random.Random,
        *,
        source: str,
    ) -> list[ActionLog]:
        power = actor.snapshot.spirit_power
        if power is None:
            return []

        logs: list[ActionLog] = []

        # 蚀焰：独立条件触发（命中目标且灼烧≥5 即可引爆），不依赖本次普攻是否造成伤害。
        # 只接受 ATTACK 来源，避免 burn DOT / 引爆自身造成的伤害再次触发蚀焰。
        if (
            power.power_id == "shiyan"
            and source == _DamageSource.ATTACK
            and target.hp > 0
        ):
            stacks = self._burn_stacks(target)
            if stacks >= 5:
                per_burn_pct = power.rolls.get("per_burn_pct", 25)
                total_pct = stacks * per_burn_pct
                # 蚀焰伤害基底改为 actor 当前杀伐 × total_pct%（避免 0 伤普攻引爆为 0）
                base_damage = max(1, self._current_atk(actor) * total_pct // 100)
                # 收集被消耗的灼烧（用于判断是否触发余烬重燃）
                consumed_burns = [s for s in target.statuses if s.name == "灼烧"]
                # 触发即消耗目标全部灼烧层数
                target.statuses = [s for s in target.statuses if s.name != "灼烧"]
                # 蚀焰伤害通过统一管线 _SHIYAN_PROFILE：吃增伤/承伤/减伤/护盾，不暴击、不触发 _before_attack_bonus
                explode_actual = self._apply_typed_damage(target, base_damage, _SHIYAN_PROFILE, actor=actor)
                # 无论实际伤害是否为 0，都明确播报"引爆 + 消耗灼烧"事件
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 的蚀焰引爆 {stacks} 层灼烧！焰意轰然炸裂，造成 {explode_actual} 点伤害。",
                        actor_name=actor.snapshot.name,
                    )
                )
                # 引爆后给目标附加创伤（按器灵品阶递增 1~5 层，受 5 层上限约束）
                wound_stacks = power.rolls.get("wound_stacks", 1)
                if wound_stacks > 0 and target.hp > 0:
                    current_wounds = self._status_count(target, "创伤")
                    add_wounds = min(wound_stacks, max(0, 5 - current_wounds))
                    for _ in range(add_wounds):
                        self._add_status(
                            target,
                            _StatusEffect(
                                "创伤",
                                damage_taken_pct=5,
                                heal_received_pct=-8,
                                is_debuff=True,
                                source=actor,
                            ),
                        )
                    if add_wounds > 0:
                        logs.append(
                            self._effect_log(
                                round_no,
                                target,
                                f"蚀焰焚痕未消，{target.snapshot.name} 附加 {add_wounds} 层创伤。",
                                actor_name=actor.snapshot.name,
                            )
                        )
                # 触发余烬重燃（B 方案：引爆消耗也算 on_burn_consumed）
                if target.hp > 0 and consumed_burns:
                    has_relight = any(getattr(b, "is_relight", False) for b in consumed_burns)
                    if not has_relight:
                        logs.extend(
                            self._trigger_on_burn_consumed(
                                actor, target, round_no=round_no, roller=roller
                            )
                        )

        if actual_damage <= 0:
            return logs

        if power.power_id == "shisheng" and source in {_DamageSource.ATTACK, _DamageSource.BURN, _DamageSource.SPIRIT}:
            healed = self._heal_by_damage(actor, actual_damage, power.rolls["heal_pct"])
            if healed > 0:
                logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 借噬生吞回血气，回复了 {healed} 点生命。"))

        execute_threshold = power.rolls.get("execute_threshold_pct", 0)
        if power.power_id == "jueming" and source in {_DamageSource.ATTACK, _DamageSource.BURN, _DamageSource.SPIRIT} and target.hp > 0:
            execute_threshold += min(12, self._debuff_count(target) * 2)
            if target.hp * 100 <= target.snapshot.max_hp * execute_threshold:
                target.hp = 0
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 以绝命断其残势，{target.snapshot.name} 当场被斩落。",
                        actor_name=actor.snapshot.name,
                    )
                )

        if source != _DamageSource.ATTACK:
            return logs

        if power.power_id == "jinmai" and target.hp > 0:
            proc_pct = power.rolls["proc_pct"] + min(18, (self._status_count(target, "破步") + self._status_count(target, "创伤")) * 4)
            if roller.random() <= proc_pct / 100:
                target.skip_next_action = True
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 的禁脉透体而入，{target.snapshot.name} 下次行动将被封断。",
                        actor_name=actor.snapshot.name,
                    )
                )

        if power.power_id == "xuekuang" and actor.hp * 100 <= actor.snapshot.max_hp * 25:
            healed = self._heal_by_damage(actor, actual_damage, power.rolls["frenzy_lifesteal_pct"])
            if healed > 0:
                logs.append(self._effect_log(round_no, actor, f"{actor.snapshot.name} 狂血奔涌，借濒死杀势回复了 {healed} 点生命。"))

        if power.power_id == "fenmai" and target.hp > 0 and self._has_burn(target):
            cap_pct = power.rolls.get("cap_pct", 6)
            stacks = self._burn_stacks(target)
            final_pct = min(cap_pct, stacks * 2)
            ignite_damage = max(1, target.snapshot.max_hp * final_pct // 100)
            ignite_actual = self._apply_damage(target, ignite_damage)
            if ignite_actual > 0:
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 的焚脉引得灼意暴走，额外焚去 {ignite_actual} 点生命。",
                        actor_name=actor.snapshot.name,
                    )
                )

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
            extra_damage = self._apply_damage(target, max(1, actual_damage * power.rolls["damage_pct"] // 100))
            removed = self._remove_one_positive_status(target) if roller.random() <= power.rolls["proc_pct"] / 100 else None
            if extra_damage > 0 or removed is not None:
                suffix = f"，并震散「{removed.name}」" if removed is not None else ""
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{actor.snapshot.name} 的碎阙破开守势，追加 {extra_damage} 点伤害{suffix}。",
                        actor_name=actor.snapshot.name,
                    )
                )

        if power.power_id == "zhuifeng" and target.hp > 0:
            actor_agi = self._current_agility(actor)
            target_agi = self._current_agility(target)
            if actor_agi * 100 >= target_agi * 150:
                late_decay = max(30, 100 - max(0, round_no - 3) * 15)
                extra_pct = power.rolls["damage_pct"] * late_decay // 100
                extra_damage = self._apply_damage(target, max(1, actual_damage * extra_pct // 100))
                if extra_damage > 0:
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 追风再落一击，追加 {extra_damage} 点伤害。",
                            actor_name=actor.snapshot.name,
                        )
                    )

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
        if power.power_id == "fanji" and source == _DamageSource.ATTACK and actor.hp > 0:
            reflect_pct = power.rolls["reflect_pct"] + (20 if had_damage_reduction else 0)
            reflect_damage = max(1, actual_damage * reflect_pct // 100)
            reflect_damage = min(actor.hp, reflect_damage)
            reflect_damage = self._apply_damage(actor, reflect_damage)
            logs.append(
                self._effect_log(
                    round_no,
                    actor,
                    f"{target.snapshot.name} 的反棘回卷而出，反弹 {reflect_damage} 点伤害。",
                    actor_name=target.snapshot.name,
                )
            )

        if power.power_id == "guifeng" and source == _DamageSource.ATTACK and target.hp > 0 and actor.hp > 0 and target.counter_used_round != round_no:
            proc_pct = power.rolls["proc_pct"] + (15 if target.hp * actor.snapshot.max_hp < actor.hp * target.snapshot.max_hp else 0)
            if roller.random() <= (proc_pct / 100):
                target.counter_used_round = round_no
                counter_damage = max(1, self._current_atk(target) * power.rolls["damage_pct"] // 100)
                counter_damage = self._apply_damage(actor, min(actor.hp, counter_damage))
                logs.append(
                    self._effect_log(
                        round_no,
                        actor,
                        f"{target.snapshot.name} 借归锋逆起一击，反击造成 {counter_damage} 点伤害。",
                        actor_name=target.snapshot.name,
                    )
                )

        if power.power_id == "huajing" and source == _DamageSource.ATTACK and target.hp > 0 and had_damage_reduction:
            healed = self._heal_by_damage(target, actual_damage, power.rolls["convert_pct"])
            if healed > 0:
                logs.append(self._effect_log(round_no, target, f"{target.snapshot.name} 运转化劲，借承伤回转了 {healed} 点生命。"))

        if power.power_id == "zhenling" and target.hp > 0 and self._has_debuff(target) and self._spirit_ready(target, "zhenling", round_no):
            removed = self._remove_one_debuff(target)
            healed = self._heal(target, power.rolls["heal_pct"])
            self._mark_spirit_triggered(target, "zhenling", round_no)
            if removed is not None or healed > 0:
                self._trigger_cleanse_followups(target)
                removed_name = removed.name if removed is not None else "杂念"
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{target.snapshot.name} 的镇灵震落「{removed_name}」，并回复了 {healed} 点生命。",
                    )
                )
        return logs

    def _trigger_spirit_revive(self, round_no: int, state: _CombatState) -> list[ActionLog]:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "niepan" or state.hp > 0:
            return []
        cost = max(1, int(power.rolls.get("cost_stacks", 6)))
        # 必须有足够生息才能复活
        consumed = self._consume_shengxi(state, cost)
        if consumed < cost:
            # 生息不足，回滚（_consume_shengxi 已部分消耗时也回不来——但这里采用"先检查再消耗"逻辑）
            return []
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
        shield_pct = max(0, int(power.rolls.get("revive_shield_pct", 0)))
        shield_amount = 0
        if shield_pct > 0:
            shield_amount = max(1, state.get_max_hp() * shield_pct // 100)
            self._add_status(state, _StatusEffect("涅槃·余烬护盾", shield=shield_amount))
        base_msg = f"{state.snapshot.name} 涅槃再起（第 {state.niepan_revive_count} 次），消耗 {cost} 层生息回复 {heal_amount} 点生命；杀伐 +{atk_bonus}%、身法 +{speed_bonus}%（持续生效）。"
        if shield_amount > 0:
            base_msg += f"凝起余烬护盾 {shield_amount}。"
        return [
            self._effect_log(
                round_no,
                state,
                base_msg,
            )
        ]

    def _consume_shengxi(self, state: _CombatState, amount: int) -> int:
        """消耗 amount 层生息（每个 _StatusEffect("生息") 计为 1 层）。返回实际消耗数量。

        若可用层数不足，不做任何消耗（事务式：要么全消耗，要么不消耗）。
        """
        if amount <= 0:
            return 0
        shengxi_indices = [i for i, st in enumerate(state.statuses) if st.name == "生息" and st.is_active()]
        if len(shengxi_indices) < amount:
            return 0
        # 移除前 amount 个（自前向后）
        to_remove = set(shengxi_indices[:amount])
        state.statuses = [st for i, st in enumerate(state.statuses) if i not in to_remove]
        return amount

    def _before_attack_bonus_pct(self, actor: _CombatState, target: _CombatState, scene: set[str]) -> int:
        total = 0
        target_debuff_count = self._debuff_count(target)
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "zhuiming":
                    if target.hp * 100 > target.snapshot.max_hp * 70:
                        total += _roll(entry.rolls, "damage_pct", 0)
                case "duanyue":
                    total += min(target_debuff_count * _roll(entry.rolls, "per_debuff_pct", _roll(entry.rolls, "damage_pct", 0)), _roll(entry.rolls, "max_bonus_pct", _roll(entry.rolls, "damage_pct", 0)))
                case "zhenguan":
                    target_has_guard = self._has_damage_reduction_status(target) or self._status_count(target, "守势") > 0
                    actor_hp_pct = actor.hp * 100 // max(1, actor.snapshot.max_hp)
                    target_hp_pct = target.hp * 100 // max(1, target.snapshot.max_hp)
                    if target_has_guard or target_hp_pct > actor_hp_pct:
                        total += _roll(entry.rolls, "damage_pct", 0)
                case "zhengheng":
                    actor_hp_pct = actor.hp * 100 // max(1, actor.snapshot.max_hp)
                    target_hp_pct = target.hp * 100 // max(1, target.snapshot.max_hp)
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
                case "xianji":
                    if actor.first_round:
                        total += _roll(entry.rolls, "damage_pct", 0)
        return total

    def _damage_dealt_pct(self, state: _CombatState) -> int:
        total = sum(status.damage_dealt_pct for status in self._active_statuses(state))
        # 灵御：战斗前 6 回合每层灵势降低自身造成伤害（拖时间换爆发期）
        power = state.snapshot.spirit_power
        if power is not None and power.power_id == "lingyu" and state.current_round <= 6:
            total -= self._status_count(state, "灵势") * power.rolls.get("self_damage_down_per_stack_pct", 0)
        return total

    def _spirit_damage_bonus_pct(
        self,
        actor: _CombatState,
        target: _CombatState,
        before_attack_bonus: int,
    ) -> int:
        power = actor.snapshot.spirit_power
        if power is None:
            return 0
        match power.power_id:
            case "xuekuang":
                missing_pct = max(0, 100 - (actor.hp * 100 // max(1, actor.snapshot.max_hp)))
                bonus = (missing_pct // 10) * power.rolls["per_lost_10_pct"]
                bonus = min(bonus, power.rolls["max_bonus_pct"])
                return bonus
            case "luejie":
                bonus = self._debuff_count(target) * power.rolls["per_debuff_pct"]
                return min(bonus, power.rolls["max_bonus_pct"])
            case "chengshi":
                if before_attack_bonus <= 0:
                    return 0
                bonus = power.rolls["base_pct"]
                if self._positive_status_count(actor) > 0:
                    bonus += power.rolls["per_type_pct"]
                return bonus
            case "lingyong":
                # 重做：灵涌仅按"自身灵势层数 × per_stack_pct%"计算增伤，不再叠正面层数权重。
                lingshi_layers = self._status_count(actor, "灵势")
                return lingshi_layers * power.rolls.get("per_stack_pct", 0)
            case "zhuying":
                actor_agi = self._current_agility(actor)
                target_agi = self._current_agility(target)
                if actor_agi <= target_agi:
                    return 0
                gap_steps = max(0, ((actor_agi * 100 // max(1, target_agi)) - 100) // 25)
                bonus = power.rolls["damage_pct"] + gap_steps * power.rolls["per_25_pct"]
                return min(bonus, power.rolls["max_bonus_pct"])
            case "mingche":
                return min(4, self._status_count(actor, "明澈")) * power.rolls["per_stack_pct"]
            case _:
                return 0

    def _damage_taken_pct(self, state: _CombatState) -> int:
        return sum(status.damage_taken_pct for status in self._active_statuses(state))

    def _damage_reduction_pct(self, state: _CombatState) -> int:
        total = sum(status.damage_reduction_pct for status in self._active_statuses(state))
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
                    total += _roll(entry.rolls, "pierce_pct", 0) * leihen_layers
        return total

    def _heal_received_pct(self, state: _CombatState) -> int:
        return sum(status.heal_received_pct for status in self._active_statuses(state))

    def _current_atk(self, state: _CombatState) -> int:
        return max(1, int(state.snapshot.atk * (1 + self._stat_bonus_pct(state, "atk_pct") / 100)))

    def _current_agility(self, state: _CombatState) -> int:
        return max(1, int(state.snapshot.agility * (1 + self._stat_bonus_pct(state, "agility_pct") / 100)))

    def _stat_bonus_pct(self, state: _CombatState, field_name: str) -> int:
        return sum(getattr(status, field_name) for status in self._active_statuses(state))

    def _has_debuff(self, state: _CombatState) -> bool:
        return any(status.is_debuff for status in self._active_statuses(state))

    def _debuff_count(self, state: _CombatState) -> int:
        return sum(1 for status in self._active_statuses(state) if status.is_debuff)

    def _positive_status_count(self, state: _CombatState) -> int:
        # 护盾（shield > 0）免疫净化：不计入可净化的正向状态总数
        return sum(1 for status in self._active_statuses(state) if not status.is_debuff and status.shield <= 0)

    def _has_burn(self, state: _CombatState) -> bool:
        return any(status.name == "灼烧" and status.burn_pct > 0 for status in self._active_statuses(state))

    def _burn_stacks(self, state: _CombatState) -> int:
        for status in self._active_statuses(state):
            if status.name == "灼烧" and status.burn_pct > 0:
                return status.duration or 0
        return 0

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
        existing = None
        for status in target.statuses:
            if status.name == "灼烧" and status.is_active() and status.burn_pct > 0:
                existing = status
                break
        if existing is not None:
            existing.duration = (existing.duration or 0) + stacks
            existing.burn_pct = max(existing.burn_pct, per_stack_pct)
            existing.source = actor
            # 重燃合并到既有灼烧时，若既有灼烧不是重燃产物，保持原 is_relight=False；
            # 反之若既有就是重燃，新加入也视作重燃链路
            if is_relight and not existing.is_relight:
                # 普通灼烧叠加重燃层数：重燃身份让位于普通灼烧（默认更强势），不改 flag
                pass
        else:
            self._add_status(
                target,
                _StatusEffect(
                    "灼烧",
                    duration=stacks,
                    burn_pct=per_stack_pct,
                    is_debuff=True,
                    source=actor,
                    is_relight=is_relight,
                ),
            )
        logs.append(
            self._effect_log(
                round_no,
                target,
                f"{actor.snapshot.name} 灼魂引焰，{target.snapshot.name} 附 {stacks} 层灼烧（每层 {per_stack_pct}% 杀伐）。",
                actor_name=actor.snapshot.name,
            )
        )
        # 重燃不再二次触发 on_burn_apply（避免余烬重燃叠焚心/焚劫导致雪球）
        if not is_relight:
            self._trigger_on_burn_apply(actor, target, round_no=round_no, logs=logs)

    def _trigger_on_burn_apply(
        self,
        actor: _CombatState,
        target: _CombatState,
        *,
        round_no: int,
        logs: list,
    ) -> None:
        for entry in actor.snapshot.affixes:
            affix_def = get_artifact_affix_definition(entry.affix_id)
            if affix_def.trigger != "on_burn_apply":
                continue
            match entry.affix_id:
                case "fenxin":
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

    def _trigger_on_burn_consumed(
        self,
        actor: _CombatState,
        target: _CombatState,
        *,
        round_no: int,
        roller: random.Random,
    ) -> list[ActionLog]:
        """灼烧被消耗时触发（蚀焰引爆 / 自然烧尽）。当前仅用于余烬词条重燃。"""
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
        return sum(status.crit_bonus_pct for status in self._active_statuses(state))

    def _crit_damage_bonus_pct(self, state: _CombatState) -> int:
        total = sum(status.crit_damage_pct for status in self._active_statuses(state))
        # 雷罚神通：常驻暴击伤害基底
        power = state.snapshot.spirit_power
        if power is not None and power.power_id == "leifa":
            total += power.rolls.get("crit_damage_base_pct", 0)
        return total

    def _dodge_bonus_pct(self, state: _CombatState) -> int:
        return sum(status.dodge_bonus_pct for status in self._active_statuses(state))

    def _has_guarantee_crit(self, state: _CombatState) -> bool:
        return any(status.guarantee_crit for status in self._active_statuses(state))

    def _consume_guarantee_crit(self, state: _CombatState) -> None:
        for status in state.statuses:
            if status.is_active() and status.guarantee_crit:
                state.statuses.remove(status)
                return

    def _total_shield(self, state: _CombatState) -> int:
        return sum(status.shield for status in self._active_statuses(state))

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

    def _status_count(self, state: _CombatState, name: str) -> int:
        total = 0
        for status in self._active_statuses(state):
            if status.name != name:
                continue
            total += status.remaining_hits if status.remaining_hits is not None else 1
        return total

    def _status_bonus_pct(self, state: _CombatState, name: str, field_name: str) -> int:
        return sum(getattr(status, field_name) for status in self._active_statuses(state) if status.name == name)

    def _remove_one_debuff(self, state: _CombatState) -> _StatusEffect | None:
        debuffs = [status for status in self._active_statuses(state) if status.is_debuff]
        if not debuffs:
            return None
        debuffs.sort(key=lambda status: 0 if status.burn_pct > 0 else 1)
        removed = debuffs[0]
        state.statuses.remove(removed)
        return removed

    def _remove_one_positive_status(self, state: _CombatState) -> _StatusEffect | None:
        # 护盾（shield > 0）免疫净化
        positives = [status for status in self._active_statuses(state) if not status.is_debuff and status.shield <= 0]
        if not positives:
            return None
        positives.sort(key=lambda status: 0 if status.name in {"灵势", "守势", "登霄"} else 1)
        removed = positives[0]
        state.statuses.remove(removed)
        return removed

    def _spirit_ready(self, state: _CombatState, key: str, round_no: int) -> bool:
        return state.spirit_proc_rounds.get(key) != round_no

    def _mark_spirit_triggered(self, state: _CombatState, key: str, round_no: int) -> None:
        state.spirit_proc_rounds[key] = round_no

    def _active_statuses(self, state: _CombatState) -> list[_StatusEffect]:
        return [status for status in state.statuses if status.is_active()]

    def _add_status(self, state: _CombatState, status: _StatusEffect) -> None:
        state.statuses.append(status)

    def _apply_damage(
        self,
        state: _CombatState,
        damage: int,
        *,
        respects_resilience: bool = True,
    ) -> int:
        """最底层扣血。
        - respects_resilience=True（默认）：扣减 state.snapshot.base_resilience % 后再扣血。
          普攻、反棘、归锋、追击、雷罚雷伤、灼烧 DOT、春生、蚀焰 等所有伤害管线最终都汇聚到这里。
        - respects_resilience=False：豁免境界韧性。仅"机制性必杀真伤"使用，如雷劫引爆。
        - 绝命斩杀直接 `target.hp = 0`，不走本函数，天然豁免。
        """
        if damage <= 0 or state.hp <= 0:
            return 0
        if respects_resilience:
            resilience = max(0, min(95, state.snapshot.base_resilience))
            if resilience > 0:
                damage = max(1, damage * (100 - resilience) // 100)
        before = state.hp
        state.hp = max(0, state.hp - damage)
        return before - state.hp

    def _apply_typed_damage(
        self,
        state: _CombatState,
        raw_damage: int,
        profile: _DamageProfile,
        actor: "_CombatState | None" = None,
    ) -> int:
        """按 profile 对 state 施加伤害。可选地按 actor 走增伤、按 state 走承伤/减伤/护盾。

        - can_be_buffed: 吃 actor 的 damage_dealt_pct + damage_dealt_basis_points
        - can_be_vulned: 吃 state 的 damage_taken_pct + damage_taken_basis_points
        - can_be_reduced: 吃 state 的 damage_reduction_pct + damage_reduction_basis_points（双重 max(0.1, ...) 兜底合并为单次）
        - can_be_shielded: 走 _consume_shield 抵挡

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
        # 护盾抵挡
        if profile.can_be_shielded:
            damage = self._consume_shield(state, damage)
            if damage <= 0:
                return 0
        return self._apply_damage(state, damage, respects_resilience=profile.respects_resilience)

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

    def _heal(self, state: _CombatState, heal_pct: int) -> int:
        heal_pct = max(1, int(heal_pct * max(0.1, 1 + self._heal_received_pct(state) / 100)))
        max_hp = state.get_max_hp()
        amount = max(1, int(max_hp * heal_pct / 100))
        before = state.hp
        state.hp = min(max_hp, state.hp + amount)
        healed = state.hp - before
        self._trigger_heal_followups(state, healed)
        return healed

    def _heal_by_damage(self, state: _CombatState, damage: int, heal_pct: int) -> int:
        if damage <= 0 or heal_pct <= 0:
            return 0
        amount = max(1, damage * heal_pct // 100)
        amount = max(1, int(amount * max(0.1, 1 + self._heal_received_pct(state) / 100)))
        max_hp = state.get_max_hp()
        before = state.hp
        state.hp = min(max_hp, state.hp + amount)
        healed = state.hp - before
        self._trigger_heal_followups(state, healed)
        return healed

    def _trigger_heal_followups(self, state: _CombatState, healed: int) -> None:
        if healed <= 0:
            return
        power = state.snapshot.spirit_power
        # 春生：固定追打 + 治疗时额外叠生息
        if power is not None and power.power_id == "chunsheng":
            convert_pct = power.rolls.get("convert_pct", 0)
            if convert_pct > 0:
                bonus = max(1, int(healed * convert_pct / 100))
                # 用 _StatusEffect.bonus_damage 携带固定追打值（下次攻击命中后以 _CHUNSHENG_BONUS_PROFILE 结算）
                self._add_status(state, _StatusEffect("春生·追击", bonus_damage=bonus, remaining_hits=1))
            shengxi_bonus = power.rolls.get("heal_shengxi_bonus", 0)
            for _ in range(shengxi_bonus):
                # 春生自带的额外生息层数（不计入护元上限）
                self._add_status(state, _StatusEffect("生息"))
        # 护元（huyuan）：自身受到治疗时按 affix index 独立叠层（不超过 per_battle_cap）
        for index, entry in enumerate(state.snapshot.affixes):
            if entry.affix_id != "huyuan":
                continue
            cap = _roll(entry.rolls, "per_battle_cap", 5)
            current = state.huyuan_heal_stacks.get(index, 0)
            if current < cap:
                state.huyuan_heal_stacks[index] = current + 1
                self._add_status(state, _StatusEffect("生息"))

    def _trigger_cleanse_followups(self, state: _CombatState) -> None:
        power = state.snapshot.spirit_power
        if power is None or power.power_id != "mingche" or self._status_count(state, "明澈") >= 4:
            return
        self._add_status(
            state,
            _StatusEffect("明澈", damage_dealt_pct=power.rolls["per_stack_pct"], damage_reduction_pct=power.rolls["per_stack_pct"]),
        )

    def _consume_hit_reduction_statuses(self, state: _CombatState) -> None:
        for status in state.statuses:
            if status.is_active() and status.damage_reduction_pct and status.remaining_hits is not None:
                status.remaining_hits -= 1
        state.statuses = self._active_statuses(state)

    def _consume_attack_bonuses(self, state: _CombatState) -> None:
        for status in state.statuses:
            if status.is_active() and status.remaining_hits is not None and (
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

    def _effect_log(
        self,
        round_no: int,
        target: _CombatState,
        text: str,
        *,
        actor_name: str | None = None,
    ) -> ActionLog:
        return ActionLog(
            round_no=round_no,
            actor_name=actor_name or target.snapshot.name,
            target_name=target.snapshot.name,
            dodged=False,
            critical=False,
            damage=0,
            target_hp_after=target.hp,
            text=text,
        )
