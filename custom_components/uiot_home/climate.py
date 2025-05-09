"""Switch platform for UIOT integration."""

import json
import logging

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .uiot_api.const import COMPANY, DOMAIN
from .uiot_api.uiot_device import UIOTDevice, is_entity_exist
from typing import Any

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities) -> None:
    """Set up the Switch platform from a config entry."""
    _LOGGER.debug("async_setup_entry climate")

    devices_data = hass.data[DOMAIN].get("devices", [])

    device_data = []
    for device in devices_data:
        if device.get("type") == "climate":
            _LOGGER.debug("climate")
            device_data.append(device)

    entities = []
    for climate_data in device_data:
        name = climate_data.get("deviceName", "")
        deviceId = climate_data.get("deviceId", "")
        _LOGGER.debug("name:%s", name)
        _LOGGER.debug("deviceId:%d", deviceId)
        uiot_dev: UIOTDevice = hass.data[DOMAIN].get("uiot_dev")
        entities.append(SmartAC(climate_data, uiot_dev, hass))

    async_add_entities(entities)

    @callback
    def handle_config_update(msg):
        if hass is None:
            return
        try:
            devices_data = msg
            device_data = []
            for device in devices_data:
                if device.get("type") == "climate":
                    _LOGGER.debug("climate")
                    _LOGGER.debug("devices_data %s", devices_data)
                    device_data.append(device)

            new_entities = []

            for climate_data in device_data:
                name = climate_data.get("deviceName", "")
                deviceId = climate_data.get("deviceId", "")
                _LOGGER.debug("name:%s", name)
                _LOGGER.debug("deviceId:%d", deviceId)
                uiot_dev: UIOTDevice = hass.data[DOMAIN].get("uiot_dev")
                if not is_entity_exist(hass, deviceId):
                    new_entities.append(SmartAC(climate_data, uiot_dev, hass))

            if new_entities:
                async_add_entities(new_entities)

        except Exception as e:
            _LOGGER.error("Error processing config update: %s", e)
            raise

    signal = "mqtt_message_network_report"
    async_dispatcher_connect(hass, signal, handle_config_update)


def get_device_hvac_model(mode, power):
    if not power:
        return HVACMode.OFF
    elif mode == "cool":
        return HVACMode.COOL
    elif mode == "heat":
        return HVACMode.HEAT
    elif mode == "fan":
        return HVACMode.FAN_ONLY
    elif mode == "dehumidification":
        return HVACMode.DRY
    elif mode == "auto":  # Add AUTO mode support
        return HVACMode.AUTO
    else:
        return HVACMode.OFF


def get_device_fan_model(mode):
    if mode == "low":
        return "low"
    elif mode == "mid":
        return "medium"
    elif mode == "high":
        return "high"
    else:
        return "low"


class SmartAC(ClimateEntity):
    """Representation of a UIOT home Switch."""

    def __init__(self, climate_data, uiot_dev, hass: HomeAssistant) -> None:
        """Initialize the switch."""
        self.hass = hass
        self._uiot_dev: UIOTDevice = uiot_dev
        self._attr_min_temp = 16
        self._attr_max_temp = 32
        self._attr_target_temperature_step = 1
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = (
            ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TARGET_TEMPERATURE
        )
        self._attr_hvac_modes = [HVACMode.COOL, HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.DRY, HVACMode.OFF, HVACMode.AUTO]
        self._attr_fan_modes = ["low", "medium", "high"]
        self._attr_name = climate_data.get("deviceName", "")
        self._attr_unique_id = str(climate_data.get("deviceId", ""))
        self.mac = climate_data.get("deviceMac", "")
        properties_data = climate_data.get("properties", "")
        if properties_data:
            self._attr_is_on = properties_data.get("powerSwitch", "") != "off"
            self._attr_target_temperature = properties_data.get("targetTemperature", 22)
            self._attr_hvac_mode = get_device_hvac_model(properties_data.get("thermostatMode", ""), self._attr_is_on)
            self._attr_fan_mode = get_device_fan_model(properties_data.get("windSpeed", ""))

        if climate_data.get("deviceOnlineState", "") == 0:
            self._attr_available = False
        else:
            self._attr_available = True
        _LOGGER.debug("_attr_available=%d", self._attr_available)

        self._attr_device_info = {
            "identifiers": {(f"{DOMAIN}", f"{self.mac}_{self._attr_unique_id}")},
            "name": f"{climate_data.get('deviceName', '')}",
            "manufacturer": f"{COMPANY}",
            "suggested_area": f"{climate_data.get('roomName', '')}",
            "model": f"{climate_data.get('model', '')}",
            "sw_version": f"{climate_data.get('softwareVersion', '')}",
            "hw_version": f"{climate_data.get('hardwareVersion', '')}",
        }
        _LOGGER.debug("初始化设备: %s", self._attr_name)
        _LOGGER.debug("deviceId=%s", self._attr_unique_id)
        _LOGGER.debug("mac=%s", self.mac)

        # 订阅状态主题以监听本地控制的变化
        signal = "mqtt_message_received_state_report"
        async_dispatcher_connect(hass, signal, self._handle_mqtt_message)

    @callback
    def _handle_mqtt_message(self, msg):
        """Handle incoming MQTT messages for state updates."""
        if self.hass is None:
            return
        msg_data = json.loads(msg.payload)

        if "online_report" in msg.topic:
            data = msg_data.get("data")
            devices_data = data.get("deviceList")
            for d in devices_data:
                deviceId = d.get("deviceId", "")
                netState = d.get("netState", "")
                if str(deviceId) == self._attr_unique_id:
                    _LOGGER.debug("设备在线状态变化 deviceId: %d,netState:%d", deviceId, netState)
                    self._attr_available = netState != 0
                    self.async_write_ha_state()
            return

        try:
            data = msg_data.get("data", "")
            if self._attr_unique_id == str(data.get("deviceId", "")):
                payload_str = data.get("properties", "")
            else:
                return
        except UnicodeDecodeError as e:
            _LOGGER.error("Failed to decode message payload: %s", e)
            return

        if not payload_str:
            _LOGGER.warning("Received empty payload")
            return

        _LOGGER.debug("收到设备状态更新: %s", payload_str)

        if payload_str.get("powerSwitch", ""):
            self._attr_is_on = payload_str.get("powerSwitch", "") == "on"

        if payload_str.get("targetTemperature", ""):
            self._attr_current_temperature = payload_str.get("targetTemperature", 25)

        if payload_str.get("windSpeed", ""):
            self._attr_fan_mode = get_device_fan_model(payload_str.get("windSpeed", ""))

        if payload_str.get("thermostatMode", ""):
            self._attr_hvac_mode = get_device_hvac_model(payload_str.get("thermostatMode", ""), self._attr_is_on)

        self._attr_available = data.get("deviceOnlineState", 1) != 0
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        self._attr_hvac_mode = hvac_mode

        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        if not self._attr_is_on:
            await self.async_turn_on()
        msg_data = {"thermostatMode": hvac_mode.lower()}
        _LOGGER.debug("msg_data:%s", msg_data)
        await self._uiot_dev.dev_control_real(self._attr_unique_id, msg_data)
        self.async_write_ha_state()
