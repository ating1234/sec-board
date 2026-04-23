#!/bin/bash
# 啟動資安新聞分析平台

set -e

# 載入環境變數
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# 檢查 DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
  echo "❌ 錯誤：請設定 DATABASE_URL 環境變數"
  echo "   複製 .env.example 為 .env 並填入設定"
  exit 1
fi

echo "🛡️  資安新聞分析平台 啟動中..."
echo "📦 DATABASE_URL: $DATABASE_URL"
echo ""

# 啟動 FastAPI
uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload

echo ""
echo "🌐 前台 Dashboard : http://localhost:8000"
echo "⚙️  管理介面       : http://localhost:8000/admin"
echo "📚 API 文件        : http://localhost:8000/docs"
