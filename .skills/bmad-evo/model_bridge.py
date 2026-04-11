"""
ModelBridge - Multi-environment model calling for BMAD-EVO

Replaces the hardcoded openclaw _call_model in all bmad-evo modules
via monkey-patching at import time. Supports 4 calling modes:

1. opencode  - Running inside opencode agent, delegate to parent process
2. claude    - Use Anthropic Claude API (Claude Code environment)
3. openclaw  - Original openclaw CLI (if installed)
4. http      - Direct HTTP API call (DashScope / OpenAI-compatible)

Detection order:
  1. Config file: .bmad-env.yaml in project root
  2. Environment variable: BMAD_EVO_MODE
  3. Auto-detect: check if openclaw/opencode/claude is available
  4. Fallback: HTTP API using key from D:/bmad-evo/config/key.txt
"""

import os
import json
import logging
import tempfile
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

BMAD_ORIGINAL_PATH = Path("D:/bmad-evo")
BMAD_KEY_FILE = BMAD_ORIGINAL_PATH / "config" / "key.txt"

SUPPORTED_MODES = ("opencode", "claude", "openclaw", "http")


class ModelRequestPending(Exception):
    """Raised when a model call needs to be handled by the opencode agent."""

    def __init__(self, request_id: str, prompt: str, model: str):
        self.request_id = request_id
        self.prompt = prompt
        self.model = model
        super().__init__(
            f"Model request pending: {request_id}. "
            f"Agent must execute prompt and call back with response."
        )


def _load_api_config() -> Dict[str, str]:
    config = {"api_key": "", "api_base": "", "anthropic_base": ""}
    if BMAD_KEY_FILE.exists():
        for line in BMAD_KEY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "API Key" in line:
                val = line.split("\uff1a", 1)[-1].split(":", 1)[-1].strip()
                config["api_key"] = val
            elif "OpenAI" in line or "openai" in line:
                val = line.split("\uff1a", 1)[-1].split(":", 1)[-1].strip()
                if not val.startswith("http"):
                    val = "https:" + val if val.startswith("//") else val
                config["api_base"] = val
            elif "Anthropic" in line or "anthropic" in line:
                val = line.split("\uff1a", 1)[-1].split(":", 1)[-1].strip()
                if not val.startswith("http"):
                    val = "https:" + val if val.startswith("//") else val
                config["anthropic_base"] = val
    env_overrides = {
        "api_key": os.environ.get("BMAD_API_KEY", ""),
        "api_base": os.environ.get("BMAD_API_BASE", ""),
    }
    for k, v in env_overrides.items():
        if v:
            config[k] = v
    return config


def _detect_environment() -> str:
    if os.environ.get("OPENCODE") or os.environ.get("OPENCODE_VERSION"):
        return "opencode"
    if shutil.which("claude"):
        return "claude"
    if shutil.which("openclaw"):
        return "openclaw"
    api_config = _load_api_config()
    if api_config["api_key"] and api_config["api_base"]:
        return "http"
    return "fallback"


def _resolve_mode(config_mode: Optional[str] = None) -> str:
    if config_mode and config_mode in SUPPORTED_MODES:
        return config_mode
    env_mode = os.environ.get("BMAD_EVO_MODE", "")
    if env_mode and env_mode in SUPPORTED_MODES:
        return env_mode
    return _detect_environment()


