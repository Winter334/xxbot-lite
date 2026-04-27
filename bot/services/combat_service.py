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

    def is_active(self) -> bool:
        duration_ok = self.duration is None or self.duration > 0
        hits_ok = self.remaining_hits is None or self.remaining_hits > 0
        return duration_ok and hits_ok


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
        defender_state = _CombatState(defender, defender.max_hp)
        logs: list[ActionLog] = []

        logs.extend(self._trigger_battle_start(1, challenger_state, scene))
        logs.extend(self._trigger_battle_start(1, defender_state, scene))
        first, second = self._determine_order(challenger_state, defender_state, roller)

        for round_no in range(1, self.max_rounds + 1):
            logs.extend(self._trigger_round_start(round_no, first, roller, scene))
            logs.extend(self._trigger_round_start(round_no, second, roller, scene))

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
        dodge_rate = clamp(0.10 * (self._current_agility(target) / max(self._current_agility(actor), 1)), 0.05, 0.60)
        if roller.random() < dodge_rate:
            logs.append(ActionLog(round_no, actor.snapshot.name, target.snapshot.name, True, False, 0, target.hp))
            self._consume_attack_bonuses(actor)
            return logs

        damage = self._current_atk(actor)
        crit_rate = clamp(0.20 * (self._current_agility(actor) / max(self._current_agility(target), 1)), 0.10, 0.90)
        critical = roller.random() < crit_rate
        if critical:
            crit_multiplier = 1.5 + 0.5 * damage / max(damage + target.snapshot.defense, 1)
            damage = int(damage * crit_multiplier)

        before_attack_bonus = self._before_attack_bonus_pct(actor, target, scene)
        damage = int(damage * (1 + before_attack_bonus / 100))
        damage = int(damage * (1 + self._damage_dealt_pct(actor) / 100))
        damage = int(damage * (1 + self._damage_taken_pct(target) / 100))
        damage = max(1, int(damage * max(0.05, 1 - (self._damage_reduction_pct(target) / 100))))
        damage = int(damage * (1 + self._spirit_damage_bonus_pct(actor, target, before_attack_bonus) / 100))
        damage = int(damage * (1 + actor.snapshot.damage_dealt_basis_points / 10_000))
        if target.snapshot.realm_index > actor.snapshot.realm_index:
            damage = int(damage * (1 + actor.snapshot.versus_higher_realm_damage_basis_points / 10_000))
        damage = int(damage * (1 + target.snapshot.damage_taken_basis_points / 10_000))
        damage = max(1, int(damage * max(0.05, 1 - (target.snapshot.damage_reduction_basis_points / 10_000))))

        damage, pre_hit_logs = self._trigger_spirit_pre_hit(round_no, target, damage, roller)
        logs.extend(pre_hit_logs)
        had_damage_reduction = self._has_damage_reduction_status(target)
        actual_damage = self._apply_damage(target, damage)
        target.hits_taken += 1
        low_hp_after_hit = target.hp
        self._consume_hit_reduction_statuses(target)

        logs.append(ActionLog(round_no, actor.snapshot.name, target.snapshot.name, False, critical, actual_damage, target.hp))
        logs.extend(self._trigger_on_hit(round_no, actor, target, actual_damage, roller, scene))
        if critical:
            logs.extend(self._trigger_on_crit(round_no, actor, scene))
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
        return logs

    def _trigger_round_start(self, round_no: int, state: _CombatState, roller: random.Random, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in state.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "juling":
                    self._add_status(state, _StatusEffect("灵势", atk_pct=_roll(entry.rolls, "atk_pct", 0)))
                    current_layers = self._status_count(state, "灵势")
                    if current_layers >= 6:
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
                    if proc_pct is None or roller.random() > proc_pct / 100:
                        continue
                    self._add_status(
                        target,
                        _StatusEffect("灼烧", duration=2, burn_pct=_roll(entry.rolls, "burn_pct", 1), is_debuff=True, source=actor),
                    )
                    if self._status_count(target, "灼痕") < 6:
                        self._add_status(
                            target,
                            _StatusEffect("灼痕", burn_bonus_pct=_roll(entry.rolls, "scar_bonus_pct", 0), is_debuff=True, source=actor),
                        )
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的灼魂生效，{target.snapshot.name} 附着灼烧并叠加灼痕。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "zhenpo":
                    if proc_pct is None or roller.random() > proc_pct / 100 or self._status_count(target, "破步") >= 4:
                        continue
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
        return logs

    def _trigger_on_crit(self, round_no: int, actor: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in actor.snapshot.affixes:
            if entry.affix_id != "kuangfeng" or not self._scene_matches(entry, scene):
                continue
            self._add_status(actor, _StatusEffect("狂锋", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0), remaining_hits=1))
            logs.append(
                self._effect_log(
                    round_no,
                    actor,
                    f"{actor.snapshot.name} 借暴击激起狂锋，下一次出手伤害提高 {entry.rolls['damage_pct']}%。",
                )
            )
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
        return logs

    def _trigger_on_low_hp(self, round_no: int, target: _CombatState, hp_after_hit: int, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        if 50 not in target.low_hp_marks and hp_after_hit * 100 < target.snapshot.max_hp * 50:
            target.low_hp_marks.add(50)
            for entry in target.snapshot.affixes:
                if entry.affix_id != "huichun" or not self._scene_matches(entry, scene):
                    continue
                healed = self._heal(target, _roll(entry.rolls, "heal_pct", 0))
                self._add_status(target, _StatusEffect("生息", heal_received_pct=_roll(entry.rolls, "heal_bonus_pct", 8)))
                self._add_status(target, _StatusEffect("生息", heal_received_pct=_roll(entry.rolls, "heal_bonus_pct", 8)))
                logs.append(self._effect_log(round_no, target, f"{target.snapshot.name} 的回春发动，回复了 {healed} 点生命，并留下生息。"))
        if 35 not in target.low_hp_marks and hp_after_hit * 100 < target.snapshot.max_hp * 35:
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
            # 灼烧按单层逐次结算，保留来源，便于器灵神通与词条附伤发生交互。
            for status in list(state.statuses):
                if state.hp <= 0:
                    break
                if status not in state.statuses or not status.is_active() or status.burn_pct <= 0:
                    continue
                burn_damage = max(1, int(state.snapshot.max_hp * status.burn_pct / 100))
                burn_damage = self._apply_burn_scar_bonus(burn_damage, state)
                actual_damage = self._apply_damage(state, burn_damage)
                if actual_damage <= 0:
                    continue
                burn_logs = [
                    self._effect_log(
                        round_no,
                        state,
                        f"{state.snapshot.name} 受灼烧侵蚀，损失 {actual_damage} 点生命。",
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
                if burn_logs:
                    burn_logs[0].target_hp_after = state.hp
                logs.extend(burn_logs)
                if died_from_burn:
                    break
            if state.hp <= 0:
                continue
            scar_layers = self._status_count(state, "灼痕")
            if scar_layers < 2:
                continue
            for entry in (challenger.snapshot.affixes if state is defender else defender.snapshot.affixes):
                if entry.affix_id != "ranjin":
                    continue
                source = challenger if state is defender else defender
                multiplier = 2 if scar_layers >= 6 else 1
                ranjin_damage = max(1, int(state.snapshot.max_hp * _roll(entry.rolls, "damage_pct", 0) / 100) * (scar_layers // 2) * multiplier)
                actual_damage = self._apply_damage(state, ranjin_damage)
                if actual_damage > 0:
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 被燃烬灼穿，额外损失 {actual_damage} 点生命。", actor_name=source.snapshot.name))
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
        if power is None or actual_damage <= 0:
            return []

        logs: list[ActionLog] = []
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

        if power.power_id == "fenmai" and target.hp > 0 and (self._has_burn(target) or self._status_count(target, "灼痕") > 0):
            scar_bonus = min(80, self._status_count(target, "灼痕") * 10)
            ignite_damage = max(1, actual_damage * (power.rolls["ignite_pct"] + scar_bonus) // 100)
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
        if power is None or power.power_id != "niepan" or state.hp > 0 or state.revive_used:
            return []
        state.revive_used = True
        heal_amount = max(1, int(state.snapshot.max_hp * power.rolls["heal_pct"] / 100))
        state.hp = heal_amount
        self._add_status(state, _StatusEffect("涅槃护体", damage_reduction_pct=power.rolls["reduce_pct"], remaining_hits=2))
        return [
            self._effect_log(
                round_no,
                state,
                f"{state.snapshot.name} 于绝境中涅槃再起，回复 {heal_amount} 点生命，并得 2 次护体减伤。",
            )
        ]

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
        return total

    def _damage_dealt_pct(self, state: _CombatState) -> int:
        return sum(status.damage_dealt_pct for status in self._active_statuses(state))

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
                bonus = power.rolls["damage_pct"] if before_attack_bonus > 0 else 0
                if self._positive_status_count(actor) > 0 and before_attack_bonus > 0:
                    bonus += power.rolls["damage_pct"] // 2
                return bonus
            case "lingyong":
                weighted_count = self._positive_status_count(actor) + self._status_count(actor, "灵势")
                bonus = weighted_count * power.rolls["per_buff_pct"]
                return min(bonus, power.rolls["max_bonus_pct"])
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
        return sum(status.damage_reduction_pct for status in self._active_statuses(state))

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
        return sum(1 for status in self._active_statuses(state) if not status.is_debuff)

    def _has_burn(self, state: _CombatState) -> bool:
        return any(status.burn_pct > 0 for status in self._active_statuses(state))

    def _has_damage_reduction_status(self, state: _CombatState) -> bool:
        return any(status.damage_reduction_pct > 0 for status in self._active_statuses(state))

    def _status_count(self, state: _CombatState, name: str) -> int:
        total = 0
        for status in self._active_statuses(state):
            if status.name != name:
                continue
            total += status.remaining_hits if status.remaining_hits is not None else 1
        return total

    def _status_bonus_pct(self, state: _CombatState, name: str, field_name: str) -> int:
        return sum(getattr(status, field_name) for status in self._active_statuses(state) if status.name == name)

    def _apply_burn_scar_bonus(self, damage: int, state: _CombatState) -> int:
        bonus_pct = self._status_bonus_pct(state, "灼痕", "burn_bonus_pct")
        return max(1, damage * (100 + bonus_pct) // 100)

    def _remove_one_debuff(self, state: _CombatState) -> _StatusEffect | None:
        debuffs = [status for status in self._active_statuses(state) if status.is_debuff]
        if not debuffs:
            return None
        debuffs.sort(key=lambda status: 0 if status.burn_pct > 0 else 1)
        removed = debuffs[0]
        state.statuses.remove(removed)
        return removed

    def _remove_one_positive_status(self, state: _CombatState) -> _StatusEffect | None:
        positives = [status for status in self._active_statuses(state) if not status.is_debuff]
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

    def _apply_damage(self, state: _CombatState, damage: int) -> int:
        if damage <= 0 or state.hp <= 0:
            return 0
        before = state.hp
        state.hp = max(0, state.hp - damage)
        return before - state.hp

    def _heal(self, state: _CombatState, heal_pct: int) -> int:
        heal_pct = max(1, int(heal_pct * max(0.1, 1 + self._heal_received_pct(state) / 100)))
        amount = max(1, int(state.snapshot.max_hp * heal_pct / 100))
        before = state.hp
        state.hp = min(state.snapshot.max_hp, state.hp + amount)
        healed = state.hp - before
        self._trigger_heal_followups(state, healed)
        return healed

    def _heal_by_damage(self, state: _CombatState, damage: int, heal_pct: int) -> int:
        if damage <= 0 or heal_pct <= 0:
            return 0
        amount = max(1, damage * heal_pct // 100)
        amount = max(1, int(amount * max(0.1, 1 + self._heal_received_pct(state) / 100)))
        before = state.hp
        state.hp = min(state.snapshot.max_hp, state.hp + amount)
        healed = state.hp - before
        self._trigger_heal_followups(state, healed)
        return healed

    def _trigger_heal_followups(self, state: _CombatState, healed: int) -> None:
        if healed <= 0:
            return
        power = state.snapshot.spirit_power
        if power is not None and power.power_id == "chunsheng":
            self._add_status(state, _StatusEffect("春生", damage_dealt_pct=power.rolls["damage_pct"], remaining_hits=1))
        for entry in state.snapshot.affixes:
            if entry.affix_id != "guiyuan":
                continue
            self._add_status(state, _StatusEffect("生息", heal_received_pct=_roll(entry.rolls, "heal_bonus_pct", 8)))
            self._add_status(state, _StatusEffect("归元", damage_dealt_pct=_roll(entry.rolls, "damage_pct", 0), remaining_hits=1))

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
            if status.is_active() and status.damage_dealt_pct and status.remaining_hits is not None:
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
