from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import random

from bot.data.realms import get_stage
from bot.models.character import Character
from bot.services.fate_service import FateService
from bot.utils.time_utils import ensure_shanghai, now_shanghai


TRAVEL_DURATION_CHOICES = (30, 60, 120, 180, 240, 300)


@dataclass(slots=True)
class TravelEventDefinition:
    event_id: str
    title: str
    flavor_text: str
    weight: int
    soul_min: int = 0
    soul_max: int = 0
    cultivation_pct_min: int = 0
    cultivation_pct_max: int = 0
    atk_pct_min: int = 0
    atk_pct_max: int = 0
    def_pct_min: int = 0
    def_pct_max: int = 0
    agi_pct_min: int = 0
    agi_pct_max: int = 0
    no_gain: bool = False


@dataclass(slots=True)
class TravelEventLog:
    title: str
    flavor_text: str
    result_text: str
    soul_delta: int
    cultivation_delta: int
    atk_pct_delta: int
    def_pct_delta: int
    agi_pct_delta: int


@dataclass(slots=True)
class TravelSettlement:
    success: bool
    message: str
    settled_events: int
    settled_minutes: int
    elapsed_minutes: int
    total_soul: int
    total_cultivation: int
    total_atk_pct: int
    total_def_pct: int
    total_agi_pct: int
    completed: bool
    logs: tuple[TravelEventLog, ...]


