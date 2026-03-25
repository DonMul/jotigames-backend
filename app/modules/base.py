from abc import ABC, abstractmethod

from fastapi import APIRouter


class ApiModule(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Return stable module identifier used for registration and diagnostics."""
        raise NotImplementedError

    @abstractmethod
    def build_router(self) -> APIRouter:
        """Construct and return FastAPI router exposing module endpoints."""
        raise NotImplementedError
