from __future__ import annotations

from dataclasses import dataclass
import random

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.models import Base
from bot.services.artifact_service import ArtifactService
from bot.services.breakthrough_service import BreakthroughService
from bot.services.character_service import CharacterService
from bot.services.combat_service import CombatService
from bot.services.fate_service import FateService
from bot.services.faction_service import FactionService
from bot.services.idle_service import IdleService
from bot.services.ladder_service import LadderService
from bot.services.ranking_service import RankingService
from bot.services.spirit_service import SpiritService
from bot.services.tower_service import TowerService
from bot.services.travel_service import TravelService


@dataclass(slots=True)
class ServiceBundle:
    fate: FateService
    artifact: ArtifactService
    spirit: SpiritService
    character: CharacterService
    idle: IdleService
    combat: CombatService
    faction: FactionService
    tower: TowerService
    breakthrough: BreakthroughService
    ladder: LadderService
    ranking: RankingService
    travel: TravelService


@pytest.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def services() -> ServiceBundle:
    rng = random.Random(42)
    fate = FateService(rng)
    artifact = ArtifactService(rng)
    spirit = SpiritService(rng)
    character = CharacterService(fate, artifact, spirit)
    idle = IdleService(fate)
    combat = CombatService(rng)
    faction = FactionService(character, combat)
    tower = TowerService(character, combat, fate, rng)
    breakthrough = BreakthroughService(character)
    ladder = LadderService(character, combat)
    ranking = RankingService(character, artifact, spirit, faction)
    travel = TravelService(fate, rng)
    return ServiceBundle(fate, artifact, spirit, character, idle, combat, faction, tower, breakthrough, ladder, ranking, travel)
