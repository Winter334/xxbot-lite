from __future__ import annotations

from dataclasses import dataclass
import random

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


@dataclass(slots=True)
class ActionLog:
    round_no: int
    actor_name: str
    target_name: str
    dodged: bool
    critical: bool
    damage: int
    target_hp_after: int


@dataclass(slots=True)
class BattleResult:
    challenger_won: bool
    winner_name: str
    loser_name: str
    rounds: int
    reached_round_limit: bool
    logs: list[ActionLog]


class CombatService:
    max_rounds = 10

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def create_combatant(self, name: str, atk: int, defense: int, agility: int, *, title: str = "", fate_name: str = "") -> CombatantSnapshot:
        return CombatantSnapshot(name, atk, defense, agility, defense * 10, title, fate_name)

    def run_battle(self, challenger: CombatantSnapshot, defender: CombatantSnapshot, *, rng: random.Random | None = None) -> BattleResult:
        roller = rng or self.rng
        hp = {challenger.name: challenger.max_hp, defender.name: defender.max_hp}
        first, second = self._determine_order(challenger, defender, roller)
        logs: list[ActionLog] = []

        for round_no in range(1, self.max_rounds + 1):
            for actor, target in ((first, second), (second, first)):
                action = self._resolve_action(round_no, actor, target, hp[target.name], roller)
                hp[target.name] = action.target_hp_after
                logs.append(action)
                if hp[target.name] <= 0:
                    winner = actor.name
                    loser = target.name
                    return BattleResult(winner == challenger.name, winner, loser, round_no, False, logs)

        return BattleResult(False, defender.name, challenger.name, self.max_rounds, True, logs)

    def _determine_order(
        self,
        challenger: CombatantSnapshot,
        defender: CombatantSnapshot,
        roller: random.Random,
    ) -> tuple[CombatantSnapshot, CombatantSnapshot]:
        if challenger.agility > defender.agility:
            return challenger, defender
        if defender.agility > challenger.agility:
            return defender, challenger
        return (challenger, defender) if roller.random() < 0.5 else (defender, challenger)

    def _resolve_action(
        self,
        round_no: int,
        actor: CombatantSnapshot,
        target: CombatantSnapshot,
        target_hp: int,
        roller: random.Random,
    ) -> ActionLog:
        dodge_rate = clamp(0.10 * (target.agility / max(actor.agility, 1)), 0.05, 0.60)
        if roller.random() < dodge_rate:
            return ActionLog(round_no, actor.name, target.name, True, False, 0, target_hp)

        damage = actor.atk
        crit_rate = clamp(0.20 * (actor.agility / max(target.agility, 1)), 0.10, 0.90)
        critical = roller.random() < crit_rate
        if critical:
            crit_multiplier = 1.5 + 0.5 * actor.atk / max(actor.atk + target.defense, 1)
            damage = int(damage * crit_multiplier)
        target_hp_after = max(0, target_hp - damage)
        return ActionLog(round_no, actor.name, target.name, False, critical, damage, target_hp_after)
