"""State-transition invariants for the E-Ambulance DES."""
from dataclasses import dataclass
from enum import Enum

class VehicleState(str, Enum):
    AVAILABLE="available"; ON_MISSION="on_mission"; CHARGING="charging"; MAINTENANCE="maintenance"

@dataclass
class VehicleRuntime:
    vehicle_id:str
    state:VehicleState
    soc_pct:float
    available_at:str

def validate_soc(soc:float)->None:
    if not 0 <= soc <= 100:
        raise ValueError(f"SOC outside [0,100]: {soc}")

def charging_completed(before:float, after:float)->None:
    validate_soc(before); validate_soc(after)
    if after < before:
        raise ValueError("charging_completed requires SOC_after >= SOC_before")

def dispatch_allowed(v:VehicleRuntime)->bool:
    validate_soc(v.soc_pct)
    return v.state == VehicleState.AVAILABLE

def transition(v:VehicleRuntime,event:str,soc_after:float|None=None)->VehicleRuntime:
    if soc_after is not None: validate_soc(soc_after)
    if event=="dispatched":
        if v.state != VehicleState.AVAILABLE: raise ValueError("Only available vehicles can be dispatched")
        return VehicleRuntime(v.vehicle_id,VehicleState.ON_MISSION,v.soc_pct,v.available_at)
    if event=="handover_complete":
        return VehicleRuntime(v.vehicle_id,VehicleState.AVAILABLE,soc_after if soc_after is not None else v.soc_pct,v.available_at)
    if event=="charging_start":
        if v.state not in (VehicleState.AVAILABLE,VehicleState.ON_MISSION): raise ValueError("Invalid charging transition")
        return VehicleRuntime(v.vehicle_id,VehicleState.CHARGING,v.soc_pct,v.available_at)
    if event=="charging_completed":
        new=v.soc_pct if soc_after is None else soc_after
        charging_completed(v.soc_pct,new)
        return VehicleRuntime(v.vehicle_id,VehicleState.AVAILABLE,new,v.available_at)
    raise ValueError(f"Unknown event: {event}")
