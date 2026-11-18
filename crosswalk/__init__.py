"""Crosswalks from other metadata standards to the CDIF Discovery profile."""

from .iso19115 import MAPPINGS, CrosswalkError, crosswalk, detect_dialect

__all__ = ["MAPPINGS", "CrosswalkError", "crosswalk", "detect_dialect"]
