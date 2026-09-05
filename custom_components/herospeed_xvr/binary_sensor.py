"""Binary sensor platform for Herospeed XVR."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from email.parser import BytesParser
from email.policy import default

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_HOST,
    CONF_MOTION_RESET_DELAY,
    CONF_NUM_SENSORS,
    CONF_PORT,
    CONF_SMTP_PASSWORD,
    CONF_SMTP_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SMTP_IDLE_TIMEOUT = 120


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Herospeed XVR binary sensors."""
    host = config_entry.data[CONF_HOST]
    port = config_entry.data[CONF_PORT]
    num_sensors = config_entry.data[CONF_NUM_SENSORS]
    motion_reset_delay = config_entry.data[CONF_MOTION_RESET_DELAY]
    smtp_username = config_entry.data[CONF_SMTP_USERNAME]
    smtp_password = config_entry.data[CONF_SMTP_PASSWORD]

    sensors = [
        HeropspeedXVRMotionSensor(
            hass=hass,
            host=host,
            channel=channel,
            reset_delay=motion_reset_delay,
        )
        for channel in range(1, num_sensors + 1)
    ]

    async_add_entities(sensors, True)

    # Stop a stale server if the platform is set up again unexpectedly.
    entry_data = hass.data.setdefault(DOMAIN, {}).setdefault(
        config_entry.entry_id, {"smtp_server": None}
    )
    previous_server = entry_data.get("smtp_server")
    if previous_server is not None:
        await previous_server.stop()

    smtp_server = XVRSMTPServer(
        hass=hass,
        config_entry=config_entry,
        host=host,
        port=port,
        sensors=sensors,
        username=smtp_username,
        password=smtp_password,
    )
    await smtp_server.start()
    entry_data["smtp_server"] = smtp_server


class HeropspeedXVRMotionSensor(BinarySensorEntity):
    """Representation of a Herospeed XVR motion sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        channel: int,
        reset_delay: int | float,
    ) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._host = host
        self._channel = channel
        self._reset_delay = reset_delay
        self._cancel_reset_timer: Callable[[], None] | None = None

        self._attr_is_on = False
        self._attr_name = f"XVR Channel {channel} Motion"
        self._attr_unique_id = f"herospeed_xvr_{host}_channel_{channel}"

    async def async_set_motion(self, detected: bool = True) -> None:
        """Handle motion detection."""
        self._attr_is_on = detected
        self.async_write_ha_state()

        if self._cancel_reset_timer is not None:
            self._cancel_reset_timer()
            self._cancel_reset_timer = None

        if detected:
            self._cancel_reset_timer = async_call_later(
                self._hass,
                self._reset_delay,
                self._async_reset_motion,
            )

    async def _async_reset_motion(self, _now=None) -> None:
        """Reset motion state after the configured delay."""
        self._cancel_reset_timer = None
        await self.async_set_motion(False)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the pending reset timer when the entity is removed."""
        if self._cancel_reset_timer is not None:
            self._cancel_reset_timer()
            self._cancel_reset_timer = None


