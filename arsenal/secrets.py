"""Deprecated compatibility names for the generic persistent input store."""
from ..inputs import InputError, InputStore

ArsenalEnvError = InputError
SecretStore = InputStore

__all__ = ["ArsenalEnvError", "SecretStore"]
