"""Shared data and errors used by every vendor connector."""
from dataclasses import dataclass


class ConnectorNotConfigured(Exception):
    pass


class ConnectorLoginError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(repr=False)
class Credentials:
    username: str
    password: str


@dataclass(frozen=True)
class SourceRecord:
    source_key: str
    vendor_sku: str | None
    attributes: dict
    source_url: str | None = None
