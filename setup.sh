#!/bin/bash
# 初始安裝腳本（只需執行一次）

set -e
echo "🛡️  資安新聞分析平台 — 初始化安裝"
echo "========================================"

# 1. 建立虛擬環境
echo ""
echo "📦 步驟 1：建立 Python 虛擬環境..."
python3 -m venv venv
source venv/bin/activate

# 2. 安裝依賴
echo ""
echo "📥 步驟 2：安裝 Python 套件..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✅ 套件安裝完成"

# 3. 設定環境變數
echo ""
echo "🔧 步驟 3：設定環境變數..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  已建立 .env 檔案，請編輯並填入 PostgreSQL 連線資訊："
  echo "   nano .env"
  echo ""
  echo "   DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/cybersec_db"
else
  echo "✅ .env 已存在"
fi

echo ""
echo "========================================"
echo "✅ 安裝完成！"
echo ""
echo "後續步驟："
echo "  1. 確認 PostgreSQL 17 已安裝並執行"
echo "  2. 建立資料庫："
echo "       createdb -U postgres cybersec_db"
echo "  3. 編輯 .env 填入正確的 PostgreSQL 密碼"
echo "  4. 啟動服務："
echo "       source venv/bin/activate"
echo "       bash start.sh"
echo ""
echo "  🌐 前台：http://localhost:8000"
echo "  ⚙️  管理：http://localhost:8000/admin"
echo "  📚 API：  http://localhost:8000/docs"
