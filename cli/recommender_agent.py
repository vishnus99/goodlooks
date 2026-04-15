from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import URLError
from urllib.request import urlopen


class RecommenderError(RuntimeError):
    """Raised when the LangChain recommender cannot produce a valid result."""


DEFAULT_BACKEND = "langchain"
DEFAULT_PROVIDER = "auto"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def _config_dir() -> Path:
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    base_dir = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base_dir / "goodlooks"


def recommender_config_path() -> Path:
    return _config_dir() / "recommender.json"


def save_recommender_config(config: dict[str, Any]) -> Path:
    path = recommender_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return path


def _load_recommender_config() -> dict[str, Any]:
    path = recommender_config_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _maybe_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def _is_ollama_reachable(base_url: str) -> bool:
    try:
        with urlopen(f"{base_url}/api/tags", timeout=1.5):
            return True
    except Exception:
        return False


def _env_or_config(
    env_name: str,
    config: dict[str, Any],
    config_key: str,
    default: str = "",
) -> str:
    env_val = os.getenv(env_name)
    if env_val is not None and str(env_val).strip():
        return str(env_val).strip()
    cfg_val = config.get(config_key)
    if cfg_val is not None and str(cfg_val).strip():
        return str(cfg_val).strip()
    return default


def _resolve_recommender_settings() -> dict[str, Any]:
    config = _load_recommender_config()
    backend = _env_or_config(
        "GOODLOOKS_RECOMMENDER_BACKEND", config, "backend", DEFAULT_BACKEND
    ).lower()
    provider_requested = _env_or_config(
        "GOODLOOKS_RECOMMENDER_PROVIDER", config, "provider", DEFAULT_PROVIDER
    ).lower()
    ollama_base_url = _env_or_config(
        "OLLAMA_BASE_URL", config, "ollama_base_url", DEFAULT_OLLAMA_BASE_URL
    )

    provider = provider_requested
    provider_source = "configured"
    if provider_requested == "auto":
        provider_source = "auto-detected"
        if _maybe_import("langchain_ollama") and _is_ollama_reachable(ollama_base_url):
            provider = "ollama"
        elif os.getenv("OPENAI_API_KEY", "").strip():
            provider = "openai"
        else:
            # Keep defaulting to ollama in auto mode for local-first UX.
            provider = "ollama"

    if provider == "ollama":
        model = _env_or_config(
            "GOODLOOKS_LLM_MODEL", config, "model", DEFAULT_OLLAMA_MODEL
        )
    else:
        model = _env_or_config(
            "GOODLOOKS_LLM_MODEL", config, "model", DEFAULT_OPENAI_MODEL
        )

    timeout_raw = _env_or_config(
        "GOODLOOKS_RECOMMENDER_TIMEOUT_SEC",
        config,
        "timeout_sec",
        str(DEFAULT_TIMEOUT_SEC),
    )
    try:
        timeout_sec = float(timeout_raw)
    except ValueError:
        timeout_sec = DEFAULT_TIMEOUT_SEC
    timeout_sec = max(1.0, min(30.0, timeout_sec))

    return {
        "backend": backend,
        "provider": provider,
        "provider_source": provider_source,
        "model": model,
        "timeout_sec": timeout_sec,
        "ollama_base_url": ollama_base_url,
        "config_path": str(recommender_config_path()),
    }


def current_recommender_settings() -> dict[str, Any]:
    """Return effective settings after env/config/default resolution."""
    return _resolve_recommender_settings()


def _normalize_recommendation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = str(payload.get("summary", "")).strip()
    first_action = str(payload.get("first_action", "")).strip()
    raw_estimate = payload.get("estimated_time_minutes", 25)
    try:
        estimate = int(raw_estimate)
    except (TypeError, ValueError):
        estimate = 25
    estimate = max(1, min(240, estimate))

    steps_raw = payload.get("steps", [])
    if not isinstance(steps_raw, list):
        steps_raw = []
    steps = [str(s).strip() for s in steps_raw if str(s).strip()]
    if len(steps) < 2:
        raise RecommenderError("Recommendation must include at least two steps.")
    steps = steps[:8]

    blockers_raw = payload.get("risks_or_blockers", [])
    if not isinstance(blockers_raw, list):
        blockers_raw = []
    blockers = [str(b).strip() for b in blockers_raw if str(b).strip()][:6]

    if not summary:
        summary = "Recommended approach."
    if not first_action:
        first_action = steps[0]

    return {
        "summary": summary,
        "first_action": first_action,
        "estimated_time_minutes": estimate,
        "steps": steps,
        "risks_or_blockers": blockers,
    }


