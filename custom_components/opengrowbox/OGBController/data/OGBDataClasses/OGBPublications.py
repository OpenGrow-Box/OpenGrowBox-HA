import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Optional, Union

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
    Timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    )

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class OGBWaterPublication:
    Name: str
    ecCurrent: Optional[float] = None
    tdsCurrent: Optional[float] = None
    phCurrent: Optional[float] = None
    oxiCurrent: Optional[float] = None
    salCurrent: Optional[float] = None
    waterTemp: Optional[float] = None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class OGBSoilPublication:
    Name: str
    ecCurrent: Optional[float] = None
    moistCurrent: Optional[float] = None
    phCurrent: Optional[float] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class OGBMoisturePublication:
    Name: str
    MoistureValues: list
    AvgMoisture: float | None = None


@dataclass
class OGBDLIPublication:
    Name: str
    DLI: int


@dataclass
class OGBPPFDPublication:
    Name: str
    PPFD: int


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
    priority: str


@dataclass(frozen=True)
class OGBWeightPublication:
    Name: str
    message: str
    tempDeviation: float
    humDeviation: float
    tempWeight: float
    humWeight: float


@dataclass(frozen=True)
class OGBHydroPublication:
    Name: str
    Mode: str
    Cycle: bool
    Active: bool
    Intervall: float
    Duration: float
    Message: str
    Devices: List[str]


@dataclass(frozen=True)
class OGBRetrivePublication:
    Name: str
    Active: bool
    Cycle: bool
    Mode: bool
    Intervall: float
    Duration: float
    Message: str
    Devices: List[str]


@dataclass(frozen=True)
class OGBCropSteeringPublication:
    Name: str
    Active: bool
    Mode: str
    Message: str
    SoilMaxMoisture: float
    SoilMinMoisture: float
    PlantPhase: str
    GenerativeWeek: int
    Devices: List[str]


@dataclass(frozen=True)
class OGBECAction:
    Name: str
    TargetEC: str
    CurrentEC: str


@dataclass(frozen=True)
class OGBDripperAction:
    Name: str
    Device: str
    Action: str


@dataclass(frozen=True)
class OGBRetrieveAction:
    Name: str
    Device: str
    Cycle: str
    Action: str


@dataclass(frozen=True)
class OGBHydroAction:
    Name: str
    Device: str
    Cycle: str
    Action: str


@dataclass(frozen=True)
class OGBWaterAction:
    Name: str
    Device: str
    Cycle: str
    Action: str
    Message: str


@dataclass(frozen=True)
class OGBLightAction:
    Name: str
    Device: str
    Voltage: int
    Dimmable: bool
    Type: str
    Action: str
    Message: str
    SunRise: bool
    SunSet: bool


@dataclass(frozen=True)
class OGBPremPublication:
    Name: str
    UserID: str
    Plan: str
    ValidUntil: bool
    Active: bool
    Message: str


@dataclass
class OGBMediumPlantPublication:
    """Publication for per-medium plant data - emitted as array for UI"""
    Name: str  # Room name
    plants: List[dict]  # Array of plant info per medium
    Timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    )

    def to_dict(self):
        return {
            "Name": self.Name,
            "plants": self.plants,
            "Timestamp": self.Timestamp,
        }


@dataclass
class OGBPlantDatesPublication:
    """Publication for single medium plant dates update"""
    Name: str  # Room name
    medium_name: str
    plant_name: str
    breeder_name: str
    plant_type: str
    plant_stage: str
    current_phase: str
    grow_start_date: Optional[str] = None
    bloom_switch_date: Optional[str] = None
    breeder_bloom_days: int = 0
    total_grow_days: int = 0
    veg_days: int = 0
    bloom_days: int = 0
    veg_week: int = 0
    bloom_week: int = 0
    days_to_harvest: Optional[int] = None
    estimated_harvest: Optional[str] = None
    dli_target: float = 0
    ppfd_target: int = 0
    light_hours: int = 0
    Timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    )

    def to_dict(self):
        return asdict(self)