class TravelService:
    event_interval_minutes = 30
    max_events_per_trip = 10

    def __init__(self, fate_service: FateService, rng: random.Random | None = None) -> None:
        self.fate_service = fate_service
        self.rng = rng or random.Random()
        self._events = self._build_event_pool()

    def current_travel_minutes(self, character: Character, *, now=None) -> int:
        if not character.is_traveling:
            return 0
        current_time = ensure_shanghai(now or now_shanghai())
        elapsed = current_time - ensure_shanghai(character.travel_started_at)
        capped = min(elapsed, timedelta(minutes=character.travel_duration_minutes))
        return max(0, int(capped.total_seconds() // 60))

    def next_duration_choice(self, current_minutes: int) -> int:
        try:
            index = TRAVEL_DURATION_CHOICES.index(current_minutes)
        except ValueError:
            return TRAVEL_DURATION_CHOICES[0]
        return TRAVEL_DURATION_CHOICES[(index + 1) % len(TRAVEL_DURATION_CHOICES)]

    def cycle_selected_duration(self, character: Character) -> int:
        next_minutes = self.next_duration_choice(character.travel_selected_duration_minutes or TRAVEL_DURATION_CHOICES[0])
        character.travel_selected_duration_minutes = next_minutes
        character.last_highlight_text = f"方才翻了翻行程卷册，将下次游历定为 {next_minutes} 分钟。"
        return next_minutes

    def start_travel(self, character: Character, duration_minutes: int | None = None, *, now=None) -> TravelSettlement:
        actual_duration = duration_minutes or character.travel_selected_duration_minutes or TRAVEL_DURATION_CHOICES[0]
        if actual_duration not in TRAVEL_DURATION_CHOICES:
            return TravelSettlement(False, "该游历时长尚未列入一期行程。", 0, 0, 0, 0, 0, 0, 0, 0, False, ())
        if character.is_traveling:
            return TravelSettlement(False, "你已在外游历，暂不可再启新程。", 0, 0, 0, 0, 0, 0, 0, 0, False, ())
        if character.is_retreating:
            return TravelSettlement(False, "你仍在洞府闭关，需先出关，方可动身游历。", 0, 0, 0, 0, 0, 0, 0, 0, False, ())

        current_time = ensure_shanghai(now or now_shanghai())
        character.is_traveling = True
        character.travel_started_at = current_time
        character.travel_duration_minutes = actual_duration
        character.travel_selected_duration_minutes = actual_duration
        character.last_highlight_text = f"方才离山游历，预计行程 {actual_duration} 分钟。"
        return TravelSettlement(
            True,
            f"你已离开山门，踏上 {actual_duration} 分钟的游历行程。",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            False,
            (),
        )

    def stop_travel(self, character: Character, *, now=None) -> TravelSettlement:
        if not character.is_traveling:
            return TravelSettlement(False, "你当前并未游历，无需强行归程。", 0, 0, 0, 0, 0, 0, 0, 0, False, ())

        current_time = ensure_shanghai(now or now_shanghai())
        elapsed_minutes = self.current_travel_minutes(character, now=current_time)
        settled_events = min(self.max_events_per_trip, elapsed_minutes // self.event_interval_minutes)
        logs = tuple(self._resolve_event(character) for _ in range(settled_events))
        total_soul = sum(log.soul_delta for log in logs)
        total_cultivation = sum(log.cultivation_delta for log in logs)
        total_atk_pct = sum(log.atk_pct_delta for log in logs)
        total_def_pct = sum(log.def_pct_delta for log in logs)
        total_agi_pct = sum(log.agi_pct_delta for log in logs)
        completed = elapsed_minutes >= character.travel_duration_minutes
        settled_minutes = settled_events * self.event_interval_minutes

        character.is_traveling = False
        character.travel_started_at = current_time
        previous_duration = character.travel_duration_minutes
        character.travel_duration_minutes = 0
        character.travel_selected_duration_minutes = previous_duration or character.travel_selected_duration_minutes
        if logs:
            pieces: list[str] = []
            if total_soul:
                pieces.append(f"器魂 {'+' if total_soul > 0 else ''}{total_soul}")
            if total_cultivation:
                pieces.append(f"修为 {'+' if total_cultivation > 0 else ''}{total_cultivation}")
            for label, delta in (("杀伐", total_atk_pct), ("护体", total_def_pct), ("身法", total_agi_pct)):
                if delta:
                    pieces.append(f"{label} {'+' if delta > 0 else ''}{delta}%")
            character.last_highlight_text = f"方才游历归来，{'，'.join(pieces)}。"
        elif completed:
            character.last_highlight_text = "方才游历归来，此行虽无所得，却也算见过山海。"
        else:
            character.last_highlight_text = "方才中途折返，此次游历尚未来得及撞上真正机缘。"

        if completed:
            message = f"你已按原定 {previous_duration} 分钟行程归来，本次游历共结算 {settled_events} 次奇遇。"
        elif settled_events > 0:
            message = f"你中途折返，已按已走完的 {settled_minutes} 分钟路程结算 {settled_events} 次奇遇。未满 30 分钟的路程不计收益。"
        else:
            message = "你中途折返，此行未满一个结算周期，故未有实际收益。"

        return TravelSettlement(
            True,
            message,
            settled_events,
            settled_minutes,
            elapsed_minutes,
            total_soul,
            total_cultivation,
            total_atk_pct,
            total_def_pct,
            total_agi_pct,
            completed,
            logs,
        )

    def _resolve_event(self, character: Character) -> TravelEventLog:
        event = self.rng.choices(self._events, weights=[item.weight for item in self._events], k=1)[0]
        soul_delta = self._roll_range(event.soul_min, event.soul_max)
        cultivation_delta = self._roll_cultivation(character, event.cultivation_pct_min, event.cultivation_pct_max)
        if soul_delta > 0:
            soul_delta = self.fate_service.apply_system_soul_modifier(character.fate_key, soul_delta)
        atk_pct_delta = self._roll_range(event.atk_pct_min, event.atk_pct_max)
        def_pct_delta = self._roll_range(event.def_pct_min, event.def_pct_max)
        agi_pct_delta = self._roll_range(event.agi_pct_min, event.agi_pct_max)

        if soul_delta and character.artifact is not None:
            character.artifact.soul_shards = max(0, (character.artifact.soul_shards or 0) + soul_delta)
        if cultivation_delta:
            character.cultivation = max(0, character.cultivation + cultivation_delta)
        character.travel_atk_pct += atk_pct_delta
        character.travel_def_pct += def_pct_delta
        character.travel_agi_pct += agi_pct_delta

        return TravelEventLog(
            title=event.title,
            flavor_text=event.flavor_text,
            result_text=self._format_result_text(soul_delta, cultivation_delta, atk_pct_delta, def_pct_delta, agi_pct_delta, no_gain=event.no_gain),
            soul_delta=soul_delta,
            cultivation_delta=cultivation_delta,
            atk_pct_delta=atk_pct_delta,
            def_pct_delta=def_pct_delta,
            agi_pct_delta=agi_pct_delta,
        )

    def _roll_cultivation(self, character: Character, pct_min: int, pct_max: int) -> int:
        if pct_min == pct_max == 0:
            return 0
        pct = self._roll_range(pct_min, pct_max)
        if pct == 0:
            return 0
        stage = get_stage(character.realm_key, character.stage_key)
        amount = max(1, int(stage.cultivation_max * abs(pct) / 100))
        if pct > 0:
            return min(amount, max(0, stage.cultivation_max - character.cultivation))
        return -min(amount, character.cultivation)

    def _roll_range(self, lower: int, upper: int) -> int:
        if lower == upper:
            return lower
        return self.rng.randint(lower, upper)

    def _format_result_text(
        self,
        soul_delta: int,
        cultivation_delta: int,
        atk_pct_delta: int,
        def_pct_delta: int,
        agi_pct_delta: int,
        *,
        no_gain: bool,
    ) -> str:
        if no_gain:
            return "本次结算无收益"

        parts: list[str] = []
        if soul_delta:
            parts.append(f"器魂 {'+' if soul_delta > 0 else ''}{soul_delta}")
        if cultivation_delta:
            parts.append(f"修为 {'+' if cultivation_delta > 0 else ''}{cultivation_delta}")
        if atk_pct_delta:
            parts.append(f"杀伐 {'+' if atk_pct_delta > 0 else ''}{atk_pct_delta}%")
        if def_pct_delta:
            parts.append(f"护体 {'+' if def_pct_delta > 0 else ''}{def_pct_delta}%")
        if agi_pct_delta:
            parts.append(f"身法 {'+' if agi_pct_delta > 0 else ''}{agi_pct_delta}%")
        return "，".join(parts) if parts else "本次结算无收益"

    def _build_event_pool(self) -> tuple[TravelEventDefinition, ...]:
        return (
            TravelEventDefinition("soul_1", "山涧拾魂", "夜色沉沉，你在乱石缝间寻到一缕未散器魂，收入本命法宝之中。", 12, soul_min=2, soul_max=5),
            TravelEventDefinition("cult_1", "崖壁悟道", "山风掠过断崖，你于斑驳石痕间忽生顿悟，胸中所学略有精进。", 8, cultivation_pct_min=3, cultivation_pct_max=6),
            TravelEventDefinition("mix_1", "破庙残卷", "你在荒庙供案下翻出一页残卷，虽不成体系，却仍有几分妙用。", 7, soul_min=1, soul_max=3, cultivation_pct_min=2, cultivation_pct_max=4),
            TravelEventDefinition("atk_up", "灵泉洗体", "山腹灵泉温润如玉，你静坐片刻，只觉气血流转都轻快了几分。", 6, atk_pct_min=1, atk_pct_max=2),
            TravelEventDefinition("soul_2", "荒丘遗藏", "你顺着残破阵纹挖开旧土，竟寻到一处遗藏，器魂盈溢而散。", 8, soul_min=4, soul_max=8),
            TravelEventDefinition("def_up", "山门旧碑", "古老山门旁立着一方残碑，你凝视良久，竟从其中悟出一缕道意。", 5, def_pct_min=2, def_pct_max=3),
            TravelEventDefinition("rare_1", "天碑留痕", "云海深处现出一方天碑，其上道痕只显一息，你勉强记下一笔。", 3, agi_pct_min=3, agi_pct_max=4),
            TravelEventDefinition("rare_2", "洞天遗府", "你误入残缺洞天，虽只得其一角，却已足够让本命法宝大饱一顿。", 3, soul_min=8, soul_max=12),
            TravelEventDefinition("rare_3", "异果成熟", "老树深处结出一枚异果，你摘下吞服，只觉筋骨与灵台一并震动。", 3, cultivation_pct_min=4, cultivation_pct_max=8, atk_pct_min=2, atk_pct_max=3),
            TravelEventDefinition("trade_1", "燃血炼魂", "你以血气温养残魂，虽伤自身根基，却让法宝中的器魂明显壮大。", 6, soul_min=4, soul_max=8, cultivation_pct_min=-6, cultivation_pct_max=-3),
            TravelEventDefinition("trade_2", "碎境求悟", "你强行打碎一缕将成未成的感悟，换得更深的体悟留在道躯之中。", 5, cultivation_pct_min=-8, cultivation_pct_max=-4, def_pct_min=1, def_pct_max=2),
            TravelEventDefinition("trade_3", "炼魂反噬", "你试图炼化异质残魂，过程略有反噬，却也让灵台多出几分沉淀。", 5, soul_min=-3, soul_max=-1, cultivation_pct_min=5, cultivation_pct_max=9),
            TravelEventDefinition("trade_4", "祭脉开门", "你以一缕经脉损耗为代价，强开前路，换来更多可供炼化的残魂。", 4, soul_min=6, soul_max=10, agi_pct_min=-1, agi_pct_max=-1),
            TravelEventDefinition("swing_1", "吞食异果", "你误入荒谷果林，摘下一枚异果吞下，药力横冲经脉，痛得眼前发黑。", 6, atk_pct_min=2, atk_pct_max=4, def_pct_min=-2, def_pct_max=-1),
            TravelEventDefinition("swing_2", "逆流淬骨", "你跃入寒潭逆流淬骨，骨血更坚，却也让身形沉滞了几分。", 6, def_pct_min=2, def_pct_max=4, agi_pct_min=-2, agi_pct_max=-1),
            TravelEventDefinition("swing_3", "风雷过体", "你在山巅引风雷入体，脚步愈发轻灵，但杀伐中的沉重也被削去。", 6, agi_pct_min=2, agi_pct_max=4, atk_pct_min=-2, atk_pct_max=-1),
            TravelEventDefinition("swing_4", "三才错脉", "你误触古阵，体内三才气机一时紊乱，虽有所得，却也付出代价。", 4, atk_pct_min=3, atk_pct_max=3, agi_pct_min=-1, agi_pct_max=-1),
            TravelEventDefinition("bad_1", "误入瘴林", "你误入瘴林深处，勉强脱身时已头晕目眩，所得感悟也散了大半。", 6, cultivation_pct_min=-6, cultivation_pct_max=-3),
            TravelEventDefinition("bad_2", "邪风蚀骨", "荒野邪风透骨而过，你强撑片刻，仍觉道躯某处隐隐受损。", 6, def_pct_min=-2, def_pct_max=-1),
            TravelEventDefinition("bad_3", "贼修掠魂", "夜半忽有贼修窥伺，你虽将其惊退，却还是被顺走几缕器魂。", 5, soul_min=-4, soul_max=-2),
            TravelEventDefinition("bad_4", "岔路迷行", "你在群山迷雾中绕行许久，直到天色将晚，才发现这一路几乎一无所获。", 5, no_gain=True),
        )