def _task_context(task: dict[str, Any]) -> str:
    return (
        "Task details:\n"
        f"- id: {task.get('id')}\n"
        f"- title: {task.get('title', '')}\n"
        f"- urgency: {task.get('urgency', 'normal')}\n"
        f"- done: {bool(task.get('done'))}\n"
        f"- created_at: {task.get('created_at', '')}\n"
    )


class RecommenderProvider(Protocol):
    name: str

    def build_structured_llm(self, recommendation_model: Any) -> Any:
        ...

    def diagnose(self, settings: dict[str, Any]) -> list[dict[str, str]]:
        ...


@dataclass
class OllamaProvider:
    settings: dict[str, Any]
    name: str = "ollama"

    @property
    def model(self) -> str:
        return str(self.settings["model"])

    @property
    def base_url(self) -> str:
        return str(self.settings["ollama_base_url"])

    def build_structured_llm(self, recommendation_model: Any) -> Any:
        try:
            from langchain_ollama import ChatOllama
        except Exception as exc:
            raise RecommenderError(
                "langchain-ollama is not installed. Install with: python -m pip install langchain-ollama"
            ) from exc
        llm = ChatOllama(model=self.model, temperature=0.2, base_url=self.base_url)
        return llm.with_structured_output(recommendation_model)

    def diagnose(self, settings: dict[str, Any]) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []
        checks.append(
            {"name": "model", "status": "ok", "detail": f"Ollama model configured: {self.model}"}
        )
        try:
            import langchain_ollama  # noqa: F401

            checks.append(
                {
                    "name": "python_package",
                    "status": "ok",
                    "detail": "langchain-ollama import is available.",
                }
            )
        except Exception:
            checks.append(
                {
                    "name": "python_package",
                    "status": "fail",
                    "detail": "langchain-ollama is not installed.",
                }
            )
        try:
            with urlopen(f"{self.base_url}/api/tags", timeout=2.5) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
            if self.model in payload:
                checks.append(
                    {
                        "name": "ollama",
                        "status": "ok",
                        "detail": f"Ollama reachable at {self.base_url} and model appears available.",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "ollama",
                        "status": "fail",
                        "detail": (
                            f"Ollama reachable at {self.base_url}, but model '{self.model}' not found. "
                            f"Run: ollama pull {self.model}"
                        ),
                    }
                )
        except URLError as exc:
            checks.append(
                {
                    "name": "ollama",
                    "status": "fail",
                    "detail": f"Cannot reach Ollama at {self.base_url}: {exc}. Start Ollama and retry.",
                }
            )
        except Exception as exc:
            checks.append(
                {"name": "ollama", "status": "fail", "detail": f"Ollama check failed: {exc}"}
            )
        return checks


@dataclass
class OpenAIProvider:
    settings: dict[str, Any]
    name: str = "openai"

    @property
    def model(self) -> str:
        return str(self.settings["model"])

    def build_structured_llm(self, recommendation_model: Any) -> Any:
        try:
            from langchain_openai import ChatOpenAI
        except Exception as exc:
            raise RecommenderError(
                "langchain-openai is not installed. Install with: python -m pip install langchain-openai"
            ) from exc
        llm = ChatOpenAI(model=self.model, temperature=0.2)
        return llm.with_structured_output(recommendation_model)

    def diagnose(self, settings: dict[str, Any]) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []
        checks.append(
            {"name": "model", "status": "ok", "detail": f"OpenAI model configured: {self.model}"}
        )
        try:
            import langchain_openai  # noqa: F401

            checks.append(
                {
                    "name": "python_package",
                    "status": "ok",
                    "detail": "langchain-openai import is available.",
                }
            )
        except Exception:
            checks.append(
                {
                    "name": "python_package",
                    "status": "fail",
                    "detail": "langchain-openai is not installed.",
                }
            )
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        checks.append(
            {
                "name": "openai_api_key",
                "status": "ok" if openai_key else "fail",
                "detail": "OPENAI_API_KEY is set." if openai_key else "OPENAI_API_KEY is missing.",
            }
        )
        return checks


