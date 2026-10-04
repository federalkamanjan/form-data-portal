"""Mapper: narrows each raw table to the configured fields."""
from .mapper import apply_mapping, confirm_choices, detect_mapping, propose_mapping, result_from_columns
from .models import FieldMatch, MappingError, MappingResult, MappingStatus
from .store import MappingStore

__all__ = [
    "apply_mapping", "confirm_choices", "detect_mapping", "propose_mapping", "result_from_columns",
    "FieldMatch", "MappingError", "MappingResult", "MappingStatus", "MappingStore",
]
