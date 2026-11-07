"""One small, model-swappable LLM layer.

Every call is: system + user -> JSON validated against a Pydantic schema (one repair retry).
Providers: Claude via Amazon Bedrock (Anthropic SDK, Mantle endpoint), any OpenAI-compatible
endpoint (Gemini, Groq, OpenRouter, Bedrock open models, a Kuku-provided API, Ollama), and
'rules' (no network; deterministic heuristics). Calls are cached on disk, budgeted, and traced.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
ROOT = Path(__file__).resolve().parent.parent


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


@dataclass
class Profile:
    name: str
    provider: str                     # anthropic_bedrock | openai_compat | rules
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    region_env: str = "AWS_REGION"
    effort: str | None = None         # Claude: low|medium|high|xhigh|max
    thinking: str = "none"            # Claude: none|adaptive
    json_mode: str = "json_object"    # openai_compat: json_schema|json_object|prompt
    extra: dict = field(default_factory=dict)
    price_in: float = 0.0             # USD per 1M tokens (list price, for cost reports)
    price_out: float = 0.0
    max_concurrency: int = 4

    @property
    def family(self) -> str:
        m = self.model.lower()
        for fam in ("claude", "gpt-oss", "gemini", "gemma", "llama", "qwen", "mistral", "deepseek", "nova", "gpt"):
            if fam in m:
                return fam
        return self.provider


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config.yaml"
    raw = path.read_text()
    raw = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), raw)
    return yaml.safe_load(raw)


def get_profile(cfg: dict, name: str) -> Profile:
    if name not in cfg["profiles"]:
        raise LLMError(f"unknown profile '{name}'. Known: {', '.join(cfg['profiles'])}")
    return Profile(name=name, **cfg["profiles"][name])


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    seconds: float = 0.0


class Budget:
    def __init__(self, max_calls: int = 400, max_usd: float = 5.0):
        self.max_calls, self.max_usd = max_calls, max_usd
        self.usage = Usage()
        self.by_role: dict[str, Usage] = {}
        self._lock = threading.Lock()

    def charge(self, role: str, tin: int, tout: int, usd: float, secs: float, cached: bool) -> None:
        with self._lock:
            for u in (self.usage, self.by_role.setdefault(role, Usage())):
                if cached:
                    u.cache_hits += 1
                else:
                    u.calls += 1
                    u.tokens_in += tin
                    u.tokens_out += tout
                    u.usd += usd
                    u.seconds += secs

    def check(self) -> None:
        if self.usage.calls >= self.max_calls:
            raise BudgetExceeded(f"call budget {self.max_calls} reached")
        if self.usage.usd >= self.max_usd:
            raise BudgetExceeded(f"cost budget ${self.max_usd:.2f} reached")


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    if not text.startswith(("{", "[")):
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            text = m.group(0)
    return text


# ---------------------------------------------------------------- providers
class _Claude:
    """Claude in Amazon Bedrock via the Anthropic SDK's Mantle client."""

    def __init__(self, p: Profile):
        from anthropic import AnthropicBedrockMantle

        region = os.environ.get(p.region_env) or os.environ.get("AWS_REGION") or "us-east-1"
        self.client = AnthropicBedrockMantle(aws_region=region, max_retries=4, timeout=300)
        self.p = p

    def __call__(self, system: str, user: str, schema: type[BaseModel], max_tokens: int):
        kw: dict[str, Any] = {}
        if self.p.effort:
            kw["output_config"] = {"effort": self.p.effort}
        if self.p.thinking == "adaptive":
            kw["thinking"] = {"type": "adaptive"}
        resp = self.client.messages.parse(
            model=self.p.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}], output_format=schema, **kw,
        )
        if resp.stop_reason == "refusal":
            raise LLMError(f"model refused: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise LLMError("output truncated at max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        u = resp.usage
        tin = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) \
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        return text, tin, u.output_tokens or 0


class _OpenAICompat:
    def __init__(self, p: Profile):
        from openai import OpenAI

        key = os.environ.get(p.api_key_env, "") if p.api_key_env else "none"
        if p.api_key_env and not key:
            raise LLMError(f"profile '{p.name}' needs env var {p.api_key_env}")
        self.client = OpenAI(base_url=p.base_url, api_key=key or "none", max_retries=4, timeout=300)
        self.p = p

    def __call__(self, system: str, user: str, schema: type[BaseModel], max_tokens: int):
        sys = system
        kw: dict[str, Any] = dict(self.p.extra)
        if self.p.json_mode == "json_schema":
            kw["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "schema": schema.model_json_schema(), "strict": False}}
        else:
            sys = system + "\n\nReturn ONLY a JSON object matching this JSON Schema:\n" + \
                json.dumps(schema.model_json_schema())
            if self.p.json_mode == "json_object":
                kw["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(
            model=self.p.model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}], **kw,
        )
        ch = resp.choices[0]
        if ch.finish_reason == "length":
            raise LLMError("output truncated at max_tokens")
        u = resp.usage
        return ch.message.content or "", (u.prompt_tokens if u else 0), (u.completion_tokens if u else 0)


