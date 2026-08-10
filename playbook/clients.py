"""Ленивые фабрики LLM-клиентов для OpenAI API сервера llama.cpp.

Модуль намеренно не импортирует сторонние LLM-библиотеки на верхнем уровне.
Зависимости конкретного клиента импортируются только при обращении к свойству.
"""

from __future__ import annotations

from functools import cached_property
from importlib import import_module
from typing import Any, Final


__all__ = ["ClientDependencyError", "Clients", "LlamaCppClients"]


class ClientDependencyError(ImportError):
    """Запрошенный клиент не может быть создан из-за отсутствующей зависимости."""


class Clients:
    """Фабрика клиентов для одного OpenAI-совместимого сервера llama.cpp.

    Args:
        base_url: Адрес сервера или его OpenAI API. Допустимы как
            ``http://localhost:8080``, так и ``http://localhost:8080/v1``.
        model: Имя модели, которое следует передавать клиентам. Оно обязательно
            для фабрик, создающих готовую модель, но не для низкоуровневых HTTP-
            клиентов.
        context_window: Размер контекстного окна модели в токенах. Используется
            интеграциями, которым неизвестны метаданные локального alias.
        api_key: Ключ OpenAI API. llama.cpp обычно не проверяет ключ, однако
            многие клиенты требуют непустое значение.
        timeout: Стандартный тайм-аут HTTP-запросов в секундах.

    Ни одна сторонняя клиентская библиотека не импортируется конструктором.
    """

    names: Final[tuple[str, ...]] = (
        "openai",
        "litellm",
        "dspy",
        "instructor",
        "pydantic_ai",
        "baml",
        "smolagents",
        "llama_index",
        "httpx",
        "llama_cpp_agent",
        "outlines",
        "guidance",
    )

    def __init__(
        self,
        base_url: str,
        *,
        model: str | None = None,
        context_window: int | None = None,
        api_key: str = "llama.cpp",
        timeout: float = 60.0,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url должен быть непустой строкой")
        if timeout <= 0:
            raise ValueError("timeout должен быть больше нуля")
        if context_window is not None and context_window <= 0:
            raise ValueError("context_window должен быть больше нуля")

        normalized = base_url.strip().rstrip("/")
        if normalized.endswith("/v1"):
            normalized = normalized[:-3].rstrip("/")

        self.server_url = normalized
        self.openai_url = f"{normalized}/v1"
        self.model = model
        self.context_window = context_window
        self.api_key = api_key
        self.timeout = timeout

    @staticmethod
    def _module(module: str, distribution: str | None = None) -> Any:
        """Импортирует зависимость только в момент запроса клиента."""
        try:
            return import_module(module)
        except (ImportError, OSError) as error:
            package = distribution or module.split(".", 1)[0]
            raise ClientDependencyError(
                f"Для этого клиента требуется рабочий пакет {package!r} "
                "в виртуальном окружении @comp/.venv"
            ) from error

    def _model(self) -> str:
        if not self.model:
            raise ValueError(
                "Имя модели не задано: передайте model конструктору Clients"
            )
        return self.model

    @cached_property
    def openai(self) -> Any:
        """Возвращает ``openai.OpenAI`` для endpoint ``/v1``."""
        module = self._module("openai")
        return module.OpenAI(**{
            "base_url": self.openai_url,
            "api_key": self.api_key,
            "timeout": self.timeout,
        })

    @cached_property
    def litellm(self) -> Any:
        """Возвращает ``litellm.Router`` с одной локальной моделью."""
        module = self._module("litellm")
        model_name = self._model()
        params = {
            "model": f"openai/{model_name}",
            "api_base": self.openai_url,
            "api_key": self.api_key,
            "timeout": self.timeout,
        }
        model_info = {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
            "cache_creation_input_token_cost": 0.0,
            "cache_read_input_token_cost": 0.0,
        }
        module.register_model(model_cost={params["model"]: model_info})
        model_list = [{
            "model_name": model_name,
            "litellm_params": params,
            "model_info": model_info,
        }]
        return module.Router(model_list=model_list)

    @cached_property
    def dspy(self) -> Any:
        """Возвращает ``dspy.LM``."""
        module = self._module("dspy", "dspy-ai")
        return module.LM(f"openai/{self._model()}", **{
            "api_base": self.openai_url,
            "api_key": self.api_key,
            "timeout": self.timeout,
        })

    @cached_property
    def instructor(self) -> Any:
        """Возвращает Instructor-обёртку над общим ``openai.OpenAI``."""
        module = self._module("instructor")
        return module.from_openai(self.openai)

    @cached_property
    def pydantic_ai(self) -> Any:
        """Возвращает OpenAI-модель Pydantic AI."""
        provider_module = self._module(
            "pydantic_ai.providers.openai", "pydantic-ai"
        )
        model_module = self._module("pydantic_ai.models.openai", "pydantic-ai")
        provider = provider_module.OpenAIProvider(**{
            "base_url": self.openai_url,
            "api_key": self.api_key,
        })
        model_type = getattr(model_module, "OpenAIModel", None)
        if model_type is None:
            model_type = getattr(model_module, "OpenAIChatModel", None)
        if model_type is None:
            raise ClientDependencyError(
                "Pydantic AI не содержит OpenAIModel или OpenAIChatModel"
            )
        return model_type(self._model(), provider=provider)

    @cached_property
    def baml(self) -> Any:
        """Возвращает BAML ``ClientRegistry`` с локальным OpenAI-провайдером."""
        module = self._module("baml_py", "baml-py")
        registry = module.ClientRegistry()
        name = "llama_cpp"
        options = {
            "base_url": self.openai_url,
            "api_key": self.api_key,
            "model": self._model(),
        }
        registry.add_llm_client(name=name, provider="openai", options=options)
        registry.set_primary(name)
        return registry

    @cached_property
    def smolagents(self) -> Any:
        """Возвращает ``smolagents.OpenAIServerModel``."""
        module = self._module("smolagents")
        return module.OpenAIServerModel(**{
            "model_id": self._model(),
            "api_base": self.openai_url,
            "api_key": self.api_key,
            "client_kwargs": {"timeout": self.timeout},
        })

    @cached_property
    def llama_index(self) -> Any:
        """Возвращает LlamaIndex LLM для OpenAI-совместимого локального сервера."""
        module = self._module(
            "llama_index.llms.openai_like", "llama-index-llms-openai-like"
        )
        return module.OpenAILike(**{
            "model": self._model(),
            "api_base": self.openai_url,
            "api_key": self.api_key,
            "timeout": self.timeout,
            "context_window": self.context_window or 3900,
            "is_chat_model": True,
            "is_function_calling_model": False,
        })

    @cached_property
    def httpx(self) -> Any:
        """Возвращает ``httpx.Client`` для прямых POST на ``/completion``."""
        module = self._module("httpx")
        return module.Client(base_url=self.server_url, timeout=self.timeout)

    @cached_property
    def llama_cpp_agent(self) -> Any:
        """Возвращает серверный provider пакета ``llama-cpp-agent``."""
        module = self._module("llama_cpp_agent.providers", "llama-cpp-agent")
        provider_type = getattr(module, "LlamaCppServerProvider", None)
        if provider_type is None:
            provider_type = getattr(module, "LlamaServerProvider", None)
        if provider_type is None:
            raise ClientDependencyError(
                "Пакет 'llama-cpp-agent' не содержит LlamaCppServerProvider "
                "или LlamaServerProvider"
            )
        return provider_type(server_address=self.server_url)

    @cached_property
    def outlines(self) -> Any:
        """Возвращает OpenAI-модель Outlines."""
        module = self._module("outlines.models.openai", "outlines")
        factory = getattr(module, "from_openai", None)
        if factory is not None:
            return factory(self.openai, self._model())
        return module.OpenAI(self.openai, self._model())

    @cached_property
    def guidance(self) -> Any:
        """Возвращает ``guidance.models.OpenAI``."""
        module = self._module("guidance.models", "guidance")
        return module.OpenAI(self._model(), **{
            "base_url": self.openai_url,
            "api_key": self.api_key,
            "timeout": self.timeout,
        })


LlamaCppClients = Clients
