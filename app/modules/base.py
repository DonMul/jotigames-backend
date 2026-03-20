from abc import ABC, abstractmethod

from fastapi import APIRouter


class ApiModule(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def build_router(self) -> APIRouter:
        raise NotImplementedError
