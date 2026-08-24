import logging
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


_LANGUAGE_NAMES = {
    "ja": "Japanese",
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ko": "Korean",
}


def _lang_name(code: str) -> str:
    code = (code or "").strip().lower()
    return _LANGUAGE_NAMES.get(code, code or "")


def _merged_retry_config(config: Dict[str, Any], service_key: str) -> Dict[str, Any]:
    translation_cfg = config.get("translation", {}) or {}
    base = translation_cfg.get("retry", {}) or {}
    service_cfg = translation_cfg.get(service_key, {}) or {}
    merged = dict(base)
    merged.update({k: v for k, v in service_cfg.items() if v is not None and k in base})
    return merged


def _retry_with_backoff(*, logger: logging.Logger, max_retries: int, initial_delay_seconds: float,
                         max_delay_seconds: float, backoff_multiplier: float, jitter_ratio: float,
                         is_retryable, get_retry_after_seconds, op):
    delay = max(0.0, float(initial_delay_seconds))
    for attempt in range(int(max_retries) + 1):
        try:
            return op()
        except Exception as e:
            if attempt >= int(max_retries) or not is_retryable(e):
                raise
            retry_after = None
            try:
                retry_after = get_retry_after_seconds(e)
            except Exception:
                pass
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except Exception:
                    pass
            jitter = delay * float(jitter_ratio) * (2.0 * random.random() - 1.0)
            sleep_s = max(0.0, min(float(max_delay_seconds), delay + jitter))
            logger.warning(f"Rate/transient error; retrying in {sleep_s:.2f}s (attempt {attempt + 1}/{max_retries}): {e}")
            time.sleep(sleep_s)
            delay = min(float(max_delay_seconds), max(0.0, delay) * float(backoff_multiplier))


@dataclass
class TranslationSegment:
    original_text: str
    translated_text: str


@dataclass
class TranslationResult:
    success: bool
    segments: Optional[List[TranslationSegment]] = None
    error: Optional[str] = None


class Translator(ABC):
    @abstractmethod
    def translate_segments(self, segments: List[str], source_lang: str, target_lang: str) -> TranslationResult:
        ...


