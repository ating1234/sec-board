#!/usr/bin/env python3
"""
AI 分類器診斷腳本（支援 Groq 與 Gemini）
在專案根目錄執行：python test_classifier.py
"""

import sys, os, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; N = "\033[0m"
def ok(s):   print(f"{G}✅ {s}{N}")
def fail(s): print(f"{R}❌ {s}{N}")
def warn(s): print(f"{Y}⚠️  {s}{N}")
def info(s): print(f"{B}ℹ️  {s}{N}")

print("=" * 60)
print("🛡️  AI 分類器診斷工具（Groq / Gemini）")
print("=" * 60)

# ── 1. 資料庫初始化 ────────────────────────────────────────
print("\n【步驟 1】初始化資料庫")
try:
    from backend.database import init_db
    init_db()
    ok("資料庫初始化完成")
except Exception as e:
    fail(f"資料庫初始化失敗：{e}")
    sys.exit(1)

# ── 2. 讀取設定 ────────────────────────────────────────────
print("\n【步驟 2】讀取 LLM 設定")
from backend.config import get_llm_config
cfg      = get_llm_config()
provider = cfg.get("provider", "groq")
info(f"目前供應商：{provider.upper()}")

if provider == "groq":
    api_key    = cfg.get("groq_api_key", "").strip()
    model_name = cfg.get("groq_model",   "llama-3.3-70b-versatile")
    if not api_key:
        fail("Groq API Key 未設定，請至管理介面 → LLM設定 輸入金鑰")
        print(f"\n{B}💡 申請免費 Groq API Key：https://console.groq.com{N}")
        sys.exit(1)
    ok(f"Groq API Key：{api_key[:8]}...{api_key[-4:]}")
    ok(f"模型：{model_name}")
else:
    api_key    = cfg.get("gemini_api_key", "").strip()
    model_name = cfg.get("gemini_model",   "gemini-2.0-flash")
    if not api_key:
        fail("Gemini API Key 未設定，請至管理介面 → LLM設定 輸入金鑰")
        sys.exit(1)
    ok(f"Gemini API Key：{api_key[:8]}...{api_key[-4:]}")
    ok(f"模型：{model_name}")

# ── 3. 套件檢查 ────────────────────────────────────────────
print("\n【步驟 3】檢查套件")
if provider == "groq":
    try:
        from groq import Groq
        import importlib.metadata as meta
        ver = meta.version("groq")
        ok(f"groq 版本：{ver}")
    except ImportError:
        fail("groq 套件未安裝，請執行：pip install groq")
        sys.exit(1)
else:
    try:
        from google import genai
        from google.genai import types
        import importlib.metadata as meta
        ver = meta.version("google-genai")
        ok(f"google-genai 版本：{ver}")
    except ImportError:
        fail('google-genai 未安裝，請執行：pip install "google-genai>=1.0.0"')
        sys.exit(1)

# ── 4. 建立 Client ─────────────────────────────────────────
print("\n【步驟 4】建立 Client")
client = None
if provider == "groq":
    try:
        client = Groq(api_key=api_key)
        ok("Groq Client 建立成功")
    except Exception as e:
        fail(f"Groq Client 建立失敗：{e}")
        traceback.print_exc()
        sys.exit(1)
else:
    try:
        client = genai.Client(api_key=api_key)
        ok("Gemini Client 建立成功")
    except Exception as e:
        fail(f"Gemini Client 建立失敗：{e}")
        traceback.print_exc()
        sys.exit(1)

# ── 5. 連線測試 ────────────────────────────────────────────
print("\n【步驟 5】連線測試（ping）")
if provider == "groq":
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "user", "content": "請回覆數字 1"}],
            model=model_name,
            max_tokens=10,
        )
        reply = resp.choices[0].message.content
        print(f"  回應：{repr(reply)}")
        ok("Groq 連線成功")
    except Exception as e:
        fail(f"Groq 連線失敗：{type(e).__name__}: {e}")
        traceback.print_exc()
        if "invalid_api_key" in str(e).lower() or "401" in str(e):
            print(f"\n{Y}💡 API Key 無效，請至 https://console.groq.com 確認{N}")
        elif "rate" in str(e).lower() or "429" in str(e):
            print(f"\n{Y}💡 速率限制，稍後再試（免費方案：14,400 req/day）{N}")
        sys.exit(1)
else:
    try:
        resp = client.models.generate_content(
            model    = model_name,
            contents = "請回覆數字 1",
        )
        print(f"  回應：{repr(resp.text)}")
        ok("Gemini 連線成功")
    except Exception as e:
        fail(f"Gemini 連線失敗：{type(e).__name__}: {e}")
        traceback.print_exc()
        if "404" in str(e):
            print(f"\n{Y}💡 模型不存在（{model_name}），請至管理介面更新模型名稱{N}")
        elif "429" in str(e) or "EXHAUSTED" in str(e):
            print(f"\n{Y}💡 Gemini 配額已耗盡，建議切換至 Groq（免費）{N}")
        sys.exit(1)

# ── 6. JSON 模式測試 ───────────────────────────────────────
print("\n【步驟 6】測試 JSON 輸出模式")
if provider == "groq":
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "user", "content": '請回傳 JSON：{"status": "ok", "value": 42}'}],
            model=model_name,
            response_format={"type": "json_object"},
            max_tokens=50,
        )
        parsed = json.loads(resp.choices[0].message.content)
        ok(f"JSON 模式正常，回應：{parsed}")
    except Exception as e:
        warn(f"Groq JSON 模式測試失敗：{e}")
else:
    try:
        resp = client.models.generate_content(
            model    = model_name,
            contents = '請回傳 JSON：{"status": "ok", "value": 42}',
            config   = types.GenerateContentConfig(
                temperature        = 0.1,
                response_mime_type = "application/json",
            ),
        )
        parsed = json.loads(resp.text)
        ok(f"JSON 模式正常，回應：{parsed}")
    except Exception as e:
        warn(f"Gemini JSON 模式測試失敗：{e}")

# ── 7. 完整分類測試 ────────────────────────────────────────
print("\n【步驟 7】完整分類測試")
SAMPLE_TITLE   = "台灣某大型醫院遭勒索病毒攻擊，病患資料外洩"
SAMPLE_CONTENT = "一家台灣北部的醫學中心於週二遭到勒索病毒攻擊，導致系統停擺12小時。攻擊者要求100個比特幣贖金，並聲稱竊取超過100萬筆病患個資。院方已通報衛福部與警方調查。"

try:
    from backend.classifier import classify_article
    result = classify_article(SAMPLE_TITLE, SAMPLE_CONTENT, "診斷測試")
    print(f"  標題：{SAMPLE_TITLE}")
    print(f"  分類結果：")
    fallback_count = 0
    for k, v in result.items():
        is_fb = v in ("其他", "不明", "（AI 分類暫時不可用，請至管理介面重新分類）")
        icon  = f"{Y}⚠️ " if is_fb else f"{G}  ✅"
        print(f"    {icon} {k}：{v}{N}")
        if is_fb: fallback_count += 1

    if fallback_count >= 3:
        warn("多個欄位為 fallback，分類可能仍有問題")
    else:
        ok("分類結果正常！")
except Exception as e:
    fail(f"分類失敗：{type(e).__name__}: {e}")
    traceback.print_exc()

print("\n" + "=" * 60)
print("診斷完成")
print("=" * 60)
