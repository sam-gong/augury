"""Strategy implementations. Each module exports a Strategy subclass."""
from ._base import Strategy, OverlayLine
from .sma_band import SmaBand
from .sma_cross import SmaCross
from .hybrid import HybridStrategy
from .thermo_band import ThermoBand, BALANCED_PARAMS
from .cross_asset import CrossAssetSmaBand

__all__ = ["Strategy", "OverlayLine", "SmaBand", "SmaCross",
           "HybridStrategy", "ThermoBand", "BALANCED_PARAMS",
           "CrossAssetSmaBand"]