class DeepLTranslator(Translator):
    """DeepL API translator. No custom-prompt support (DeepL does not expose one)."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        translation_cfg = config.get("translation", {}) or {}
        self.api_key = translation_cfg.get("api_keys", {}).get("deepl") or os.environ.get("DEEPL_API_KEY")
        plan = str((translation_cfg.get("deepl") or {}).get("plan", "free")).lower()
        self.api_url = (
            "https://api-free.deepl.com/v2/translate" if plan == "free" else "https://api.deepl.com/v2/translate"
        )
        if not self.api_key:
            self.logger.warning("DeepL API key not configured")

    def translate_segments(self, segments: List[str], source_lang: str, target_lang: str) -> TranslationResult:
        if not self.api_key:
            return TranslationResult(success=False, error="DeepL API key not configured")

        retry_cfg = _merged_retry_config(self.config, "deepl")
        max_retries = int(retry_cfg.get("max_retries", 8))
        initial_delay = float(retry_cfg.get("initial_delay_seconds", 2.0))
        max_delay = float(retry_cfg.get("max_delay_seconds", 60.0))
        backoff_mult = float(retry_cfg.get("backoff_multiplier", 2.0))
        jitter_ratio = float(retry_cfg.get("jitter_ratio", 0.2))

        results: List[TranslationSegment] = []
        try:
            for i, text in enumerate(segments):
                if not text.strip():
                    results.append(TranslationSegment(original_text=text, translated_text=""))
                    continue

                def _do_request():
                    headers = {"Authorization": f"DeepL-Auth-Key {self.api_key}"}
                    data = {
                        "text": text,
                        "source_lang": source_lang.upper(),
                        "target_lang": target_lang.upper(),
                    }
                    resp = requests.post(self.api_url, headers=headers, data=data, timeout=30)
                    resp.raise_for_status()
                    return resp.json()

                def _is_retryable(e: Exception) -> bool:
                    if isinstance(e, requests.exceptions.HTTPError):
                        status = e.response.status_code if e.response is not None else None
                        return status in (429, 500, 502, 503, 504)
                    return isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))

                def _retry_after(e: Exception) -> Optional[float]:
                    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                        ra = e.response.headers.get("Retry-After")
                        return float(ra) if ra else None
                    return None

                self.logger.info(f"Translating segment {i + 1}/{len(segments)} with DeepL")
                payload = _retry_with_backoff(
                    logger=self.logger,
                    max_retries=max_retries,
                    initial_delay_seconds=initial_delay,
                    max_delay_seconds=max_delay,
                    backoff_multiplier=backoff_mult,
                    jitter_ratio=jitter_ratio,
                    is_retryable=_is_retryable,
                    get_retry_after_seconds=_retry_after,
                    op=_do_request,
                )
                translated = payload["translations"][0]["text"]
                results.append(TranslationSegment(original_text=text, translated_text=translated))

            return TranslationResult(success=True, segments=results)
        except Exception as e:
            self.logger.error(f"DeepL translation error: {e}")
            return TranslationResult(success=False, error=str(e), segments=results or None)


class OpenAITranslator(Translator):
    """OpenAI LLM-based translator, with customizable system/user prompts."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        translation_cfg = config.get("translation", {}) or {}
        self.api_key = translation_cfg.get("api_keys", {}).get("openai") or os.environ.get("OPENAI_API_KEY")
        openai_cfg = translation_cfg.get("openai", {}) or {}
        self.model = openai_cfg.get("model") or "gpt-4o-mini"
        self.batch_size = int(openai_cfg.get("batch_size", 1)) or 1

        custom_prompts = translation_cfg.get("custom_prompts", {}) or {}
        system_prompt = str(custom_prompts.get("system_prompt") or "").strip()
        system_prompt_extended = str(custom_prompts.get("system_prompt_extended") or "").strip()
        # system_prompt_extended is a project-specific complement to system_prompt, not a replacement.
        self.system_prompt = (
            f"{system_prompt}\n\n{system_prompt_extended}" if system_prompt_extended else system_prompt
        ) or "Translate the following text accurately while preserving the original meaning and tone."
        self.user_prompt_template = custom_prompts.get("user_prompt_template", "{text}")

        if not self.api_key:
            self.logger.warning("OpenAI API key not configured")

    def _context(self, source_lang: str, target_lang: str) -> _SafeDict:
        runtime = self.config.get("_runtime", {}) or {}
        return _SafeDict(
            source_language=source_lang,
            target_language=target_lang,
            source_language_name=_lang_name(source_lang),
            target_language_name=_lang_name(target_lang),
            video_title=str(runtime.get("video_title") or ""),
            video_filename=str(runtime.get("video_filename") or ""),
        )

    def translate_segments(self, segments: List[str], source_lang: str, target_lang: str) -> TranslationResult:
        if not self.api_key:
            return TranslationResult(success=False, error="OpenAI API key not configured")

        import openai

        # max_retries=0: retry/backoff is handled entirely by _retry_with_backoff below
        # (with configurable delays/jitter/Retry-After handling). Leaving the SDK's own
        # built-in retry (default 2, on 429/5xx/connection errors) enabled would silently
        # stack extra attempts/delays on top of the configured retry policy.
        client = openai.OpenAI(api_key=self.api_key, max_retries=0)
        ctx = self._context(source_lang, target_lang)
        system_prompt = self.system_prompt.format_map(ctx)

        retry_cfg = _merged_retry_config(self.config, "openai")
        max_retries = int(retry_cfg.get("max_retries", 8))
        initial_delay = float(retry_cfg.get("initial_delay_seconds", 2.0))
        max_delay = float(retry_cfg.get("max_delay_seconds", 60.0))
        backoff_mult = float(retry_cfg.get("backoff_multiplier", 2.0))
        jitter_ratio = float(retry_cfg.get("jitter_ratio", 0.2))

        def _is_retryable(e: Exception) -> bool:
            status = getattr(e, "status_code", None)
            return status in (429, 500, 502, 503, 504) or "rate limit" in str(e).lower()

        def _retry_after(e: Exception) -> Optional[float]:
            return None

        def _request_batch(batch: List[str]) -> List[str]:
            # Numbered list so the model can keep segments aligned 1:1 in its response.
            numbered_input = "\n".join(f"{j + 1}. {text}" for j, text in enumerate(batch))
            user_prompt = self.user_prompt_template.format_map(_SafeDict(**ctx, text=numbered_input))

            def _do_request():
                return client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )

            response = _retry_with_backoff(
                logger=self.logger,
                max_retries=max_retries,
                initial_delay_seconds=initial_delay,
                max_delay_seconds=max_delay,
                backoff_multiplier=backoff_mult,
                jitter_ratio=jitter_ratio,
                is_retryable=_is_retryable,
                get_retry_after_seconds=_retry_after,
                op=_do_request,
            )
            content = response.choices[0].message.content or ""
            lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
            # Strip a leading "N." numbering if the model echoed it back.
            cleaned = []
            for ln in lines:
                parts = ln.split(".", 1)
                if len(parts) == 2 and parts[0].strip().isdigit():
                    cleaned.append(parts[1].strip())
                else:
                    cleaned.append(ln)
            return cleaned

        results: List[TranslationSegment] = []
        try:
            batches = [segments[i:i + self.batch_size] for i in range(0, len(segments), self.batch_size)]
            done = 0
            for batch in batches:
                self.logger.info(f"Translating segments {done + 1}-{done + len(batch)}/{len(segments)} with OpenAI")
                cleaned = _request_batch(batch)

                if len(batch) > 1 and len(cleaned) != len(batch):
                    # The model sometimes merges/splits fragmented lines instead of respecting
                    # the numbered 1:1 format, which would silently misalign translations with
                    # their segments. Retranslate this batch one segment at a time instead.
                    self.logger.warning(
                        f"OpenAI returned {len(cleaned)} line(s) for a batch of {len(batch)} segments "
                        f"(segments {done + 1}-{done + len(batch)}/{len(segments)}); "
                        "retranslating this batch one segment at a time to keep alignment."
                    )
                    cleaned = []
                    for text in batch:
                        single = _request_batch([text])
                        cleaned.append(single[0] if single else text)

                for j, original in enumerate(batch):
                    translated = cleaned[j] if j < len(cleaned) else original
                    results.append(TranslationSegment(original_text=original, translated_text=translated))
                done += len(batch)

            return TranslationResult(success=True, segments=results)
        except Exception as e:
            self.logger.error(f"OpenAI translation error: {e}")
            return TranslationResult(success=False, error=str(e), segments=results or None)


class TranslatorFactory:
    @staticmethod
    def create_translator(config: Dict[str, Any]) -> Translator:
        service = (config.get("translation", {}) or {}).get("service", "deepl").lower()
        if service == "deepl":
            return DeepLTranslator(config)
        if service == "openai":
            return OpenAITranslator(config)
        raise ValueError(f"Unsupported translation service: {service} (supported: deepl, openai)")
