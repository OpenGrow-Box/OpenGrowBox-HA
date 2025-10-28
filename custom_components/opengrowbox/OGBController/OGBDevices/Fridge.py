
import logging
import asyncio


_LOGGER = logging.getLogger(__name__)

class Fridge():
    def __init__(self, deviceName, deviceData, eventManager, dataStore, deviceType, inRoom, hass=None, deviceLabel="EMPTY", allLabels=[]):
        self.hass = hass
        self.eventManager = eventManager
        self.dataStore = dataStore
        self.deviceName = deviceName
        self.deviceType = deviceType
        self.inRoom = inRoom
        self.deviceData = deviceData
        self.deviceLabel = deviceLabel


        self.devicePlatform = None
        self.sensorMap = None
        self.labelMap = allLabels

        


    def __repr__(self):
        pass
    
    def __str__(self):
        pass