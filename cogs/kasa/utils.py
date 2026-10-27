import asyncio
import datetime
import json
import os
from collections.abc import Iterable
from pathlib import Path

import discord
from aiomqtt import Client
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from core import load_env
from core.classes import BaseClassMixin
from core.models import Field
from database import HS300, Emeter, engine
from loggers import TZ

from .const import (
    MQTT_TIMEOUT,
    SAMPLE_INTERVAL_SECONDS,
    SECONDS_PER_HOUR,
    WATTS_PER_KILOWATT,
    MQTTQoS,
    PlugID,
    plug_mapping,
)

load_env(path=Path.cwd() / "env" / "mqtt.env")

MQTT_BROKER: str = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT: int = int(os.environ.get("MQTT_PORT", 1883))


class KasaUtils(BaseClassMixin):
    @staticmethod
    def _get_emeter_topic(plug_id: PlugID) -> str:
        plug_url_suffix = f"/{int(plug_id)}" if plug_id != PlugID.POWER_STRIP else ""
        return f"hs300/emeter{plug_url_suffix}"

    async def _publish_command(
        self, action: str, plug_id: PlugID, payload: dict | None = None
    ) -> None:
        topic = f"hs300/command/{action}/{int(plug_id)}"
        encoded_payload = json.dumps(payload or {}).encode("utf-8")
        self.logger.info("Publishing MQTT command to %s: %s", topic, payload or {})
        async with Client(MQTT_BROKER, MQTT_PORT) as client:
            await client.publish(topic, encoded_payload, qos=MQTTQoS.AT_LEAST_ONCE)

    async def get_power_usage(self, plug_id: PlugID) -> dict:
        plug_id = PlugID(plug_id)
        topic = self._get_emeter_topic(plug_id)
        async with asyncio.timeout(MQTT_TIMEOUT):
            async with Client(MQTT_BROKER, MQTT_PORT) as client:
                await client.subscribe(topic)
                async for message in client.messages:
                    return {**json.loads(message.payload), "id": int(plug_id)}

        raise RuntimeError(f"No MQTT message received from topic {topic}")

    async def get_power_usage_multiple(self, plug_ids: Iterable[int | PlugID]) -> dict[str, dict]:
        unique_plug_ids = list(dict.fromkeys(PlugID(pid) for pid in plug_ids))
        if not unique_plug_ids:
            return {}

        payloads: dict[str, dict] = {}
        seen_ids: set[int] = set()

        async with asyncio.timeout(MQTT_TIMEOUT):
            async with Client(MQTT_BROKER, MQTT_PORT) as client:
                for plug_id in unique_plug_ids:
                    await client.subscribe(self._get_emeter_topic(plug_id))

                async for message in client.messages:
                    payload = json.loads(message.payload)
                    topic = str(message.topic)
                    id_num = int(
                        PlugID.POWER_STRIP
                        if topic == "hs300/emeter"
                        else topic.rsplit("/", maxsplit=1)[-1]
                    )
                    payloads[payload["name"]] = {**payload, "id": id_num}
                    seen_ids.add(id_num)
                    if len(seen_ids) >= len(unique_plug_ids):
                        break

        return dict(sorted(payloads.items(), key=lambda x: x[1]["id"]))

    async def turn_on(self, plug_id: PlugID) -> str:
        await self._publish_command("on", plug_id)
        return f"Turned on plug: {plug_id!s}"

    async def turn_off(self, plug_id: PlugID) -> str:
        await self._publish_command("off", plug_id)
        return f"Turned off plug: {plug_id!s}"

    async def toggle(self, plug_id: PlugID, status: str | None = None) -> str:
        await self._publish_command("toggle", plug_id, {"status": status})
        return f"Toggled plug: {plug_id!s}"

    async def get_daily_power_usage(self, plug_id: PlugID) -> float:
        async with AsyncSession(engine) as session:
            table = plug_mapping.get(PlugID(plug_id), HS300)
            since = datetime.datetime.now(TZ) - datetime.timedelta(days=1)

            # pylint: disable=not-callable
            query = select(func.sum(table.power)).where(table.create_time >= since)
            total_power = (await session.exec(query)).one()
            if total_power is None:
                return -1.0

            # Convert total power samples (5s interval) to kWh
            return (
                float(total_power)
                / WATTS_PER_KILOWATT
                * SAMPLE_INTERVAL_SECONDS
                / SECONDS_PER_HOUR
            )


class KasaResponseFormatter(BaseClassMixin):
    @classmethod
    async def format_power_usage(cls, payload: dict) -> discord.Embed:
        return await cls.create_embed(
            "Power Usage Report",
            f"Power usage of the plug: {payload['name']}(ID: {payload['id']})",
            Field(name="Total Energy(Wh)", value=payload["total_wh"]),
            Field(name="Voltage(V)", value=payload["V"]),
            Field(name="Current(A)", value=payload["A"]),
            Field(name="Power(W)", value=payload["W"]),
            color=(discord.Color.green() if payload["status"] else discord.Color.from_rgb(0, 0, 0)),
        )

    @classmethod
    async def format_power_usage_multiple(cls, payloads: dict[str, dict]) -> list[discord.Embed]:
        return [await cls.format_power_usage(payload) for payload in payloads.values()]

    @classmethod
    async def format_daily_usage(cls, data: Emeter) -> discord.Embed:
        return await cls.create_embed(
            "Daily Power Usage Report",
            f"Today you used {data.power} Wh of electricity",
            Field(name="Total Energy(Wh)", value=data.total_wh),
            Field(name="Voltage(V)", value=data.voltage),
            Field(name="Current(A)", value=data.current),
            Field(name="Power(W)", value=data.power),
            color=(discord.Color.green() if data.status else discord.Color.from_rgb(0, 0, 0)),
        )
