"""
AI 分類器（支援 Groq 與 Gemini，可在管理介面切換）

Groq：免費，14,400 req/day，使用 Llama-3.3-70B
Gemini：google-genai SDK（v1 API），gemini-2.0-flash
"""

import json
import logging
import re
import time
from typing import Optional

from .config import get_llm_config

logger = logging.getLogger(__name__)

# ── 合法分類選項 ────────────────────────────────────────────
VALID_ATTACK_TYPES = [
    "勒索軟體", "網路釣魚", "DDoS攻擊", "零日漏洞", "APT攻擊",
    "資料外洩", "供應鏈攻擊", "社交工程", "惡意軟體", "系統漏洞",
    "身份詐騙", "加密貨幣詐騙", "其他",
]
VALID_REGIONS = [
    "台灣", "中國大陸", "香港澳門", "其他亞太", "北美", "歐洲",
    "中東", "東南亞", "日本韓國", "全球", "不明",
]
VALID_SYSTEMS = [
    "Windows", "Linux", "macOS", "Web應用程式", "雲端服務",
    "IoT設備", "工業控制系統", "行動裝置", "網路設備",
    "資料庫", "瀏覽器", "電子郵件系統", "其他",
]
VALID_SEVERITIES = ["嚴重", "高", "中", "低"]
MAX_SUMMARY_LEN  = 150

# ── 提示詞 ──────────────────────────────────────────────────
_PROMPT_TEMPLATE = """\
你是資安分析師。請分析以下新聞，回傳 JSON 物件，包含以下欄位：
- attack_type：攻擊類型，必須從選項中選一個
- region：主要受影響地區，必須從選項中選一個
- affected_system：受影響系統，必須從選項中選一個
- severity：嚴重程度（嚴重/高/中/低）
- summary：150字以內繁體中文摘要，說明攻擊對象、手法與影響

【新聞標題】{title}
【新聞來源】{source}
【新聞內容】{content}

攻擊類型選項：勒索軟體、網路釣魚、DDoS攻擊、零日漏洞、APT攻擊、資料外洩、供應鏈攻擊、社交工程、惡意軟體、系統漏洞、身份詐騙、加密貨幣詐騙、其他
地區選項：台灣、中國大陸、香港澳門、其他亞太、北美、歐洲、中東、東南亞、日本韓國、全球、不明
系統選項：Windows、Linux、macOS、Web應用程式、雲端服務、IoT設備、工業控制系統、行動裝置、網路設備、資料庫、瀏覽器、電子郵件系統、其他
嚴重程度：嚴重=關鍵基礎設施/大量受害/CVSS≥9；高=重要系統/已利用/CVSS 7-9；中=有限影響/CVSS 4-7；低=資訊性/無受害者

請直接回傳 JSON，不要包含任何其他文字。"""


# ── 主分類函式 ────────────────────────────────────────────────

def classify_article(title: str, content: str, source: str = "") -> dict:
    """
    使用設定的 LLM 供應商（Groq 或 Gemini）分類一篇新聞。
    保證回傳 dict，失敗時回傳 fallback。
    """
    track_id = f"{(source or '?')[:8]}#{abs(hash(title)) % 9999:04d}"

    try:
        cfg      = get_llm_config()
        provider = cfg.get("provider", "groq").lower()
    except Exception as e:
        logger.error(f"[{track_id}] 讀取設定失敗：{e}")
        return _fallback_classification()

    prompt = _build_prompt(cfg, title, content, source)

    if provider == "gemini":
        return _classify_with_gemini(cfg, prompt, track_id)
    else:
        return _classify_with_groq(cfg, prompt, track_id)


# ── Groq 分類 ──────────────────────────────────────────────────

