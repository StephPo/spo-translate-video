import os
import logging
import random
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from abc import ABC, abstractmethod
import time
import requests


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _lang_name(lang_code: str) -> str:
    code = (lang_code or "").strip().lower()
    mapping = {
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
    return mapping.get(code, code or "")


def _merged_retry_config(config: Dict[str, Any], service_key: str) -> Dict[str, Any]:
    translation_cfg = config.get('translation', {}) if isinstance(config.get('translation', {}), dict) else {}
    base = translation_cfg.get('retry', {}) if isinstance(translation_cfg.get('retry', {}), dict) else {}
    service_cfg = translation_cfg.get(service_key, {}) if isinstance(translation_cfg.get(service_key, {}), dict) else {}

    merged = dict(base)
    merged.update({k: v for k, v in service_cfg.items() if v is not None})
    return merged


def _retry_with_backoff(
    *,
    logger: logging.Logger,
    max_retries: int,
    initial_delay_seconds: float,
    max_delay_seconds: float,
    backoff_multiplier: float,
    jitter_ratio: float,
    is_retryable,
    get_retry_after_seconds,
    op,
):
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
                retry_after = None

            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except Exception:
                    pass

            jitter = delay * float(jitter_ratio) * (2.0 * random.random() - 1.0)
            sleep_s = max(0.0, min(float(max_delay_seconds), delay + jitter))
            logger.warning(
                f"Rate/transient error; retrying in {sleep_s:.2f}s (attempt {attempt + 1}/{max_retries}): {e}"
            )
            time.sleep(sleep_s)
            delay = min(float(max_delay_seconds), max(0.0, delay) * float(backoff_multiplier))

@dataclass
class TranslationSegment:
    original_text: str
    translated_text: str
    source_language: str
    target_language: str
    confidence: float
    processing_time: float

@dataclass
class TranslationResult:
    success: bool
    segments: Optional[List[TranslationSegment]] = None
    full_translated_text: Optional[str] = None
    error: Optional[str] = None
    total_processing_time: Optional[float] = None

class Translator(ABC):
    """Abstract base class for translation services"""
    
    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> TranslationResult:
        pass
    
    @abstractmethod
    def translate_segments(self, segments: List[str], source_lang: str, target_lang: str) -> TranslationResult:
        pass

class GoogleTranslateTranslator(Translator):
    """Google Translate API translator"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.translation_config = config.get('translation', {})
        
        # API settings
        self.api_key = self.translation_config.get('api_keys', {}).get('google_translate') or os.environ.get('GOOGLE_TRANSLATE_API_KEY')
        self.quality = self.translation_config.get('quality', 'standard')
        self.preserve_formatting = self.translation_config.get('preserve_formatting', True)
        
        # Custom prompts
        self.custom_prompts = self.translation_config.get('custom_prompts', {})
        
        # API endpoint
        self.api_url = "https://translation.googleapis.com/language/translate/v2"
        
        if not self.api_key:
            self.logger.warning("Google Translate API key not configured")
    
    def translate(self, text: str, source_lang: str, target_lang: str) -> TranslationResult:
        """Translate single text using Google Translate"""
        start_time = time.time()
        
        try:
            if not self.api_key:
                return TranslationResult(
                    success=False, 
                    error="Google Translate API key not configured"
                )
            
            # Prepare request
            data = {
                'q': text,
                'source': source_lang,
                'target': target_lang,
                'format': 'text' if self.preserve_formatting else 'plain'
            }
            
            # Add quality parameter if specified
            if self.quality == 'high_quality':
                data['model'] = 'base'
            
            params = {'key': self.api_key}
            
            # Make API request
            response = requests.post(self.api_url, params=params, data=data)
            response.raise_for_status()
            
            result = response.json()
            
            # Extract translation
            if 'data' in result and 'translations' in result['data']:
                translation = result['data']['translations'][0]
                translated_text = translation['translatedText']
                
                processing_time = time.time() - start_time
                
                return TranslationResult(
                    success=True,
                    segments=[TranslationSegment(
                        original_text=text,
                        translated_text=translated_text,
                        source_language=source_lang,
                        target_language=target_lang,
                        confidence=0.9,  # Google doesn't provide confidence scores
                        processing_time=processing_time
                    )],
                    full_translated_text=translated_text,
                    total_processing_time=processing_time
                )
            else:
                return TranslationResult(
                    success=False, 
                    error="Invalid response from Google Translate API"
                )
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Google Translate API error: {str(e)}")
            return TranslationResult(success=False, error=f"API error: {str(e)}")
        except Exception as e:
            self.logger.error(f"Translation error: {str(e)}")
            return TranslationResult(success=False, error=str(e))
    
    def translate_segments(self, segments: List[str], source_lang: str, target_lang: str) -> TranslationResult:
        """Translate multiple text segments"""
        start_time = time.time()
        translation_segments = []
        full_translated_text = ""
        
        try:
            for i, segment in enumerate(segments):
                if not segment.strip():
                    continue
                
                self.logger.info(f"Translating segment {i+1}/{len(segments)}")
                
                result = self.translate(segment, source_lang, target_lang)
                
                if result.success and result.segments:
                    translation_segment = result.segments[0]
                    translation_segments.append(translation_segment)
                    full_translated_text += translation_segment.translated_text + " "
                else:
                    self.logger.warning(f"Failed to translate segment {i+1}: {result.error}")
                    # Add original text as fallback
                    translation_segments.append(TranslationSegment(
                        original_text=segment,
                        translated_text=segment,  # Fallback to original
                        source_language=source_lang,
                        target_language=target_lang,
                        confidence=0.0,
                        processing_time=0.0
                    ))
                    full_translated_text += segment + " "
            
            total_processing_time = time.time() - start_time
            
            return TranslationResult(
                success=True,
                segments=translation_segments,
                full_translated_text=full_translated_text.strip(),
                total_processing_time=total_processing_time
            )
            
        except Exception as e:
            self.logger.error(f"Error translating segments: {str(e)}")
            return TranslationResult(success=False, error=str(e))

class DeepLTranslator(Translator):
    """DeepL API translator"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.translation_config = config.get('translation', {})
        deepl_cfg = self.translation_config.get('deepl', {}) if isinstance(self.translation_config.get('deepl', {}), dict) else {}
        
        # API settings
        self.api_key = self.translation_config.get('api_keys', {}).get('deepl') or os.environ.get('DEEPL_API_KEY')
        self.preserve_formatting = self.translation_config.get('preserve_formatting', True)
        
        # API endpoint
        plan = str(deepl_cfg.get('plan', 'free')).strip().lower()
        if plan == 'pro':
            self.api_url = "https://api.deepl.com/v2/translate"
        else:
            self.api_url = "https://api-free.deepl.com/v2/translate"  # Free API URL
        
        if not self.api_key:
            self.logger.warning("DeepL API key not configured")
    
    def translate(self, text: str, source_lang: str, target_lang: str) -> TranslationResult:
        """Translate single text using DeepL"""
        start_time = time.time()
        
        try:
            if not self.api_key:
                return TranslationResult(
                    success=False, 
                    error="DeepL API key not configured"
                )
            
            # Map language codes to DeepL format
            lang_mapping = {
                'ja': 'JA',
                'fr': 'FR',
                'en': 'EN'
            }
            
            source_lang = lang_mapping.get(source_lang, source_lang.upper())
            target_lang = lang_mapping.get(target_lang, target_lang.upper())
            
            # Prepare request
            data = {
                'text': text,
                'source_lang': source_lang,
                'target_lang': target_lang,
                'preserve_formatting': str(self.preserve_formatting).lower()
            }
            
            headers = {
                'Authorization': f'DeepL-Auth-Key {self.api_key}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            retry_cfg = _merged_retry_config(self.config, 'deepl')
            max_retries = int(retry_cfg.get('max_retries', 5))
            initial_delay_seconds = float(retry_cfg.get('initial_delay_seconds', 1.0))
            max_delay_seconds = float(retry_cfg.get('max_delay_seconds', 30.0))
            backoff_multiplier = float(retry_cfg.get('backoff_multiplier', 2.0))
            jitter_ratio = float(retry_cfg.get('jitter_ratio', 0.2))

            def _is_retryable(e: Exception) -> bool:
                if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
                    return True
                if isinstance(e, requests.exceptions.HTTPError):
                    status = getattr(getattr(e, 'response', None), 'status_code', None)
                    return status in (408, 429, 500, 502, 503, 504)
                return False

            def _retry_after(e: Exception) -> Optional[float]:
                if isinstance(e, requests.exceptions.HTTPError) and getattr(e, 'response', None) is not None:
                    ra = e.response.headers.get('Retry-After')
                    if ra is not None:
                        return float(ra)
                return None

            def _op():
                resp = requests.post(self.api_url, headers=headers, data=data)
                resp.raise_for_status()
                return resp

            response = _retry_with_backoff(
                logger=self.logger,
                max_retries=max_retries,
                initial_delay_seconds=initial_delay_seconds,
                max_delay_seconds=max_delay_seconds,
                backoff_multiplier=backoff_multiplier,
                jitter_ratio=jitter_ratio,
                is_retryable=_is_retryable,
                get_retry_after_seconds=_retry_after,
                op=_op,
            )
            
            result = response.json()
            
            # Extract translation
            if 'translations' in result and len(result['translations']) > 0:
                translation = result['translations'][0]
                translated_text = translation['text']
                
                processing_time = time.time() - start_time
                
                return TranslationResult(
                    success=True,
                    segments=[TranslationSegment(
                        original_text=text,
                        translated_text=translated_text,
                        source_language=source_lang,
                        target_language=target_lang,
                        confidence=0.95,  # DeepL typically provides high quality
                        processing_time=processing_time
                    )],
                    full_translated_text=translated_text,
                    total_processing_time=processing_time
                )
            else:
                return TranslationResult(
                    success=False, 
                    error="Invalid response from DeepL API"
                )
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"DeepL API error: {str(e)}")
            return TranslationResult(success=False, error=f"API error: {str(e)}")
        except Exception as e:
            self.logger.error(f"Translation error: {str(e)}")
            return TranslationResult(success=False, error=str(e))
    
    def translate_segments(self, segments: List[str], source_lang: str, target_lang: str) -> TranslationResult:
        """Translate multiple text segments"""
        start_time = time.time()
        translation_segments = []
        full_translated_text = ""
        
        try:
            for i, segment in enumerate(segments):
                if not segment.strip():
                    continue
                
                self.logger.info(f"Translating segment {i+1}/{len(segments)} with DeepL")
                
                result = self.translate(segment, source_lang, target_lang)
                
                if result.success and result.segments:
                    translation_segment = result.segments[0]
                    translation_segments.append(translation_segment)
                    full_translated_text += translation_segment.translated_text + " "
                else:
                    self.logger.warning(f"Failed to translate segment {i+1}: {result.error}")
                    # Add original text as fallback
                    translation_segments.append(TranslationSegment(
                        original_text=segment,
                        translated_text=segment,  # Fallback to original
                        source_language=source_lang,
                        target_language=target_lang,
                        confidence=0.0,
                        processing_time=0.0
                    ))
                    full_translated_text += segment + " "
            
            total_processing_time = time.time() - start_time
            
            return TranslationResult(
                success=True,
                segments=translation_segments,
                full_translated_text=full_translated_text.strip(),
                total_processing_time=total_processing_time
            )
            
        except Exception as e:
            self.logger.error(f"Error translating segments with DeepL: {str(e)}")
            return TranslationResult(success=False, error=str(e))

class OpenAITranslator(Translator):
    """OpenAI GPT-based translator"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.translation_config = config.get('translation', {})
        
        # API settings
        self.api_key = self.translation_config.get('api_keys', {}).get('openai') or os.environ.get('OPENAI_API_KEY')
        openai_cfg = self.translation_config.get('openai', {}) if isinstance(self.translation_config.get('openai', {}), dict) else {}
        self.model = (
            openai_cfg.get('model')
            or self.translation_config.get('openai_model')
            or "gpt-4o-mini"
        )
        
        # Custom prompts
        self.custom_prompts = self.translation_config.get('custom_prompts', {})
        self.system_prompt = self.custom_prompts.get('system_prompt', 
            "Translate the following text accurately while preserving the original meaning and tone.")
        self.user_prompt_template = self.custom_prompts.get('user_prompt_template', "{text}")
        
        if not self.api_key:
            self.logger.warning("OpenAI API key not configured")
    
    def translate(self, text: str, source_lang: str, target_lang: str) -> TranslationResult:
        """Translate single text using OpenAI"""
        start_time = time.time()
        
        try:
            if not self.api_key:
                return TranslationResult(
                    success=False, 
                    error="OpenAI API key not configured"
                )
            
            import openai
            
            client = openai.OpenAI(api_key=self.api_key)
            
            # Prepare messages
            ctx = _SafeDict(
                text=text,
                source_language=source_lang,
                target_language=target_lang,
                source_language_name=_lang_name(source_lang),
                target_language_name=_lang_name(target_lang),
            )

            system_prompt = str(self.system_prompt).format_map(ctx)
            user_prompt = str(self.user_prompt_template).format_map(ctx)
            
            messages = [
                {"role": "system", "content": f"{system_prompt} Translate from {source_lang} to {target_lang}."},
                {"role": "user", "content": user_prompt}
            ]

            retry_cfg = _merged_retry_config(self.config, 'openai')
            max_retries = int(retry_cfg.get('max_retries', 5))
            initial_delay_seconds = float(retry_cfg.get('initial_delay_seconds', 1.0))
            max_delay_seconds = float(retry_cfg.get('max_delay_seconds', 30.0))
            backoff_multiplier = float(retry_cfg.get('backoff_multiplier', 2.0))
            jitter_ratio = float(retry_cfg.get('jitter_ratio', 0.2))

            def _is_retryable(e: Exception) -> bool:
                try:
                    # OpenAI Python SDK (v1) exceptions
                    if isinstance(e, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)):
                        return True
                    if isinstance(e, openai.InternalServerError):
                        return True
                    if isinstance(e, openai.APIStatusError):
                        return getattr(e, 'status_code', None) in (408, 429, 500, 502, 503, 504)
                except Exception:
                    pass
                return False

            def _retry_after(e: Exception) -> Optional[float]:
                try:
                    headers = getattr(e, 'response', None)
                    if headers is not None:
                        ra = getattr(headers, 'headers', None)
                        if ra is not None:
                            val = ra.get('Retry-After')
                            if val is not None:
                                return float(val)
                except Exception:
                    return None
                return None

            def _op():
                return client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,  # Lower temperature for more consistent translations
                    max_tokens=1000
                )

            response = _retry_with_backoff(
                logger=self.logger,
                max_retries=max_retries,
                initial_delay_seconds=initial_delay_seconds,
                max_delay_seconds=max_delay_seconds,
                backoff_multiplier=backoff_multiplier,
                jitter_ratio=jitter_ratio,
                is_retryable=_is_retryable,
                get_retry_after_seconds=_retry_after,
                op=_op,
            )
            
            translated_text = response.choices[0].message.content.strip()
            
            processing_time = time.time() - start_time
            
            return TranslationResult(
                success=True,
                segments=[TranslationSegment(
                    original_text=text,
                    translated_text=translated_text,
                    source_language=source_lang,
                    target_language=target_lang,
                    confidence=0.9,  # OpenAI doesn't provide confidence scores
                    processing_time=processing_time
                )],
                full_translated_text=translated_text,
                total_processing_time=processing_time
            )
                
        except ImportError:
            return TranslationResult(
                success=False, 
                error="OpenAI library not installed. Install with: pip install openai"
            )
        except Exception as e:
            self.logger.error(f"OpenAI translation error: {str(e)}")
            return TranslationResult(success=False, error=str(e))
    
    def translate_segments(self, segments: List[str], source_lang: str, target_lang: str) -> TranslationResult:
        """Translate multiple text segments"""
        start_time = time.time()
        translation_segments = []
        full_translated_text = ""
        
        try:
            for i, segment in enumerate(segments):
                if not segment.strip():
                    continue
                
                self.logger.info(f"Translating segment {i+1}/{len(segments)} with OpenAI")
                
                result = self.translate(segment, source_lang, target_lang)
                
                if result.success and result.segments:
                    translation_segment = result.segments[0]
                    translation_segments.append(translation_segment)
                    full_translated_text += translation_segment.translated_text + " "
                else:
                    self.logger.warning(f"Failed to translate segment {i+1}: {result.error}")
                    # Add original text as fallback
                    translation_segments.append(TranslationSegment(
                        original_text=segment,
                        translated_text=segment,  # Fallback to original
                        source_language=source_lang,
                        target_language=target_lang,
                        confidence=0.0,
                        processing_time=0.0
                    ))
                    full_translated_text += segment + " "
            
            total_processing_time = time.time() - start_time
            
            return TranslationResult(
                success=True,
                segments=translation_segments,
                full_translated_text=full_translated_text.strip(),
                total_processing_time=total_processing_time
            )
            
        except Exception as e:
            self.logger.error(f"Error translating segments with OpenAI: {str(e)}")
            return TranslationResult(success=False, error=str(e))

class TranslatorFactory:
    """Factory for creating translators"""
    
    @staticmethod
    def create_translator(config: Dict[str, Any]) -> Translator:
        service = config.get('translation', {}).get('service', 'google_translate').lower()
        
        if service == 'google_translate':
            return GoogleTranslateTranslator(config)
        elif service == 'deepl':
            return DeepLTranslator(config)
        elif service == 'openai':
            return OpenAITranslator(config)
        else:
            raise ValueError(f"Unsupported translation service: {service}")

def main():
    """Test function for translator"""
    logging.basicConfig(level=logging.INFO)
    
    # Sample config
    config = {
        'translation': {
            'service': 'google_translate',
            'source_language': 'ja',
            'target_language': 'fr',
            'quality': 'standard',
            'preserve_formatting': True,
            'api_keys': {
                'google_translate': os.environ.get('GOOGLE_TRANSLATE_API_KEY', ''),
                'deepl': os.environ.get('DEEPL_API_KEY', ''),
                'openai': os.environ.get('OPENAI_API_KEY', '')
            }
        }
    }
    
    # Create translator
    translator = TranslatorFactory.create_translator(config)
    
    # Test translation
    test_text = "こんにちは、元気ですか？"
    result = translator.translate(test_text, 'ja', 'fr')
    
    if result.success:
        print(f"Translation successful: {result.full_translated_text}")
    else:
        print(f"Error: {result.error}")
    
    print("Translator ready for use")

if __name__ == "__main__":
    main()