class ModelBridge:
    """Unified model calling interface across environments."""

    _prompt_pass = None
    _pending_requests = []
    PENDING_SENTINEL = "__BMAD_MODEL_REQUEST_PENDING__"

    def __init__(self, mode: Optional[str] = None, config: Optional[Dict] = None):
        self.mode = _resolve_mode(mode)
        self.api_config = _load_api_config()
        self.timeout = (config or {}).get("timeout", 120)
        self._request_counter = 0
        logger.info(f"ModelBridge initialized: mode={self.mode}")

    @classmethod
    def set_prompt_pass(cls, prompt_pass):
        cls._prompt_pass = prompt_pass
        cls._pending_requests = []

    @classmethod
    def get_pending_requests(cls):
        return list(cls._pending_requests)

    @classmethod
    def clear_pending(cls):
        cls._pending_requests = []

    def call_model(self, model: str, prompt: str, timeout: Optional[int] = None) -> str:
        timeout = timeout or self.timeout
        caller = {
            "opencode": self._call_opencode,
            "claude": self._call_claude,
            "openclaw": self._call_openclaw,
            "http": self._call_http,
        }.get(self.mode, self._call_fallback)
        return caller(model, prompt, timeout)

    def _call_opencode(self, model: str, prompt: str, timeout: int) -> str:
        """
        Prompt-passing mode: in opencode environment, the host agent IS the model.
        Returns a sentinel string that signals the patch layer to raise
        ModelRequestPending AFTER the bmad-evo caller's try/except completes.
        """
        self._request_counter += 1
        req_id = f"bmad-{self._request_counter:03d}"

        request = {
            "id": req_id,
            "prompt": prompt,
            "model": model,
            "type": "model_inference",
        }
        ModelBridge._pending_requests.append(request)

        if ModelBridge._prompt_pass:
            ModelBridge._prompt_pass.create_request(
                req_id, prompt, model, "model_inference", "ModelBridge"
            )

        return ModelBridge.PENDING_SENTINEL

    def _generate_local_response(self, model: str, prompt: str) -> str:
        """Generate a structured response locally for the opencode skill proxy mode."""
        prompt_lower = prompt.lower()

        if any(
            kw in prompt_lower
            for kw in ["route", "mapping", "primary_model", "fallback_model"]
        ):
            return self._make_routing_response(model, prompt)

        if any(
            kw in prompt_lower
            for kw in ["execution_order", "parallel_group", "can_parallel"]
        ):
            return self._make_role_response(model, prompt)

        if any(
            kw in prompt_lower
            for kw in ["task_type", "complexity_score", "recommended_roles"]
        ):
            return self._make_analysis_response(model, prompt)

        return json.dumps(
            {
                "output": "Task processed via opencode skill proxy",
                "model_requested": model,
                "prompt_preview": prompt[:200],
            },
            ensure_ascii=False,
            indent=2,
        )

    def _make_analysis_response(self, model, prompt):
        return json.dumps(
            {
                "task_type": self._infer_task_type(prompt),
                "complexity_score": self._infer_complexity(prompt),
                "recommended_roles_count": self._infer_roles(prompt),
                "key_skills": ["analysis", "design", "implementation"],
                "estimated_duration": "2-4 hours",
                "risk_factors": ["scope ambiguity", "integration complexity"],
                "success_criteria": ["all requirements implemented", "tests passing"],
            },
            ensure_ascii=False,
            indent=2,
        )

    def _make_role_response(self, model, prompt):
        return json.dumps(
            {
                "roles": [
                    {
                        "name": "analyst",
                        "title": "Requirements Analyst",
                        "responsibilities": [
                            "analyze requirements",
                            "identify constraints",
                        ],
                        "can_parallel": False,
                        "model_requirement": "strong reasoning",
                    },
                    {
                        "name": "architect",
                        "title": "System Architect",
                        "responsibilities": [
                            "design architecture",
                            "define boundaries",
                        ],
                        "can_parallel": False,
                        "model_requirement": "deep reasoning",
                    },
                    {
                        "name": "implementer",
                        "title": "Implementer",
                        "responsibilities": ["implement solution", "write tests"],
                        "can_parallel": True,
                        "model_requirement": "code generation",
                    },
                ],
                "execution_order": ["analyst", "architect", "implementer"],
                "parallel_groups": [],
                "rationale": "Sequential analysis-then-implementation flow",
            },
            ensure_ascii=False,
            indent=2,
        )

    def _make_routing_response(self, model, prompt):
        return json.dumps(
            {
                "mappings": [
                    {
                        "role_id": "analyst",
                        "primary_model": model or "glm-4.7",
                        "fallback_models": ["glm-5.1"],
                        "reasoning": "auto-assigned",
                    },
                    {
                        "role_id": "architect",
                        "primary_model": model or "glm-5.1",
                        "fallback_models": ["glm-4.7"],
                        "reasoning": "auto-assigned",
                    },
                    {
                        "role_id": "implementer",
                        "primary_model": model or "glm-5.1",
                        "fallback_models": ["glm-4.7"],
                        "reasoning": "auto-assigned",
                    },
                ],
                "estimated_cost_tier": "medium",
            },
            ensure_ascii=False,
            indent=2,
        )

        if any(kw in prompt_lower for kw in ["role", "execution_order", "parallel"]):
            return json.dumps(
                {
                    "roles": [
                        {
                            "name": "analyst",
                            "title": "Requirements Analyst",
                            "responsibilities": [
                                "analyze requirements",
                                "identify constraints",
                            ],
                            "can_parallel": False,
                            "model_requirement": "strong reasoning",
                        },
                        {
                            "name": "architect",
                            "title": "System Architect",
                            "responsibilities": [
                                "design architecture",
                                "define boundaries",
                            ],
                            "can_parallel": False,
                            "model_requirement": "deep reasoning",
                        },
                        {
                            "name": "implementer",
                            "title": "Implementer",
                            "responsibilities": ["implement solution", "write tests"],
                            "can_parallel": True,
                            "model_requirement": "code generation",
                        },
                    ],
                    "execution_order": ["analyst", "architect", "implementer"],
                    "parallel_groups": [],
                    "rationale": "Sequential analysis-then-implementation flow",
                },
                ensure_ascii=False,
                indent=2,
            )

        if any(kw in prompt_lower for kw in ["route", "model", "mapping"]):
            return json.dumps(
                {
                    "mappings": [
                        {
                            "role_id": "analyst",
                            "primary_model": model or "glm-4.7",
                            "fallback_models": ["glm-5.1"],
                            "reasoning": "auto-assigned",
                        },
                    ],
                    "estimated_cost_tier": "medium",
                },
                ensure_ascii=False,
                indent=2,
            )

        return json.dumps(
            {
                "output": "Task processed via opencode skill proxy",
                "model_requested": model,
                "prompt_preview": prompt[:200],
            },
            ensure_ascii=False,
            indent=2,
        )

    def _infer_task_type(self, prompt: str) -> str:
        keywords = {
            "web": "web_development",
            "api": "api_design",
            "data": "data_processing",
            "test": "testing",
            "deploy": "deployment",
            "ui": "ui_design",
            "auth": "security",
            "payment": "e_commerce",
            "mobile": "mobile_development",
            "ml": "machine_learning",
            "microservice": "system_design",
        }
        prompt_lower = prompt.lower()
        for kw, task_type in keywords.items():
            if kw in prompt_lower:
                return task_type
        return "general_development"

    def _infer_complexity(self, prompt: str) -> int:
        score = 3
        complexity_markers = [
            "distributed",
            "microservice",
            "real-time",
            "scalab",
            "machine learning",
            "multi-agent",
            "concurrent",
        ]
        simplicity_markers = ["simple", "basic", "single", "one page", "crud"]
        prompt_lower = prompt.lower()
        for marker in complexity_markers:
            if marker in prompt_lower:
                score += 1
        for marker in simplicity_markers:
            if marker in prompt_lower:
                score -= 1
        return max(1, min(10, score))

    def _infer_roles(self, prompt: str) -> int:
        complexity = self._infer_complexity(prompt)
        if complexity <= 3:
            return 1
        elif complexity <= 5:
            return 2
        elif complexity <= 7:
            return 3
        else:
            return 5

    def _call_claude(self, model: str, prompt: str, timeout: int) -> str:
        if shutil.which("claude"):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as f:
                f.write(prompt)
                prompt_file = f.name
            try:
                cmd = [
                    "claude",
                    "--model",
                    model or "claude-sonnet-4-20250514",
                    "--prompt",
                    f"Read this file and respond: {prompt_file}",
                    "--output-format",
                    "text",
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception as e:
                logger.warning(f"Claude CLI failed: {e}")
            finally:
                Path(prompt_file).unlink(missing_ok=True)

        anthropic_base = self.api_config.get("anthropic_base", "")
        api_key = self.api_config.get("api_key", "")
        if anthropic_base and api_key:
            return self._call_http_anthropic(model, prompt, timeout)

        return self._call_http(model, prompt, timeout)

    def _call_openclaw(self, model: str, prompt: str, timeout: int) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(prompt)
            prompt_file = f.name
        try:
            cmd = [
                "openclaw",
                "sessions",
                "spawn",
                "--model",
                model,
                "--task-file",
                prompt_file,
                "--timeout",
                str(timeout),
                "--cleanup",
                "keep",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 30
            )
            if result.returncode != 0:
                raise RuntimeError(f"openclaw failed: {result.stderr}")
            return result.stdout
        finally:
            Path(prompt_file).unlink(missing_ok=True)

    def _call_http(self, model: str, prompt: str, timeout: int) -> str:
        api_key = self.api_config.get("api_key", "")
        api_base = self.api_config.get("api_base", "")
        if not api_key or not api_base:
            return self._call_fallback(model, prompt, timeout)

        import urllib.request
        import urllib.error

        url = f"{api_base.rstrip('/')}/chat/completions"
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 4096,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"HTTP API call failed: {e}")
            return self._call_fallback(model, prompt, timeout)

    def _call_http_anthropic(self, model: str, prompt: str, timeout: int) -> str:
        api_key = self.api_config.get("api_key", "")
        api_base = self.api_config.get("anthropic_base", "")
        if not api_key or not api_base:
            return self._call_fallback(model, prompt, timeout)

        import urllib.request
        import urllib.error

        url = f"{api_base.rstrip('/')}/messages"
        payload = json.dumps(
            {
                "model": model or "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["content"][0]["text"]
        except Exception as e:
            logger.warning(f"Anthropic API call failed: {e}")
            return self._call_fallback(model, prompt, timeout)

    def _call_fallback(self, model: str, prompt: str, timeout: int) -> str:
        preview = prompt[:300].replace("\n", " ")
        return (
            f"[ModelBridge fallback] model={model}\n"
            f"No working model backend found.\n"
            f"Prompt preview: {preview}...\n\n"
            f"Configure via:\n"
            f"  1. .bmad-env.yaml -> mode: http/opencode/claude/openclaw\n"
            f"  2. Environment: BMAD_EVO_MODE=http\n"
            f"  3. Ensure API key in D:/bmad-evo/config/key.txt"
        )


def load_bmad_env_config(project_path: Path) -> Optional[str]:
    env_file = project_path / ".bmad-env.yaml"
    if not env_file.exists():
        return None
    try:
        import yaml

        with open(env_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("mode")
    except Exception:
        return None


def create_bmad_env_template(project_path: Path, mode: str = "auto") -> Path:
    env_file = project_path / ".bmad-env.yaml"
    content = f"""# BMAD-EVO Environment Configuration
# mode: auto | opencode | claude | openclaw | http
# auto = detect automatically

mode: {mode}

# HTTP API settings (used when mode=http or as fallback)
# api_key: sk-your-key-here
# api_base: https://coding.dashscope.aliyuncs.com/v1

# Timeout in seconds
timeout: 120

# Model overrides (optional)
# models:
#   primary: glm-4.7
#   fallback: glm-5.1
"""
    with open(env_file, "w", encoding="utf-8") as f:
        f.write(content)
    return env_file


def patch_bmad_modules(bridge: ModelBridge):
    """Monkey-patch all bmad-evo _call_model methods to use ModelBridge."""
    patched = []

    modules_to_patch = [
        ("task_analyzer", "TaskAnalyzer"),
        ("role_generator", "DynamicRoleGenerator"),
        ("model_router", "ModelRouter"),
        ("resilient_executor", "ResilientExecutor"),
    ]

    for module_name, class_name in modules_to_patch:
        try:
            import importlib

            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name, None)
            if cls and hasattr(cls, "_call_model"):
                original = cls._call_model

                def make_patched(br, orig):
                    def _patched_call_model(self_inner, model, prompt):
                        try:
                            return br.call_model(
                                model, prompt, getattr(self_inner, "timeout", 120)
                            )
                        except ModelRequestPending:
                            raise
                        except Exception as e:
                            logger.warning(
                                f"ModelBridge call failed ({br.mode}), using original: {e}"
                            )
                            return orig(self_inner, model, prompt)

                    return _patched_call_model

                cls._call_model = make_patched(bridge, original)
                patched.append(f"{module_name}.{class_name}._call_model")
                logger.info(
                    f"Patched {module_name}.{class_name}._call_model -> mode={bridge.mode}"
                )
        except Exception as e:
            logger.debug(f"Could not patch {module_name}.{class_name}: {e}")

    bridge._patched_modules = patched
    if patched:
        logger.info(
            f"ModelBridge patched {len(patched)} modules with mode={bridge.mode}"
        )
    else:
        logger.warning(
            f"ModelBridge: no modules patched (bmad-evo may not be importable)"
        )
    return patched