# ---------------------------------------------------------------- client
class LLM:
    """A role-bound client: cache -> provider -> validate -> (one repair retry)."""

    def __init__(self, profile: Profile, role: str, budget: Budget, cache_dir: Path, trace_path: Path | None,
                 rules_fn: Callable[[str, str, type[BaseModel]], BaseModel] | None = None):
        self.p, self.role, self.budget = profile, role, budget
        self.trace_path = trace_path
        self.rules_fn = rules_fn
        self._sem = threading.Semaphore(max(1, profile.max_concurrency))
        self._trace_lock = threading.Lock()
        import diskcache

        self.cache = diskcache.Cache(str(cache_dir))
        self._impl = None

    @property
    def family(self) -> str:
        return self.p.family

    def _provider(self):
        if self._impl is None:
            if self.p.provider == "anthropic_bedrock":
                self._impl = _Claude(self.p)
            elif self.p.provider == "openai_compat":
                self._impl = _OpenAICompat(self.p)
            else:
                raise LLMError(f"provider '{self.p.provider}' has no network implementation")
        return self._impl

    def json(self, system: str, user: str, schema: type[T], *, max_tokens: int = 8000,
             prompt_version: str = "v1", tag: str = "") -> T:
        if self.p.provider == "rules":
            if not self.rules_fn:
                raise LLMError(f"role '{self.role}' has no rules fallback")
            out = self.rules_fn(system, user, schema)
            self._trace(tag, cached=False, tin=0, tout=0, secs=0.0, ok=True, note="rules")
            return out  # type: ignore[return-value]
        key = hashlib.sha256("\x1f".join(
            [self.p.model, self.p.effort or "", self.p.thinking, prompt_version, schema.__name__,
             json.dumps(schema.model_json_schema(), sort_keys=True), system, user]).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit is not None:
            self.budget.charge(self.role, 0, 0, 0.0, 0.0, cached=True)
            self._trace(tag, cached=True, tin=0, tout=0, secs=0.0, ok=True)
            return schema.model_validate_json(hit)
        self.budget.check()
        prov = self._provider()
        cur_user, last_err = user, None
        for attempt in range(2):
            t = time.time()
            with self._sem:
                text, tin, tout = prov(system, cur_user, schema, max_tokens)
            secs = time.time() - t
            usd = tin / 1e6 * self.p.price_in + tout / 1e6 * self.p.price_out
            self.budget.charge(self.role, tin, tout, usd, secs, cached=False)
            try:
                obj = schema.model_validate_json(_extract_json(text))
                self.cache.set(key, obj.model_dump_json())
                self._trace(tag, cached=False, tin=tin, tout=tout, secs=secs, ok=True, attempt=attempt)
                return obj
            except ValidationError as e:
                last_err = str(e)[:1500]
                self._trace(tag, cached=False, tin=tin, tout=tout, secs=secs, ok=False, attempt=attempt,
                            note=last_err[:300])
                cur_user = (user + "\n\nYour previous reply did not match the required JSON schema:\n"
                            + last_err + "\nReply again with ONLY the corrected JSON object.")
        raise LLMError(f"[{self.role}/{tag}] schema validation failed twice: {last_err}")

    def _trace(self, tag: str, **kw) -> None:
        if not self.trace_path:
            return
        rec = {"ts": round(time.time(), 3), "role": self.role, "profile": self.p.name, "model": self.p.model,
               "tag": tag, **kw}
        with self._trace_lock, open(self.trace_path, "a") as f:
            f.write(json.dumps(rec) + "\n")


class Router:
    """Builds role-bound clients from config. One-profile mode routes every role to one model."""

    def __init__(self, cfg: dict, overrides: dict[str, str] | None = None, single: str | None = None,
                 out_dir: Path | None = None, budget: Budget | None = None):
        self.cfg = cfg
        self.roles = dict(cfg.get("roles", {}))
        if single:
            self.roles = {r: single for r in self.roles}
        for r, prof in (overrides or {}).items():
            if prof:
                self.roles[r] = prof
        b = cfg.get("budget", {})
        self.budget = budget or Budget(max_calls=b.get("max_calls", 400), max_usd=b.get("max_usd", 5.0))
        self.cache_dir = ROOT / ".cache" / "llm"
        self.trace = (out_dir / "trace.jsonl") if out_dir else None
        self._clients: dict[str, LLM] = {}
        self.rules: dict[str, Callable] = {}

    def __getitem__(self, role: str) -> LLM:
        if role not in self._clients:
            prof = get_profile(self.cfg, self.roles[role])
            self._clients[role] = LLM(prof, role, self.budget, self.cache_dir, self.trace, self.rules.get(role))
        return self._clients[role]

    def describe(self) -> str:
        return ", ".join(f"{r}={p}" for r, p in self.roles.items())