def _classify_with_groq(cfg: dict, prompt: str, track_id: str) -> dict:
    """使用 Groq（Llama-3.3-70B）進行分類"""
    try:
        from groq import Groq
    except ImportError:
        logger.error(f"[{track_id}] groq 套件未安裝，執行 pip install groq")
        return _fallback_classification()

    api_key = cfg.get("groq_api_key", "").strip()
    if not api_key:
        logger.error(f"[{track_id}] Groq API Key 未設定，請至管理介面設定")
        return _fallback_classification()

    model = cfg.get("groq_model", "llama-3.3-70b-versatile")

    try:
        client = Groq(api_key=api_key)
    except Exception as e:
        logger.error(f"[{track_id}] Groq Client 建立失敗：{e}")
        return _fallback_classification()

    for attempt in range(3):
        try:
            logger.debug(f"[{track_id}] Groq 第 {attempt+1} 次呼叫（model={model}）")

            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                response_format={"type": "json_object"},  # 強制 JSON 輸出
                temperature=0.1,
                max_tokens=600,
            )

            raw_text = response.choices[0].message.content
            if not raw_text or not raw_text.strip():
                logger.warning(f"[{track_id}] Groq 回應為空（第 {attempt+1} 次）")
                time.sleep(2)
                continue

            try:
                result = json.loads(raw_text)
            except json.JSONDecodeError:
                extracted = _extract_json(raw_text)
                if extracted is None:
                    logger.warning(f"[{track_id}] Groq JSON 解析失敗：{raw_text[:150]}")
                    time.sleep(2)
                    continue
                result = json.loads(extracted)

            validated = _validate_and_fix(result, track_id)
            logger.info(
                f"[{track_id}] Groq 分類成功｜"
                f"{validated['attack_type']}｜"
                f"{validated['region']}｜"
                f"{validated['severity']}"
            )
            return validated

        except json.JSONDecodeError:
            logger.warning(f"[{track_id}] Groq JSON 解析失敗（第 {attempt+1} 次）")
            time.sleep(2)

        except Exception as e:
            err = str(e)
            if any(k in err for k in ("invalid_api_key", "Authentication", "401")):
                logger.error(f"[{track_id}] Groq API 金鑰錯誤，停止重試：{e}")
                break
            if any(k in err for k in ("rate_limit", "429", "quota", "RateLimitError")):
                wait = 20 + attempt * 15
                logger.warning(f"[{track_id}] Groq 速率限制，等待 {wait} 秒：{e}")
                time.sleep(wait)
            elif "model_not_found" in err or "404" in err:
                logger.error(f"[{track_id}] Groq 模型不存在：{model}")
                break
            else:
                logger.error(f"[{track_id}] Groq 呼叫失敗（第 {attempt+1} 次）：{type(e).__name__}: {e}")
                time.sleep(5)

    logger.error(f"[{track_id}] Groq 所有重試失敗，使用 fallback")
    return _fallback_classification()


# ── Gemini 分類 ────────────────────────────────────────────────

def _classify_with_gemini(cfg: dict, prompt: str, track_id: str) -> dict:
    """使用 Gemini（google-genai SDK v1）進行分類"""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error(f"[{track_id}] google-genai 套件未安裝，執行 pip install google-genai")
        return _fallback_classification()

    api_key = cfg.get("gemini_api_key", "").strip()
    if not api_key:
        logger.error(f"[{track_id}] Gemini API Key 未設定，請至管理介面設定")
        return _fallback_classification()

    model = cfg.get("gemini_model", "gemini-2.0-flash")

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        logger.error(f"[{track_id}] Gemini Client 建立失敗：{e}")
        return _fallback_classification()

    for attempt in range(3):
        try:
            logger.debug(f"[{track_id}] Gemini 第 {attempt+1} 次呼叫（model={model}）")

            response = client.models.generate_content(
                model    = model,
                contents = prompt,
                config   = types.GenerateContentConfig(
                    temperature        = 0.1,
                    max_output_tokens  = 600,
                    response_mime_type = "application/json",
                ),
            )

            raw_text = response.text
            if not raw_text or not raw_text.strip():
                logger.warning(f"[{track_id}] Gemini 回應為空（第 {attempt+1} 次）")
                time.sleep(2)
                continue

            try:
                result = json.loads(raw_text)
            except json.JSONDecodeError:
                extracted = _extract_json(raw_text)
                if extracted is None:
                    logger.warning(f"[{track_id}] Gemini JSON 解析失敗：{raw_text[:150]}")
                    time.sleep(2)
                    continue
                result = json.loads(extracted)

            validated = _validate_and_fix(result, track_id)
            logger.info(
                f"[{track_id}] Gemini 分類成功｜"
                f"{validated['attack_type']}｜"
                f"{validated['region']}｜"
                f"{validated['severity']}"
            )
            return validated

        except json.JSONDecodeError:
            logger.warning(f"[{track_id}] Gemini JSON 解析失敗（第 {attempt+1} 次）")
            time.sleep(2)

        except Exception as e:
            err = str(e)
            if any(k in err for k in ("API_KEY", "PERMISSION_DENIED", "invalid_api_key", "API key")):
                logger.error(f"[{track_id}] Gemini API 金鑰錯誤，停止重試：{e}")
                break
            if any(k in err for k in ("RESOURCE_EXHAUSTED", "429", "quota", "rate")):
                wait = 15 + attempt * 10
                logger.warning(f"[{track_id}] Gemini 速率限制，等待 {wait} 秒：{e}")
                time.sleep(wait)
            elif "not found" in err.lower() or "404" in err:
                logger.error(f"[{track_id}] Gemini 模型不存在：{model}")
                break
            else:
                logger.error(f"[{track_id}] Gemini 呼叫失敗（第 {attempt+1} 次）：{type(e).__name__}: {e}")
                time.sleep(5)

    logger.error(f"[{track_id}] Gemini 所有重試失敗，使用 fallback")
    return _fallback_classification()


