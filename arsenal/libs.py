"""Lazy typed library adapters for a llama.cpp server."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from importlib import import_module
import os
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import dspy
    import guidance.models
    import httpx
    import instructor
    import litellm
    import openai
    import outlines.models.openai
    import smolagents
    from llama_index.llms.openai_like import OpenAILike
    from pydantic_ai.models.openai import OpenAIChatModel

__all__ = ["LibDependencyError", "Libs"]


class LibDependencyError(ImportError):
    """The requested integration cannot be created without a Python package."""


@dataclass(frozen=True)
class _Config:
    server_url: str
    openai_url: str
    model: str | None
    context_window: int | None
    api_key: str
    timeout: float

    def require_model(self) -> str:
        if not self.model:
            raise ValueError("Model name is not set: pass model to the Libs constructor")
        return self.model


class _Adapter:
    _path = "assistant.clients"

    def __init__(self, config: _Config) -> None:
        self._config = config

    def _module(self, module: str, package: str | None = None) -> Any:
        try:
            return import_module(module)
        except (ImportError, OSError) as error:
            required = package or module.split(".", 1)[0]
            raise LibDependencyError(
                f"{self._path} requires package {required!r} "
                "in the project Python interpreter."
            ) from error


class OpenAILib(_Adapter):
    _path = "assistant.clients.openai.client"
    @cached_property
    def client(self) -> "openai.OpenAI":
        module = self._module("openai")
        return module.OpenAI(base_url=self._config.openai_url, api_key=self._config.api_key, timeout=self._config.timeout)


class LiteLLMLib(_Adapter):
    _path = "assistant.clients.litellm.router"
    @cached_property
    def router(self) -> "litellm.Router":
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        module = self._module("litellm")
        model = self._config.require_model()
        params = {"model": f"openai/{model}", "api_base": self._config.openai_url, "api_key": self._config.api_key, "timeout": self._config.timeout}
        costs = {"input_cost_per_token": 0.0, "output_cost_per_token": 0.0, "cache_creation_input_token_cost": 0.0, "cache_read_input_token_cost": 0.0}
        module.register_model(model_cost={params["model"]: costs})
        return module.Router(model_list=[{"model_name": model, "litellm_params": params, "model_info": costs}])


class DSPyLib(_Adapter):
    _path = "assistant.clients.dspy.model"
    @cached_property
    def model(self) -> "dspy.LM":
        module = self._module("dspy", "dspy-ai")
        return module.LM(f"openai/{self._config.require_model()}", api_base=self._config.openai_url, api_key=self._config.api_key, timeout=self._config.timeout)


class InstructorLib(_Adapter):
    _path = "assistant.clients.instructor.client"
    def __init__(self, config: _Config, openai: OpenAILib) -> None:
        super().__init__(config)
        self._openai = openai

    @cached_property
    def client(self) -> "instructor.Instructor":
        module = self._module("instructor")
        return module.from_openai(self._openai.client)


class PydanticAILib(_Adapter):
    _path = "assistant.clients.pydantic_ai.model"
    @cached_property
    def model(self) -> "OpenAIChatModel":
        provider_module = self._module("pydantic_ai.providers.openai", "pydantic-ai")
        model_module = self._module("pydantic_ai.models.openai", "pydantic-ai")
        openai = self._module("openai")
        client = openai.AsyncOpenAI(base_url=self._config.openai_url, api_key=self._config.api_key, timeout=self._config.timeout)
        provider = provider_module.OpenAIProvider(openai_client=client)
        model_type = getattr(model_module, "OpenAIModel", None) or getattr(model_module, "OpenAIChatModel", None)
        if model_type is None:
            raise LibDependencyError("Package 'pydantic-ai' does not provide OpenAIModel or OpenAIChatModel.")
        return model_type(self._config.require_model(), provider=provider)


class SmolagentsLib(_Adapter):
    _path = "assistant.clients.smolagents.model"
    @cached_property
    def model(self) -> "smolagents.OpenAIServerModel":
        module = self._module("smolagents")
        return module.OpenAIServerModel(model_id=self._config.require_model(), api_base=self._config.openai_url, api_key=self._config.api_key, client_kwargs={"timeout": self._config.timeout})


class LlamaIndexLib(_Adapter):
    _path = "assistant.clients.llama_index.model"
    @cached_property
    def model(self) -> "OpenAILike":
        module = self._module("llama_index.llms.openai_like", "llama-index-llms-openai-like")
        return module.OpenAILike(model=self._config.require_model(), api_base=self._config.openai_url, api_key=self._config.api_key, timeout=self._config.timeout, context_window=self._config.context_window or 3900, is_chat_model=True, is_function_calling_model=False)


class HTTPXLib(_Adapter):
    _path = "assistant.clients.httpx.client"
    @cached_property
    def client(self) -> "httpx.Client":
        module = self._module("httpx")
        return module.Client(base_url=self._config.server_url, timeout=self._config.timeout)


class OutlinesLib(_Adapter):
    _path = "assistant.clients.outlines.model"
    def __init__(self, config: _Config, openai: OpenAILib) -> None:
        super().__init__(config)
        self._openai = openai

    @cached_property
    def model(self) -> "outlines.models.openai.OpenAI":
        module = self._module("outlines.models.openai", "outlines")
        return module.from_openai(self._openai.client, self._config.require_model())


class GuidanceLib(_Adapter):
    _path = "assistant.clients.guidance.model"
    @cached_property
    def model(self) -> "guidance.models.OpenAI":
        module = self._module("guidance.models", "guidance")
        return module.OpenAI(self._config.require_model(), echo=False, base_url=self._config.openai_url, api_key=self._config.api_key, timeout=self._config.timeout)


class Libs:
    """Ten library integrations with explicit client/router/model roles."""

    names: Final[tuple[str, ...]] = ("openai", "litellm", "dspy", "instructor", "pydantic_ai", "smolagents", "llama_index", "httpx", "outlines", "guidance")

    def __init__(self, base_url: str, *, model: str | None = None, context_window: int | None = None, api_key: str = "llama.cpp", timeout: float = 60.0) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if context_window is not None and context_window <= 0:
            raise ValueError("context_window must be greater than zero")
        server_url = base_url.strip().rstrip("/")
        if server_url.endswith("/v1"):
            server_url = server_url[:-3].rstrip("/")
        self.server_url = server_url
        self.openai_url = f"{server_url}/v1"
        self.model = model
        self.context_window = context_window
        self.api_key = api_key
        self.timeout = timeout
        config = _Config(server_url, self.openai_url, model, context_window, api_key, timeout)
        self.openai = OpenAILib(config)
        self.litellm = LiteLLMLib(config)
        self.dspy = DSPyLib(config)
        self.instructor = InstructorLib(config, self.openai)
        self.pydantic_ai = PydanticAILib(config)
        self.smolagents = SmolagentsLib(config)
        self.llama_index = LlamaIndexLib(config)
        self.httpx = HTTPXLib(config)
        self.outlines = OutlinesLib(config, self.openai)
        self.guidance = GuidanceLib(config)