def get_provider_client(settings: dict[str, Any]) -> RecommenderProvider:
    provider = str(settings["provider"])
    if provider == "ollama":
        return OllamaProvider(settings=settings)
    return OpenAIProvider(settings=settings)


def generate_recommendation_with_langchain(
    task: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    try:
        from pydantic import BaseModel, Field
    except Exception as exc:
        raise RecommenderError("pydantic is not installed.") from exc

    class Recommendation(BaseModel):
        summary: str
        first_action: str
        estimated_time_minutes: int = Field(ge=1, le=240)
        steps: list[str] = Field(min_length=2, max_length=8)
        risks_or_blockers: list[str] = Field(default_factory=list, max_length=6)

    provider = get_provider_client(settings)
    structured_llm = provider.build_structured_llm(Recommendation)
    system_prompt = (
        "You are a pragmatic productivity coach for personal tasks.\n"
        "Return concise, concrete recommendations with immediate next actions.\n"
        "Honor urgency and done status.\n"
        "If task is already done, provide short closure/review advice.\n"
        "Keep outputs realistic and actionable for one person.\n"
    )
    result = structured_llm.invoke(
        [
            ("system", system_prompt),
            ("human", _task_context(task)),
        ]
    )
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
    elif isinstance(result, dict):
        payload = result
    else:
        raise RecommenderError("Unexpected structured output from model.")
    return _normalize_recommendation_payload(payload)


def _friendly_fallback_reason(exc: Exception, settings: dict[str, Any]) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    provider = str(settings["provider"])
    if "api_key" in msg.lower() or "openai_api_key" in msg.lower():
        return "OpenAI key missing. Export OPENAI_API_KEY or switch to Ollama with GOODLOOKS_RECOMMENDER_PROVIDER=ollama."
    if "langchain-ollama is not installed" in msg:
        return "langchain-ollama is not installed. Install in the same Python env as goodlooks."
    if "langchain-openai is not installed" in msg:
        return "langchain-openai is not installed. Install in the same Python env as goodlooks."
    if provider == "ollama" and ("connection" in msg.lower() or "refused" in msg.lower()):
        return (
            f"Cannot reach Ollama at {settings['ollama_base_url']}. "
            "Start Ollama (`ollama serve`) and ensure your model is pulled."
        )
    return msg


def safe_generate_recommendation(
    task: dict[str, Any],
    fallback_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], bool, str | None]:
    settings = _resolve_recommender_settings()
    backend = str(settings["backend"])
    if backend == "heuristic":
        return fallback_fn(task), True, "Heuristic mode enabled by configuration."

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(generate_recommendation_with_langchain, task, settings)
            rec = future.result(timeout=float(settings["timeout_sec"]))
            return rec, False, None
    except FuturesTimeoutError:
        return fallback_fn(task), True, "Recommender timed out. Increase GOODLOOKS_RECOMMENDER_TIMEOUT_SEC if needed."
    except Exception as exc:
        return fallback_fn(task), True, _friendly_fallback_reason(exc, settings)


def diagnose_recommender() -> dict[str, Any]:
    settings = _resolve_recommender_settings()
    backend = str(settings["backend"])
    provider = str(settings["provider"])
    provider_source = str(settings["provider_source"])
    model_name = str(settings["model"])
    timeout_raw = str(settings["timeout_sec"])

    checks: list[dict[str, str]] = []

    if backend == "heuristic":
        checks.append(
            {
                "name": "backend",
                "status": "ok",
                "detail": "Heuristic mode enabled; no LLM/network required.",
            }
        )
        return {
            "ok": True,
            "backend": backend,
            "provider": provider,
            "model": model_name or "(not set)",
            "timeout": timeout_raw,
            "provider_source": provider_source,
            "config_path": str(settings["config_path"]),
            "checks": checks,
        }

    checks.append(
        {
            "name": "backend",
            "status": "ok",
            "detail": f"LangChain mode enabled (provider={provider}, source={provider_source}).",
        }
    )
    provider_client = get_provider_client(settings)
    checks.extend(provider_client.diagnose(settings))
    overall_ok = all(item["status"] == "ok" for item in checks)
    return {
        "ok": overall_ok,
        "backend": backend,
        "provider": provider,
        "model": model_name,
        "timeout": timeout_raw,
        "provider_source": provider_source,
        "config_path": str(settings["config_path"]),
        "checks": checks,
    }