# ── 工具函式 ──────────────────────────────────────────────────

def _build_prompt(cfg: dict, title: str, content: str, source: str) -> str:
    """建立提示詞，加上管理介面自訂前綴"""
    prompt = _PROMPT_TEMPLATE.format(
        title   = title,
        source  = source or "未知",
        content = content[:2000],
    )
    prefix = cfg.get("prompt_prefix", "").strip()
    if prefix:
        prompt = f"{prefix}\n\n{prompt}"
    return prompt


def _extract_json(text: str) -> Optional[str]:
    """從混合文字中提取 JSON（備用）"""
    text = text.strip()
    if text.startswith("{"):
        return text
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        c = m.group(1).strip()
        if c.startswith("{"):
            return c
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        return text[s : e + 1]
    return None


def _normalize(value, valid_list: list, default: str) -> str:
    """驗證並正規化 AI 回傳值（支援零寬字元和空白差異）"""
    if not isinstance(value, str):
        return default
    value = value.strip()
    if value in valid_list:
        return value
    _strip = lambda s: re.sub(r"[\s\u200b\u200c\u200d\ufeff\u3000]+", "", s)
    clean = _strip(value)
    for v in valid_list:
        if _strip(v) == clean:
            return v
    return default


def _validate_and_fix(result: dict, track_id: str = "") -> dict:
    """驗證並修正分類結果"""
    if not isinstance(result, dict):
        logger.error(f"[{track_id}] AI 回傳非 dict（{type(result).__name__}）")
        return _fallback_classification()

    attack = _normalize(result.get("attack_type",     ""), VALID_ATTACK_TYPES, "其他")
    region = _normalize(result.get("region",          ""), VALID_REGIONS,      "不明")
    system = _normalize(result.get("affected_system", ""), VALID_SYSTEMS,      "其他")
    sev    = _normalize(result.get("severity",        ""), VALID_SEVERITIES,   "中")

    summary = str(result.get("summary", "")).strip()
    if len(summary) > MAX_SUMMARY_LEN:
        cut  = summary[:MAX_SUMMARY_LEN]
        last = max(cut.rfind("。"), cut.rfind("…"), cut.rfind("！"), cut.rfind("？"))
        summary = cut[: last + 1] if last > 50 else cut + "…"

    return {
        "attack_type":     attack,
        "region":          region,
        "affected_system": system,
        "severity":        sev,
        "summary":         summary,
    }


def _fallback_classification() -> dict:
    """LLM 不可用時的預設分類"""
    return {
        "attack_type":     "其他",
        "region":          "不明",
        "affected_system": "其他",
        "severity":        "中",
        "summary":         "（AI 分類暫時不可用，請至管理介面重新分類）",
    }