class XVRSMTPServer:
    """Simple SMTP server to receive XVR alerts."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        host: str,
        port: int,
        sensors: list[HeropspeedXVRMotionSensor],
        username: str,
        password: str,
    ) -> None:
        """Initialize the SMTP server."""
        self.hass = hass
        self.config_entry = config_entry
        self.host = host
        self.port = port
        self.sensors = {sensor._channel: sensor for sensor in sensors}
        self.username = username
        self.password = password

        self.server: asyncio.AbstractServer | None = None
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._stopping = False

    async def start(self) -> None:
        """Start the SMTP server."""
        if self.server is not None:
            return

        self._stopping = False

        def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            if self._stopping:
                writer.close()
                return

            task = self.config_entry.async_create_background_task(
                self.hass,
                self.handle_smtp(reader, writer),
                name=f"{DOMAIN} SMTP connection",
            )
            self._connection_tasks.add(task)
            task.add_done_callback(self._connection_tasks.discard)

        self.server = await asyncio.start_server(
            handle_connection,
            host="0.0.0.0",
            port=self.port,
        )

        _LOGGER.info("XVR SMTP server started on port %s", self.port)

    async def stop(self) -> None:
        """Stop the SMTP server and all active SMTP connections."""
        if self._stopping:
            return

        self._stopping = True

        server = self.server
        self.server = None
        if server is not None:
            server.close()
            await server.wait_closed()

        # Close active transports first so pending reader.readline() calls wake up.
        writers = list(self._writers)
        for writer in writers:
            if not writer.is_closing():
                writer.close()

        if writers:
            await asyncio.gather(
                *(self._wait_writer_closed(writer) for writer in writers),
                return_exceptions=True,
            )

        # Explicitly cancel and await all connection tasks. This guarantees that
        # no handle_smtp() coroutine remains pending when HA unloads the entry.
        tasks = [task for task in self._connection_tasks if not task.done()]
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._connection_tasks.clear()
        self._writers.clear()

        _LOGGER.info("XVR SMTP server stopped")

    async def handle_smtp(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one SMTP connection."""
        authenticated = False
        peername = writer.get_extra_info("peername")
        self._writers.add(writer)

        try:
            writer.write(b"220 Welcome to Home Assistant XVR SMTP Server\r\n")
            await writer.drain()

            while not self._stopping:
                data = await self._readline(reader)
                if not data:
                    break

                line = data.decode(errors="replace").strip()
                upper_line = line.upper()

                if upper_line.startswith("HELO") or upper_line.startswith("EHLO"):
                    writer.write(b"250-Hello\r\n")
                    writer.write(b"250-AUTH LOGIN PLAIN\r\n")
                    writer.write(b"250 HELP\r\n")

                elif upper_line == "QUIT":
                    writer.write(b"221 Bye\r\n")
                    await writer.drain()
                    break

                elif upper_line.startswith("AUTH LOGIN"):
                    writer.write(b"334 VXNlcm5hbWU6\r\n")
                    await writer.drain()
                    username_b64 = await self._readline(reader)
                    if not username_b64:
                        break
                    username = base64.b64decode(username_b64.strip()).decode()

                    writer.write(b"334 UGFzc3dvcmQ6\r\n")
                    await writer.drain()
                    password_b64 = await self._readline(reader)
                    if not password_b64:
                        break
                    password = base64.b64decode(password_b64.strip()).decode()

                    if username == self.username and password == self.password:
                        authenticated = True
                        writer.write(b"235 Authentication successful\r\n")
                    else:
                        authenticated = False
                        writer.write(b"535 Authentication failed\r\n")

                elif upper_line.startswith("AUTH PLAIN"):
                    parts = line.split(" ", 2)
                    if len(parts) > 2 and parts[2]:
                        auth_data = parts[2].encode()
                    else:
                        writer.write(b"334 \r\n")
                        await writer.drain()
                        auth_data = await self._readline(reader)
                        if not auth_data:
                            break

                    auth_string = base64.b64decode(auth_data.strip()).decode()
                    _, username, password = auth_string.split("\0", 2)

                    if username == self.username and password == self.password:
                        authenticated = True
                        writer.write(b"235 Authentication successful\r\n")
                    else:
                        authenticated = False
                        writer.write(b"535 Authentication failed\r\n")

                elif not authenticated:
                    writer.write(b"530 Authentication required\r\n")

                elif upper_line.startswith("MAIL FROM:"):
                    writer.write(b"250 Ok\r\n")

                elif upper_line.startswith("RCPT TO:"):
                    writer.write(b"250 Ok\r\n")

                elif upper_line == "DATA":
                    writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                    await writer.drain()

                    email_data: list[bytes] = []
                    while not self._stopping:
                        data_line = await self._readline(reader)
                        if not data_line:
                            break
                        if data_line.strip() == b".":
                            break
                        email_data.append(data_line)

                    if self._stopping:
                        break

                    if not data_line:
                        break

                    await self.process_email(b"".join(email_data))
                    writer.write(b"250 Ok: queued\r\n")

                else:
                    writer.write(b"250 Ok\r\n")

                await writer.drain()

        except TimeoutError:
            _LOGGER.debug(
                "SMTP connection from %s closed after %s seconds of inactivity",
                peername,
                SMTP_IDLE_TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError):
            _LOGGER.debug("SMTP connection from %s was closed", peername)
        except Exception:
            _LOGGER.exception("SMTP error for connection from %s", peername)
        finally:
            self._writers.discard(writer)
            if not writer.is_closing():
                writer.close()
            await self._wait_writer_closed(writer)

    async def _readline(self, reader: asyncio.StreamReader) -> bytes:
        """Read one SMTP line with an inactivity timeout."""
        return await asyncio.wait_for(reader.readline(), timeout=SMTP_IDLE_TIMEOUT)

    @staticmethod
    async def _wait_writer_closed(writer: asyncio.StreamWriter) -> None:
        """Wait for a stream writer to close without leaking shutdown errors."""
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass

    async def process_email(self, email_data: bytes) -> None:
        """Process received email data."""
        try:
            msg = BytesParser(policy=default).parsebytes(email_data)

            subject = msg["subject"]
            if not subject:
                return

            if "Motion" in subject and "Channel" in subject:
                try:
                    channel = int(subject.split("Channel")[-1].strip())
                except ValueError:
                    _LOGGER.error(
                        "Could not parse channel number from subject: %s", subject
                    )
                    return

                sensor = self.sensors.get(channel)
                if sensor is not None:
                    await sensor.async_set_motion(True)
                    _LOGGER.info("Motion detected on channel %s", channel)

        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error processing XVR email")
