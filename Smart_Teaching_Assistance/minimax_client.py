"""MiniMax M3 client for SmartTA -- drop-in replacement for google-generativeai.

Wraps the MiniMax M3 API (Anthropic Messages-compatible) behind a tiny
shim that exposes the same surface the Gemini SDK did:

    from Smart_Teaching_Assistance import minimax_client as genai
    genai.configure(api_key="...")
    model = genai.GenerativeModel("MiniMax-M3")
    response = model.generate_content(prompt)        # text-only
    response = model.generate_content([prompt, img]) # vision (text + 1 image)
    response.text                                    # -> str

Why not just call the MiniMax API directly?  Because the rest of SmartTA
calls into `google.generativeai` everywhere, and the simplest path to a
working fork is to keep all those call-sites untouched.

The shim is intentionally minimal: no streaming, no tool use, no system
instructions parameter, no generation-config-object wrapper. The fork
still uses the same simple `prompt -> str` contract that Gemini had.

Environment variables (read by configure()):
    MINIMAX_API_KEY     required (or set GEMINI_API_KEY to skip MiniMax)
    MINIMAX_BASE_URL    default "https://api.minimax.io/anthropic"
    MINIMAX_MODEL       default "MiniMax-M3"
    MINIMAX_TIMEOUT_S   default 90

FALLBACK: when MiniMax returns 402 (insufficient balance), 429 (rate
limit), 500/502/503/504, the client transparently retries the call
against Gemini 2.5 Flash Lite using `GEMINI_API_KEY`. Vision calls
ALWAYS go to Gemini since the project does not currently ship a working
MiniMax-M3 vision path (matching the behavior of the main project).

Set `FORCE_GEMINI=1` in env to bypass MiniMax entirely.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

logger = logging.getLogger(__name__)

# --- Defaults ----------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.minimax.io/anthropic"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_TIMEOUT_S = 90.0
DEFAULT_MAX_TOKENS = 16384
RETRY_BACKOFF_S = 2.0
GEMINI_FALLBACK_MODEL = "gemini-flash-lite-latest"


# --- Module-level state (mirrors google.generativeai.configure) ---------------

class _State:
    api_key: Optional[str] = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S
    force_gemini: bool = False

    @classmethod
    def reset(cls) -> None:
        cls.api_key = None
        cls.base_url = DEFAULT_BASE_URL
        cls.model = DEFAULT_MODEL
        cls.timeout_s = DEFAULT_TIMEOUT_S
        cls.force_gemini = False


def configure(api_key: Optional[str] = None,
              base_url: Optional[str] = None,
              model: Optional[str] = None,
              timeout_s: Optional[float] = None,
              force_gemini: Optional[bool] = None) -> None:
    """Configure the module-level API key + endpoint.

    All args are optional; omitted values fall back to env vars, then defaults.
    """
    if api_key is None:
        api_key = (os.environ.get("MINIMAX_API_KEY")
                   or os.environ.get("minimax_api_key"))
    if base_url is None:
        base_url = (os.environ.get("MINIMAX_API_BASE")
                    or os.environ.get("MINIMAX_BASE_URL")
                    or DEFAULT_BASE_URL)
    if model is None:
        model = os.environ.get("MINIMAX_MODEL") or DEFAULT_MODEL
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("MINIMAX_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))
        except (TypeError, ValueError):
            timeout_s = DEFAULT_TIMEOUT_S
    if force_gemini is None:
        force_gemini = os.environ.get("FORCE_GEMINI", "0") == "1"

    _State.api_key = api_key
    _State.base_url = base_url.rstrip("/")
    _State.model = model
    _State.timeout_s = timeout_s
    _State.force_gemini = force_gemini
    logger.info(
        "minimax_client configured: model=%s base_url=%s force_gemini=%s",
        _State.model, _State.base_url, _State.force_gemini,
    )


def get_api_key() -> Optional[str]:
    """Return the currently configured MiniMax API key (or None)."""
    if _State.api_key:
        return _State.api_key
    return os.environ.get("MINIMAX_API_KEY") or os.environ.get("minimax_api_key")


def _get_gemini_api_key() -> Optional[str]:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("gemini_api_key")


def has_gemini_fallback() -> bool:
    return bool(_get_gemini_api_key())


# --- HTTP layer: MiniMax -----------------------------------------------------

class _MiniMaxHTTPError(RuntimeError):
    pass


def _image_to_base64_block(path: Union[str, Path]) -> dict:
    """Encode a local image file as an Anthropic Messages image content block."""
    p = Path(path)
    mime, _ = mimetypes.guess_type(p.name)
    if mime is None:
        mime = "image/png"
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}


def _pil_image_to_temp_path(img: Any) -> str:
    """Save a PIL.Image (or anything with .save + .format) to a temp file.

    Returns the temp file path. Caller is responsible for unlinking.
    """
    fmt = (getattr(img, "format", None) or "PNG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    fd, tmp = tempfile.mkstemp(suffix=f".{fmt.lower()}")
    try:
        with os.fdopen(fd, "wb") as fh:
            img.save(fh, format=fmt)
        return tmp
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _build_messages_content(prompt: Any, images: Optional[Iterable[Union[str, Path]]] = None) -> list:
    """Combine a text prompt (str OR list of content blocks) with optional images.

    SmartTA existing call-site is:
        model.generate_content([prompt, img])   # prompt=string, img=PIL.Image
    So we accept:
      * prompt is a list: pass through, append images at the end
      * prompt is a string: convert to a single text block, append images
    PIL.Image objects are serialized as image content blocks using their
    `.tobytes()` payload (which is what the in-memory path needs).
    """
    if isinstance(prompt, list):
        content = list(prompt)
    else:
        content = [{"type": "text", "text": str(prompt)}]

    if images:
        for img in images:
            if img is None:
                continue
            if isinstance(img, (str, Path)):
                content.append(_image_to_base64_block(img))
            else:
                # PIL.Image or anything with .tobytes / .format
                fmt = (getattr(img, "format", None) or "PNG").upper()
                mime = f"image/{fmt.lower()}"
                if mime == "image/jpg":
                    mime = "image/jpeg"
                data = base64.standard_b64encode(img.tobytes()).decode("ascii")
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": data},
                })
    return content


def _post_anthropic(messages: list, *, max_tokens: int, temperature: float, system: Optional[str] = None) -> dict:
    """POST to MiniMax (Anthropic Messages endpoint) and return the parsed JSON body.

    Raises _MiniMaxHTTPError on any non-2xx (after retrying transient codes).
    Callers should catch this and fall back to Gemini if appropriate.
    """
    api_key = get_api_key()
    if not api_key:
        raise _MiniMaxHTTPError("MINIMAX_API_KEY not set. Set it in .env or call configure(api_key=...).")

    url = _State.base_url + "/v1/messages"
    payload: dict = {
        "model": _State.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    body = json.dumps(payload).encode("utf-8")
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Api-Key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=_State.timeout_s) as resp:
                raw = resp.read()
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = e.read()[:300].decode("utf-8", errors="replace")
            last_err = _MiniMaxHTTPError(f"HTTP {e.code}: {err_body}")
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(RETRY_BACKOFF_S ** attempt)
                continue
            raise last_err from e
        except urllib.error.URLError as e:
            last_err = _MiniMaxHTTPError(f"Network error: {e}")
            if attempt < 2:
                time.sleep(RETRY_BACKOFF_S ** attempt)
                continue
            raise last_err from e
    raise last_err or _MiniMaxHTTPError("MiniMax call failed without a captured error")


# --- Gemini fallback path ----------------------------------------------------

def _gemini_generate_text(prompt_text: str, *, max_tokens: int, temperature: float) -> str:
    """Call Gemini 2.5 Flash Lite (text-only).

    Auto-retries on truncation: if Gemini returns MAX_TOKENS or the
    output looks like it was cut off mid-JSON (no closing brace), we
    double max_tokens and try again. This matters because notes
    generation often produces 6-8k chars of structured content; the
    default 8192 cap is hit often and the resulting truncated JSON
    can't be parsed, leaving topics as raw text in the export.

    Raises RuntimeError if no Gemini key is set or the call fails.
    """
    api_key = _get_gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set; cannot fall back from MiniMax")
    model = os.environ.get("GEMINI_FALLBACK_MODEL", GEMINI_FALLBACK_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    text = ""
    for attempt in range(3):
        body = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_State.timeout_s) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        candidate = (result.get("candidates") or [{}])[0]
        finish_reason = candidate.get("finishReason", "")
        text = "".join(
            part.get("text", "")
            for part in candidate.get("content", {}).get("parts", [])
        ).strip()

        # MAX_TOKENS == "MAX_TOKENS" (4 in the enum, string in v1beta)
        # Recitation / safety stops also count -- if we got text at all,
        # accept it; if we got nothing, treat as truncation and retry.
        if finish_reason == "MAX_TOKENS":
            new_max = min(max_tokens * 2, 16384)
            logger.warning(
                "Gemini output hit max_tokens=%d (got %d chars); retrying with %d",
                max_tokens, len(text), new_max,
            )
            max_tokens = new_max
            continue

        # Heuristic: if the response looks like JSON (starts with { or [)
        # and the closing bracket is missing, it was almost certainly
        # truncated. Retry with more tokens.
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            open_b = stripped.count("{")
            close_b = stripped.count("}")
            open_k = stripped.count("[")
            close_k = stripped.count("]")
            if (open_b > close_b) or (open_k > close_k):
                new_max = min(max_tokens * 2, 16384)
                logger.warning(
                    "Gemini output looks truncated (braces unbalanced: "
                    "{=%d, }=%d, [=%d, ]=%d, %d chars); retrying with %d",
                    open_b, close_b, open_k, close_k, len(text), new_max,
                )
                max_tokens = new_max
                continue

        # Got real output -- return it.
        return text

    # All retries exhausted -- return whatever we got. The parser
    # will fall back to "raw text" handling.
    logger.error(
        "Gemini output still truncated after 3 retries (max_tokens=%d, %d chars)",
        max_tokens, len(text),
    )
    return text


def _gemini_generate_vision(prompt_text: str, image_paths: list, *, max_tokens: int, temperature: float) -> str:
    """Call Gemini 2.5 Flash Lite (text + 1+ images)."""
    api_key = _get_gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set; cannot fall back from MiniMax")
    model = os.environ.get("GEMINI_FALLBACK_MODEL", GEMINI_FALLBACK_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    parts: list = [{"text": prompt_text}]
    for img_path in image_paths:
        p = Path(img_path)
        if not p.exists():
            continue
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "image/png"
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=_State.timeout_s) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return "".join(
        part.get("text", "")
        for part in result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    ).strip()


# --- Gemini-compatible response object ---------------------------------------

class _GenerationResponse:
    """Mimics the google.generativeai.types.GenerateContentResponse surface.

    SmartTA only touches `.text` (and occasionally `.candidates`), so this
    is intentionally minimal. `.text` returns the concatenated text of all
    text content blocks in the first content array.
    """

    def __init__(self, text: str, raw: Optional[dict] = None, candidates: Optional[list] = None):
        self._text = text
        self._raw = raw or {}
        self.candidates = candidates or []

    @property
    def text(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"_GenerationResponse(text={self._text[:80]!r})"


def _extract_text_from_anthropic(result: dict) -> str:
    """Pull the concatenated text out of an Anthropic Messages response body.

    Also note: the result dict's "stop_reason" field tells callers
    whether the response was truncated by max_tokens. We don't change
    the public return type (just the text) so existing callers don't
    break, but readers can check result.get("stop_reason") after the
    call if they want to retry.
    """
    parts: list = []
    for block in (result.get("content") or []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()


# --- Gemini-compatible model wrapper -----------------------------------------

class GenerativeModel:
    """A stand-in for google.generativeai.GenerativeModel.

    SmartTA call-site is:
        response = model.generate_content(prompt, generation_config=..., request_options=...)

    GenerationConfig shape (gemini): {temperature, max_output_tokens}
    RequestOptions shape:           {timeout}    (we ignore -- use module timeout)

    We accept them and translate into Anthropic Messages parameters, with
    automatic Gemini fallback on 402/429/5xx (or when FORCE_GEMINI=1).
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name

    def generate_content(self,
                         contents: Any,
                         generation_config: Any = None,
                         request_options: Any = None,
                         **kwargs) -> _GenerationResponse:
        # Pull the prompt out of `contents`. Gemini accepts: str, list of
        # strings/parts, or a list mixing text + PIL.Image objects.
        if isinstance(contents, list):
            text_parts: list = []
            image_parts: list = []
            for item in contents:
                if isinstance(item, (str, Path)):
                    text_parts.append(str(item))
                else:
                    image_parts.append(item)
            prompt_text = "\n".join(text_parts).strip()
            images = image_parts if image_parts else None
        else:
            prompt_text = str(contents)
            images = None

        # Translate generation_config
        temperature = 0.7
        max_tokens = DEFAULT_MAX_TOKENS
        if generation_config is not None:
            if hasattr(generation_config, "temperature") and generation_config.temperature is not None:
                temperature = float(generation_config.temperature)
            if hasattr(generation_config, "max_output_tokens") and generation_config.max_output_tokens is not None:
                max_tokens = int(generation_config.max_output_tokens)
            if isinstance(generation_config, dict):
                if "temperature" in generation_config and generation_config["temperature"] is not None:
                    temperature = float(generation_config["temperature"])
                if "max_output_tokens" in generation_config and generation_config["max_output_tokens"] is not None:
                    max_tokens = int(generation_config["max_output_tokens"])
        if "temperature" in kwargs and kwargs["temperature"] is not None:
            temperature = float(kwargs["temperature"])
        if "max_output_tokens" in kwargs and kwargs["max_output_tokens"] is not None:
            max_tokens = int(kwargs["max_output_tokens"])

        # Optional per-call timeout (mirrors request_options={"timeout": X})
        saved_timeout = _State.timeout_s
        if request_options is not None and hasattr(request_options, "get"):
            per_timeout = request_options.get("timeout")
            if per_timeout:
                _State.timeout_s = float(per_timeout)
        try:
            text = self._call_with_fallback(
                prompt_text=prompt_text,
                images=images,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        finally:
            _State.timeout_s = saved_timeout
        return _GenerationResponse(text=text)

    def _call_with_fallback(self, *, prompt_text: str, images: Optional[list],
                            max_tokens: int, temperature: float) -> str:
        """Try MiniMax first; on 402/429/5xx or forced Gemini, fall back.

        If MiniMax returns 401 (bad/expired key), we permanently flip
        _State.force_gemini=True for this process. Otherwise every single
        request would log the same auth-failure warning and pay the
        network round-trip just to fall back to Gemini. Same logic for
        403 (forbidden -- the key works for nothing on this account).
        """
        use_gemini = _State.force_gemini or bool(images)  # vision always goes to Gemini

        if not use_gemini:
            try:
                content = _build_messages_content(prompt_text, None)
                messages = [{"role": "user", "content": content}]
                # Loop on max_tokens: MiniMax (Anthropic-compatible)
                # returns stop_reason="max_tokens" when the output got
                # cut off. The lecture-notes prompt commonly produces
                # 5-7k chars; the default 8192 cap is hit often, and
                # truncated JSON then fails the parser.
                max_t = max_tokens
                anthropic_text = ""
                for attempt in range(3):
                    raw = _post_anthropic(
                        messages, max_tokens=max_t, temperature=temperature
                    )
                    anthropic_text = _extract_text_from_anthropic(raw)
                    if raw.get("stop_reason") != "max_tokens":
                        return anthropic_text
                    # Heuristic: also check for unbalanced braces (in
                    # case stop_reason is wrong but the JSON is broken).
                    stripped = anthropic_text.strip()
                    if stripped.startswith(("{", "[")):
                        ob, cb = stripped.count("{"), stripped.count("}")
                        ok, ck = stripped.count("["), stripped.count("]")
                        if ob == cb and ok == ck:
                            return anthropic_text
                    new_max = min(max_t * 2, 16384)
                    logger.warning(
                        "MiniMax output hit max_tokens=%d (got %d chars); "
                        "retrying with %d", max_t, len(anthropic_text), new_max,
                    )
                    max_t = new_max
                return anthropic_text
            except _MiniMaxHTTPError as e:
                # 401 / 403: the API key is dead for this account. Stop
                # trying it for the rest of the process -- every other
                # call would just re-fail the same way.
                err_str = str(e)
                if "HTTP 401" in err_str or "HTTP 403" in err_str:
                    if not _State.force_gemini:
                        logger.warning(
                            "MiniMax call failed (%s); disabling MiniMax for this "
                            "session and routing all subsequent calls to Gemini",
                            e,
                        )
                    _State.force_gemini = True
                else:
                    logger.warning("MiniMax call failed (%s); falling back to Gemini", e)
            except Exception as e:
                logger.warning("MiniMax call errored (%s: %s); falling back to Gemini", type(e).__name__, e)

        # Fallback to Gemini
        if not has_gemini_fallback():
            raise RuntimeError(
                "MiniMax call failed and GEMINI_API_KEY is not set for fallback. "
                "Set GEMINI_API_KEY in .env, or top up your MiniMax balance."
            )
        if images:
            image_paths: list = []
            try:
                for img in images:
                    if isinstance(img, (str, Path)):
                        image_paths.append(str(img))
                    else:
                        image_paths.append(_pil_image_to_temp_path(img))
                return _gemini_generate_vision(
                    prompt_text, image_paths, max_tokens=max_tokens, temperature=temperature
                )
            finally:
                for p in image_paths:
                    # Only delete files we created (anything inside tempfile.gettempdir)
                    try:
                        if tempfile.gettempdir() in str(p):
                            os.unlink(p)
                    except OSError:
                        pass
        return _gemini_generate_text(prompt_text, max_tokens=max_tokens, temperature=temperature)


__all__ = [
    "configure",
    "get_api_key",
    "GenerativeModel",
    "has_gemini_fallback",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "types",  # exposes a Gemini-compatible types.GenerationConfig
]


# Auto-configure from environment on first import so the rest of SmartTA
# can `import minimax_client as genai` and start using it without an
# explicit configure() call. This mirrors google-generativeai behavior.
_configured = False


def _maybe_autoconfigure() -> None:
    global _configured
    if _configured:
        return
    env_key = (os.environ.get("MINIMAX_API_KEY") or os.environ.get("minimax_api_key"))
    if env_key:
        configure(api_key=env_key)
        _configured = True
    else:
        # No MiniMax key -- still configure so _State has sensible defaults.
        configure()


_maybe_autoconfigure()


# --- Gemini-compatible `types` submodule -----------------------------------
#
# `google.generativeai.types.GenerationConfig` is the standard way to pass
# temperature / max_output_tokens. SmartTA uses it in `_base.py`. We expose
# a tiny class with the same attribute names so the existing call-sites
# work unchanged. We also expose `types.GenerationConfig` as both a class
# and a factory (so both `genai.types.GenerationConfig(temperature=...)` and
# `from ... import types; types.GenerationConfig(...)` work).

class _SimpleNamespace:
    """Minimal stand-in for argparse.Namespace / types.SimpleNamespace.

    Only used as the backing object for GenerationConfig so attribute
    access works the way Gemini's class does.
    """
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _TypesModule:
    """Drop-in replacement for `google.generativeai.types`.

    Currently only exposes `GenerationConfig`. Extend if SmartTA starts
    using more types (SafetySettings, Tool, etc.).
    """

    class GenerationConfig(_SimpleNamespace):
        """Mirrors google.generativeai.types.GenerationConfig.

        Accepts any kwargs (temperature, max_output_tokens, top_p, top_k,
        stop_sequences, ...) and stashes them as attributes. Our
        `GenerativeModel.generate_content` reads `.temperature` and
        `.max_output_tokens`.
        """
        pass


# Expose as both `minimax_client.types` (attribute) and a top-level
# import path. The attribute form is what SmartTA uses.
import sys as _sys
types = _TypesModule()
_sys.modules[__name__ + ".types"] = types
