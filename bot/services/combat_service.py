from __future__ import annotations

from dataclasses import dataclass, field
import random

from bot.data.artifact_affixes import ArtifactAffixEntry, get_artifact_affix_definition
from bot.utils.formatters import clamp


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


@dataclass(slots=True)
class _StatusEffect:
    name: str
    duration: int | None = None
    atk_pct: int = 0
    agility_pct: int = 0
    damage_taken_pct: int = 0
    damage_reduction_pct: int = 0
    damage_dealt_pct: int = 0
    burn_pct: int = 0
    remaining_hits: int | None = None
    is_debuff: bool = False

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


class CombatService:
    max_rounds = 10

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
            logs.extend(self._trigger_round_start(round_no, first, scene))
            logs.extend(self._trigger_round_start(round_no, second, scene))

            for actor, target in ((first, second), (second, first)):
                if actor.hp <= 0 or target.hp <= 0:
                    continue
                logs.extend(self._resolve_action(round_no, actor, target, roller, scene))
                if challenger_state.hp <= 0 or defender_state.hp <= 0:
                    return self._build_result(challenger_state, defender_state, round_no, False, logs)

            logs.extend(self._trigger_round_end(round_no, challenger_state, defender_state))
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

        damage = int(damage * (1 + self._before_attack_bonus_pct(actor, target, scene) / 100))
        damage = int(damage * (1 + self._damage_dealt_pct(actor) / 100))
        damage = int(damage * (1 + self._damage_taken_pct(target) / 100))
        damage = max(1, int(damage * max(0.05, 1 - (self._damage_reduction_pct(target) / 100))))
        damage = int(damage * (1 + actor.snapshot.damage_dealt_basis_points / 10_000))
        if target.snapshot.realm_index > actor.snapshot.realm_index:
            damage = int(damage * (1 + actor.snapshot.versus_higher_realm_damage_basis_points / 10_000))
        damage = int(damage * (1 + target.snapshot.damage_taken_basis_points / 10_000))
        damage = max(1, int(damage * max(0.05, 1 - (target.snapshot.damage_reduction_basis_points / 10_000))))

        target.hp = max(0, target.hp - damage)
        target.hits_taken += 1
        low_hp_after_hit = target.hp
        self._consume_hit_reduction_statuses(target)

        logs.append(ActionLog(round_no, actor.snapshot.name, target.snapshot.name, False, critical, damage, target.hp))
        logs.extend(self._trigger_on_hit(round_no, actor, target, roller, scene))
        if critical:
            logs.extend(self._trigger_on_crit(round_no, actor, scene))
        logs.extend(self._trigger_on_be_hit(round_no, target, scene))
        logs.extend(self._trigger_on_low_hp(round_no, target, low_hp_after_hit, scene))

        if logs:
            logs[0].target_hp_after = target.hp
        self._consume_attack_bonuses(actor)
        return logs

    def _trigger_battle_start(self, round_no: int, state: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in state.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "ningshen":
                    self._add_status(state, _StatusEffect("凝神", duration=2, atk_pct=entry.rolls["atk_pct"]))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 获得 2 回合凝神，杀伐提高 {entry.rolls['atk_pct']}%。"))
                case "lueying":
                    self._add_status(state, _StatusEffect("掠影", duration=2, agility_pct=entry.rolls["agi_pct"]))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 获得 2 回合掠影，身法提高 {entry.rolls['agi_pct']}%。"))
                case "zhenmai":
                    self._add_status(state, _StatusEffect("镇脉", damage_reduction_pct=entry.rolls["reduce_pct"], remaining_hits=2))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 凝起镇脉，前 2 次受击减伤 {entry.rolls['reduce_pct']}%。"))
                case "dengxiao":
                    self._add_status(
                        state,
                        _StatusEffect("登霄", duration=2, atk_pct=entry.rolls["atk_pct"], agility_pct=entry.rolls["agi_pct"]),
                    )
                    logs.append(
                        self._effect_log(
                            round_no,
                            state,
                            f"{state.snapshot.name} 借登霄之势，2 回合内杀伐提高 {entry.rolls['atk_pct']}%，身法提高 {entry.rolls['agi_pct']}%。",
                        )
                    )
                case "zhengheng":
                    self._add_status(state, _StatusEffect("争衡", duration=2, agility_pct=entry.rolls["agi_pct"]))
                    logs.append(self._effect_log(round_no, state, f"{state.snapshot.name} 激发争衡，2 回合内身法提高 {entry.rolls['agi_pct']}%。"))
        return logs

    def _trigger_round_start(self, round_no: int, state: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        if round_no > 3:
            return logs
        for entry in state.snapshot.affixes:
            if entry.affix_id != "juling" or not self._scene_matches(entry, scene):
                continue
            self._add_status(state, _StatusEffect("聚灵", atk_pct=entry.rolls["atk_pct"]))
            current_layers = sum(1 for status in self._active_statuses(state) if status.name == "聚灵")
            logs.append(
                self._effect_log(
                    round_no,
                    state,
                    f"{state.snapshot.name} 的聚灵叠至 {current_layers} 层，当前每层提供 {entry.rolls['atk_pct']}% 杀伐。",
                )
            )
        return logs

    def _trigger_on_hit(
        self,
        round_no: int,
        actor: _CombatState,
        target: _CombatState,
        roller: random.Random,
        scene: set[str],
    ) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            proc_pct = entry.rolls.get("proc_pct")
            if entry.affix_id not in {"shigu", "zhuohun", "zhenpo", "fengfeng"} or proc_pct is None:
                continue
            if roller.random() > (proc_pct / 100):
                continue
            match entry.affix_id:
                case "shigu":
                    self._add_status(target, _StatusEffect("易伤", duration=2, damage_taken_pct=entry.rolls["vuln_pct"], is_debuff=True))
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的蚀骨生效，{target.snapshot.name} 2 回合内易伤 +{entry.rolls['vuln_pct']}%。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "zhuohun":
                    self._add_status(target, _StatusEffect("灼烧", duration=2, burn_pct=entry.rolls["burn_pct"], is_debuff=True))
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的灼魂生效，{target.snapshot.name} 附着 2 回合灼烧，每回合损失最大生命 {entry.rolls['burn_pct']}%。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "zhenpo":
                    self._add_status(target, _StatusEffect("震魄", duration=2, agility_pct=-entry.rolls["agi_down_pct"], is_debuff=True))
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的震魄生效，{target.snapshot.name} 2 回合内身法降低 {entry.rolls['agi_down_pct']}%。",
                            actor_name=actor.snapshot.name,
                        )
                    )
                case "fengfeng":
                    self._add_status(target, _StatusEffect("封锋", duration=2, atk_pct=-entry.rolls["atk_down_pct"], is_debuff=True))
                    logs.append(
                        self._effect_log(
                            round_no,
                            target,
                            f"{actor.snapshot.name} 的封锋生效，{target.snapshot.name} 2 回合内杀伐降低 {entry.rolls['atk_down_pct']}%。",
                            actor_name=actor.snapshot.name,
                        )
                    )
        return logs

    def _trigger_on_crit(self, round_no: int, actor: _CombatState, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for entry in actor.snapshot.affixes:
            if entry.affix_id != "kuangfeng" or not self._scene_matches(entry, scene):
                continue
            self._add_status(actor, _StatusEffect("狂锋", damage_dealt_pct=entry.rolls["damage_pct"], remaining_hits=1))
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
        if target.hits_taken > 2:
            return logs
        for entry in target.snapshot.affixes:
            if entry.affix_id != "yazhen" or not self._scene_matches(entry, scene):
                continue
            self._add_status(target, _StatusEffect("压阵", damage_dealt_pct=entry.rolls["damage_pct"], remaining_hits=1))
            logs.append(
                self._effect_log(
                    round_no,
                    target,
                    f"{target.snapshot.name} 的压阵被激发，下一次出手伤害提高 {entry.rolls['damage_pct']}%。",
                )
            )
        return logs

    def _trigger_on_low_hp(self, round_no: int, target: _CombatState, hp_after_hit: int, scene: set[str]) -> list[ActionLog]:
        logs: list[ActionLog] = []
        if 50 not in target.low_hp_marks and hp_after_hit * 100 < target.snapshot.max_hp * 50:
            target.low_hp_marks.add(50)
            for entry in target.snapshot.affixes:
                if entry.affix_id != "huichun" or not self._scene_matches(entry, scene):
                    continue
                healed = self._heal(target, entry.rolls["heal_pct"])
                logs.append(self._effect_log(round_no, target, f"{target.snapshot.name} 的回春发动，回复了 {healed} 点生命。"))
        if 40 not in target.low_hp_marks and hp_after_hit * 100 < target.snapshot.max_hp * 40:
            target.low_hp_marks.add(40)
            for entry in target.snapshot.affixes:
                if entry.affix_id != "buqu" or not self._scene_matches(entry, scene):
                    continue
                healed = self._heal(target, entry.rolls["heal_pct"])
                self._add_status(target, _StatusEffect("不屈", damage_reduction_pct=entry.rolls["reduce_pct"], remaining_hits=1))
                logs.append(
                    self._effect_log(
                        round_no,
                        target,
                        f"{target.snapshot.name} 的不屈发动，回复 {healed} 点生命，且下一次受击减伤 {entry.rolls['reduce_pct']}%。",
                    )
                )
        return logs

    def _trigger_round_end(self, round_no: int, challenger: _CombatState, defender: _CombatState) -> list[ActionLog]:
        logs: list[ActionLog] = []
        for state in (challenger, defender):
            if state.hp <= 0:
                continue
            burn_damage = sum(max(1, int(state.snapshot.max_hp * status.burn_pct / 100)) for status in self._active_statuses(state) if status.burn_pct > 0)
            if burn_damage <= 0:
                continue
            actual_damage = min(state.hp, burn_damage)
            state.hp -= actual_damage
            logs.append(
                self._effect_log(
                    round_no,
                    state,
                    f"{state.snapshot.name} 受灼烧侵蚀，损失 {actual_damage} 点生命。",
                )
            )
        return logs

    def _before_attack_bonus_pct(self, actor: _CombatState, target: _CombatState, scene: set[str]) -> int:
        total = 0
        if self._has_debuff(target):
            target_has_debuff = True
        else:
            target_has_debuff = False
        for entry in actor.snapshot.affixes:
            if not self._scene_matches(entry, scene):
                continue
            match entry.affix_id:
                case "zhuiming":
                    if target.hp * 100 > target.snapshot.max_hp * 70:
                        total += entry.rolls["damage_pct"]
                case "duanyue":
                    if target_has_debuff:
                        total += entry.rolls["damage_pct"]
                case "zhenguan":
                    if "scene_boss" in scene:
                        total += entry.rolls["damage_pct"]
        return total

    def _damage_dealt_pct(self, state: _CombatState) -> int:
        return sum(status.damage_dealt_pct for status in self._active_statuses(state))

    def _damage_taken_pct(self, state: _CombatState) -> int:
        return sum(status.damage_taken_pct for status in self._active_statuses(state))

    def _damage_reduction_pct(self, state: _CombatState) -> int:
        return sum(status.damage_reduction_pct for status in self._active_statuses(state))

    def _current_atk(self, state: _CombatState) -> int:
        return max(1, int(state.snapshot.atk * (1 + self._stat_bonus_pct(state, "atk_pct") / 100)))

    def _current_agility(self, state: _CombatState) -> int:
        return max(1, int(state.snapshot.agility * (1 + self._stat_bonus_pct(state, "agility_pct") / 100)))

    def _stat_bonus_pct(self, state: _CombatState, field_name: str) -> int:
        return sum(getattr(status, field_name) for status in self._active_statuses(state))

    def _has_debuff(self, state: _CombatState) -> bool:
        return any(status.is_debuff for status in self._active_statuses(state))

    def _active_statuses(self, state: _CombatState) -> list[_StatusEffect]:
        return [status for status in state.statuses if status.is_active()]

    def _add_status(self, state: _CombatState, status: _StatusEffect) -> None:
        state.statuses.append(status)

    def _heal(self, state: _CombatState, heal_pct: int) -> int:
        amount = max(1, int(state.snapshot.max_hp * heal_pct / 100))
        before = state.hp
        state.hp = min(state.snapshot.max_hp, state.hp + amount)
        return state.hp - before

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
