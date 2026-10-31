"""The pipeline's provider seam: adapters are built on first use, so a missing key fails only
the stage that needs it (never the upload, nor a silent answer that needs no call)."""

from typing import Annotated, Protocol

from fastapi import Depends

from app.config import Settings, get_settings
from app.features.providers import registry
from app.features.providers.base import TextGenerator, Transcriber
from app.features.providers.usage import UsageRecorder, get_durable_usage_recorder

SettingsDep = Annotated[Settings, Depends(get_settings)]
RecorderDep = Annotated[UsageRecorder, Depends(get_durable_usage_recorder)]


class PipelineProviders(Protocol):
    def transcriber(self) -> Transcriber: ...  # may raise ProviderError

    def assessor(self) -> TextGenerator: ...  # may raise ProviderError

    def follow_up_generator(self) -> TextGenerator: ...  # may raise ProviderError


class RegistryProviders:
    def __init__(self, settings: Settings, recorder: UsageRecorder) -> None:
        self.settings = settings
        self.recorder = recorder
        self._transcriber: Transcriber | None = None
        self._assessor: TextGenerator | None = None
        self._follow_up: TextGenerator | None = None

    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            self._transcriber = registry.get_transcriber(self.settings, self.recorder)
        return self._transcriber

    def assessor(self) -> TextGenerator:
        if self._assessor is None:
            self._assessor = registry.get_text_generator(self.settings, "assessment", self.recorder)
        return self._assessor

    def follow_up_generator(self) -> TextGenerator:
        if self._follow_up is None:
            self._follow_up = registry.get_text_generator(
                self.settings, "generation", self.recorder
            )
        return self._follow_up


def get_pipeline_providers(settings: SettingsDep, recorder: RecorderDep) -> PipelineProviders:
    """FastAPI dependency; never raises (nothing is built yet)."""
    return RegistryProviders(settings, recorder)
