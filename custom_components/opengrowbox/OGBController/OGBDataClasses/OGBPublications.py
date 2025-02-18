from dataclasses import dataclass, field
from typing import List, Union, Optional
import logging
from datetime import datetime


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OGBInitData:
    Name: str
    newState: tuple[Union[float, str]] = field(default_factory=list)

@dataclass(frozen=True)
class OGBEventPublication:
    Name: str
    oldState: tuple[Union[float, str]] = field(default_factory=list)
    newState: tuple[Union[float, str]] = field(default_factory=list)

@dataclass(frozen=True)
class OGBDeviceEventPublication:
    Name: str
    oldState: tuple[Union[float, str]] = field(default_factory=list)
    newState: tuple[Union[float, str]] = field(default_factory=list)

@dataclass(frozen=True)
class OGBModePublication:
    currentMode: str
    previousMode: str 
 
@dataclass(frozen=True)
class OGBModeRunPublication:
    currentMode: str 

@dataclass(frozen=True)
class OGBVPDPublication:
    Name: str
    VPD: Optional[float] = None
    AvgTemp: Optional[float] = None
    AvgHum: Optional[float] = None
    AvgDew: Optional[float] = None

    def to_dict(self):
        return asdict(self)
    
    
    
@dataclass(frozen=True)
class OGBCO2Publication:
    Name: str
    co2Current: Optional[float] = None
    co2Target: Optional[float] = None
    minCO2: Optional[float] = None
    maxCO2: Optional[float] = None
    
    def to_dict(self):
        return asdict(self)
    
    
@dataclass(frozen=True)
class OGBActionPublication:
    Name: str
    message: str
    capability: str
    action: str

@dataclass(frozen=True)
class OGBWeightPublication:
    Name: str
    message: str
    tempDeviation: float
    humDeviation: float
    tempWeight:float
    humWeight:float