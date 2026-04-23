#!/bin/bash
# 每次啟動資安新聞分析平台用此腳本

set -e
GREEN='\033[0;32m'; NC='\033[0m'

# PostgreSQL 17 bin 路徑（Apple Silicon / Intel 相容）
if [ -d "/opt/homebrew/opt/postgresql@17/bin" ]; then
  export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"   # Apple Silicon
elif [ -d "/usr/local/opt/postgresql@17/bin" ]; then
  export PATH="/usr/local/opt/postgresql@17/bin:$PATH"       # Intel Mac
fi

# 確認 PostgreSQL 服務在執行
if ! pg_isready -q 2>/dev/null; then
  echo "⚠️  PostgreSQL 未啟動，嘗試啟動..."
  brew services start postgresql@17
  sleep 2
fi

# 載入環境變數
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# 啟動虛擬環境
source venv/bin/activate

echo -e "${GREEN}🛡️  資安新聞分析平台 啟動中...${NC}"
echo "🌐 前台：http://localhost:8000"
echo "⚙️  管理：http://localhost:8000/admin"
echo "（按 Ctrl+C 停止）"
echo ""

uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
