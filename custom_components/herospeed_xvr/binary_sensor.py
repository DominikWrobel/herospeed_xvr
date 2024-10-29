"""Binary sensor platform for Herospeed XVR."""
import asyncio
import json
import logging
import base64
from datetime import datetime, timedelta
import email
from email.parser import BytesParser
from email.policy import default

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_NUM_SENSORS,
    CONF_MOTION_RESET_DELAY,
    CONF_SMTP_USERNAME,
    CONF_SMTP_PASSWORD,
)

_LOGGER = logging.getLogger(__name__)

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

    sensors = []
    for channel in range(1, num_sensors + 1):
        sensors.append(HeropspeedXVRMotionSensor(
            hass,
            host,
            channel,
            motion_reset_delay
        ))

    async_add_entities(sensors, True)

    # Start SMTP server
    smtp_server = XVRSMTPServer(hass, host, port, sensors, smtp_username, smtp_password)
    await smtp_server.start()

class HeropspeedXVRMotionSensor(BinarySensorEntity):
    """Representation of a Herospeed XVR Motion Sensor."""

    def __init__(self, hass, host, channel, reset_delay):
        """Initialize the sensor."""
        self._hass = hass
        self._host = host
        self._channel = channel
        self._reset_delay = reset_delay
        self._attr_is_on = False
        self._reset_timer = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"XVR Channel {self._channel} Motion"

    @property
    def unique_id(self):
        """Return a unique ID."""
        return f"herospeed_xvr_{self._host}_channel_{self._channel}"

    @property
    def device_class(self):
        """Return the class of this device."""
        return BinarySensorDeviceClass.MOTION

    async def async_set_motion(self, detected: bool = True):
        """Handle motion detection."""
        self._attr_is_on = detected
        self.async_write_ha_state()

        if self._reset_timer:
            self._reset_timer.cancel()

        if detected:
            self._reset_timer = self._hass.loop.call_later(
                self._reset_delay,
                lambda: asyncio.create_task(self.async_set_motion(False))
            )

class XVRSMTPServer:
    """Simple SMTP server to receive XVR alerts."""

    def __init__(self, hass, host, port, sensors, username, password):
        """Initialize the SMTP server."""
        self.hass = hass
        self.host = host
        self.port = port
        self.sensors = {sensor._channel: sensor for sensor in sensors}
        self.username = username
        self.password = password
        self.server = None
        self.authenticated = False

    async def start(self):
        """Start the SMTP server."""
        loop = asyncio.get_event_loop()
        
        def handle_connection(reader, writer):
            asyncio.create_task(self.handle_smtp(reader, writer))
            
        self.server = await asyncio.start_server(
            handle_connection,
            host='0.0.0.0',  # Listen on all interfaces
            port=self.port
        )
        
        _LOGGER.info(f"XVR SMTP server started on port {self.port}")

    async def handle_smtp(self, reader, writer):
        """Handle SMTP connection."""
        try:
            # Send greeting
            writer.write(b'220 Welcome to Home Assistant XVR SMTP Server\r\n')
            await writer.drain()

            self.authenticated = False
            while True:
                data = await reader.readline()
                if not data:
                    break

                line = data.decode().strip()
                upper_line = line.upper()
                
                if upper_line.startswith('HELO') or upper_line.startswith('EHLO'):
                    writer.write(b'250-Hello\r\n')
                    writer.write(b'250-AUTH LOGIN PLAIN\r\n')
                    writer.write(b'250 HELP\r\n')
                elif upper_line.startswith('AUTH LOGIN'):
                    writer.write(b'334 VXNlcm5hbWU6\r\n')  # Base64 for "Username:"
                    username_b64 = await reader.readline()
                    username = base64.b64decode(username_b64.strip()).decode()
                    
                    writer.write(b'334 UGFzc3dvcmQ6\r\n')  # Base64 for "Password:"
                    password_b64 = await reader.readline()
                    password = base64.b64decode(password_b64.strip()).decode()
                    
                    if username == self.username and password == self.password:
                        self.authenticated = True
                        writer.write(b'235 Authentication successful\r\n')
                    else:
                        writer.write(b'535 Authentication failed\r\n')
                elif upper_line.startswith('AUTH PLAIN'):
                    if len(line.split(' ')) > 2:
                        # Auth string is provided in the command
                        auth_string = base64.b64decode(line.split(' ')[2]).decode()
                        _, username, password = auth_string.split('\0')
                    else:
                        # Auth string is provided in the next line
                        writer.write(b'334 \r\n')
                        auth_data = await reader.readline()
                        auth_string = base64.b64decode(auth_data.strip()).decode()
                        _, username, password = auth_string.split('\0')
                    
                    if username == self.username and password == self.password:
                        self.authenticated = True
                        writer.write(b'235 Authentication successful\r\n')
                    else:
                        writer.write(b'535 Authentication failed\r\n')
                elif not self.authenticated:
                    writer.write(b'530 Authentication required\r\n')
                elif upper_line.startswith('MAIL FROM:'):
                    writer.write(b'250 Ok\r\n')
                elif upper_line.startswith('RCPT TO:'):
                    writer.write(b'250 Ok\r\n')
                elif upper_line == 'DATA':
                    writer.write(b'354 End data with <CR><LF>.<CR><LF>\r\n')
                    email_data = []
                    while True:
                        line = await reader.readline()
                        if line.strip() == b'.':
                            break
                        email_data.append(line)
                    
                    # Process the email
                    await self.process_email(b''.join(email_data))
                    writer.write(b'250 Ok: queued\r\n')
                elif upper_line == 'QUIT':
                    writer.write(b'221 Bye\r\n')
                    break
                else:
                    writer.write(b'250 Ok\r\n')

                await writer.drain()

        except Exception as e:
            _LOGGER.error(f"SMTP Error: {str(e)}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def process_email(self, email_data):
        """Process received email data."""
        try:
            parser = BytesParser(policy=default)
            msg = parser.parsebytes(email_data)
            
            # Parse subject for motion detection
            subject = msg['subject']
            if not subject:
                return
            
            if 'Motion' in subject and 'Channel' in subject:
                # Extract channel number
                try:
                    channel = int(subject.split('Channel')[-1].strip())
                    if channel in self.sensors:
                        await self.sensors[channel].async_set_motion(True)
                        _LOGGER.info(f"Motion detected on channel {channel}")
                except ValueError:
                    _LOGGER.error(f"Could not parse channel number from subject: {subject}")
            
        except Exception as e:
            _LOGGER.error(f"Error processing email: {str(e)}")
