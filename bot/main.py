from __future__ import annotations

import logging
import random

import discord
from discord.ext import commands

from bot.config import Settings, load_settings
from bot.db import create_engine_and_session_factory, init_models
from bot.services.artifact_service import ArtifactService
from bot.services.breakthrough_service import BreakthroughService
from bot.services.broadcast_service import BroadcastService
from bot.services.character_service import CharacterService
from bot.services.combat_service import CombatService
from bot.services.fate_service import FateService
from bot.services.faction_service import FactionService
from bot.services.idle_service import IdleService
from bot.services.ladder_service import LadderService
from bot.services.pvp_service import PvpService
from bot.services.ranking_service import RankingService
from bot.services.sect_service import SectService
from bot.services.spirit_service import SpiritService
from bot.services.tower_service import TowerService
from bot.services.travel_service import TravelService


class XianBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents, application_id=settings.application_id)
        self.settings = settings
        self.engine, self.session_factory = create_engine_and_session_factory(settings.database_url)

        rng = random.Random()
        self.fate_service = FateService(rng)
        self.artifact_service = ArtifactService(rng)
        self.spirit_service = SpiritService(rng)
        self.sect_service = SectService(rng)
        self.character_service = CharacterService(self.fate_service, self.artifact_service, self.spirit_service)
        self.idle_service = IdleService(self.fate_service)
        self.combat_service = CombatService(rng)
        self.faction_service = FactionService(self.character_service, self.combat_service)
        self.tower_service = TowerService(self.character_service, self.combat_service, self.fate_service, rng)
        self.breakthrough_service = BreakthroughService(self.character_service)
        self.ladder_service = LadderService(self.character_service, self.combat_service)
        self.pvp_service = PvpService(self.character_service, self.combat_service)
        self.ranking_service = RankingService(self.character_service, self.artifact_service, self.spirit_service, self.faction_service)
        self.travel_service = TravelService(self.fate_service, rng)
        self.broadcast_service = BroadcastService(settings)

    async def setup_hook(self) -> None:
        await init_models(self.engine)
        await self.load_extension("bot.commands.xian")
        await self.tree.sync()

    async def close(self) -> None:
        await self.engine.dispose()
        await super().close()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    settings = load_settings()
    if not settings.discord_token:
        raise SystemExit("DISCORD_TOKEN is required.")
    configure_logging(settings.log_level)
    bot = XianBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
