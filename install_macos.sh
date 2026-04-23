#!/bin/bash
# ============================================================
# 資安新聞分析平台 — macOS 完整安裝腳本
# 適用：macOS 12+（Monterey / Ventura / Sonoma / Sequoia）
# 執行方式：bash install_macos.sh
# ============================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
err()  { echo -e "${RED}❌ $1${NC}"; exit 1; }
info() { echo -e "${BLUE}ℹ️  $1${NC}"; }

echo ""
echo "🛡️  資安新聞分析平台 — macOS 安裝程式"
echo "========================================"
echo ""

# ── 步驟 1：Homebrew ──────────────────────────────────────
echo "📦 步驟 1：檢查 Homebrew..."
if ! command -v brew &>/dev/null; then
  warn "Homebrew 未安裝，開始安裝..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Apple Silicon 需要加入 PATH
  if [[ $(uname -m) == "arm64" ]]; then
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
  ok "Homebrew 安裝完成"
else
  ok "Homebrew 已安裝：$(brew --version | head -1)"
fi

# ── 步驟 2：Python 3.11 ───────────────────────────────────
echo ""
echo "🐍 步驟 2：確認 Python 3.11+..."
PYTHON=""
for cmd in python3.12 python3.11 python3; do
  if command -v $cmd &>/dev/null; then
    VER=$($cmd -c "import sys; print(sys.version_info.minor)")
    if [ "$VER" -ge 10 ]; then
      PYTHON=$cmd
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  warn "安裝 Python 3.11..."
  brew install python@3.11
  PYTHON=python3.11
fi
ok "Python：$($PYTHON --version)"

# ── 步驟 3：PostgreSQL 17 ─────────────────────────────────
echo ""
echo "🐘 步驟 3：安裝 PostgreSQL 17..."
if brew list postgresql@17 &>/dev/null; then
  ok "PostgreSQL 17 已安裝"
else
  info "安裝中（約需 1-3 分鐘）..."
  brew install postgresql@17
  ok "PostgreSQL 17 安裝完成"
fi

# 確保 psql 在 PATH 中
PG_BIN="$(brew --prefix postgresql@17)/bin"
export PATH="$PG_BIN:$PATH"

# 將 PATH 寫入 shell 設定（永久生效）
SHELL_RC="$HOME/.zprofile"
if [[ "$SHELL" == *"bash"* ]]; then
  SHELL_RC="$HOME/.bash_profile"
fi
if ! grep -q "postgresql@17" "$SHELL_RC" 2>/dev/null; then
  echo "" >> "$SHELL_RC"
  echo "# PostgreSQL 17" >> "$SHELL_RC"
  echo "export PATH=\"$PG_BIN:\$PATH\"" >> "$SHELL_RC"
  info "已將 PostgreSQL 加入 $SHELL_RC"
fi

# ── 步驟 4：啟動 PostgreSQL ───────────────────────────────
echo ""
echo "🚀 步驟 4：啟動 PostgreSQL 服務..."
brew services start postgresql@17 2>/dev/null || true
sleep 2

# 等待服務就緒（最多 15 秒）
for i in {1..15}; do
  if pg_isready -q 2>/dev/null; then
    ok "PostgreSQL 服務正在執行"
    break
  fi
  sleep 1
  if [ $i -eq 15 ]; then
    err "PostgreSQL 啟動超時，請執行：brew services restart postgresql@17"
  fi
done

# ── 步驟 5：建立資料庫 ────────────────────────────────────
echo ""
echo "🗄️  步驟 5：建立資料庫 cybersec_db..."
DB_USER=$(whoami)   # macOS 預設使用目前使用者名稱
if psql -U "$DB_USER" -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw cybersec_db; then
  ok "資料庫 cybersec_db 已存在"
else
  createdb -U "$DB_USER" cybersec_db 2>/dev/null || {
    # 嘗試用 postgres 角色
    psql postgres -c "CREATE DATABASE cybersec_db;" 2>/dev/null || \
    warn "無法自動建立資料庫，請手動執行：createdb cybersec_db"
  }
  ok "資料庫 cybersec_db 建立完成"
fi

# ── 步驟 6：取得連線字串 ──────────────────────────────────
echo ""
echo "🔑 步驟 6：設定資料庫連線..."
DB_USER=$(whoami)
# macOS Homebrew PostgreSQL 不需要密碼（peer 驗證）
DB_URL="postgresql://${DB_USER}@localhost:5432/cybersec_db"

# 寫入 .env
cat > .env << EOF
# 資安新聞分析平台 — 環境設定
DATABASE_URL=${DB_URL}
EOF
ok ".env 已建立（DATABASE_URL=${DB_URL}）"

# ── 步驟 7：Python 虛擬環境 ───────────────────────────────
echo ""
echo "📦 步驟 7：建立 Python 虛擬環境..."
if [ -d "venv" ]; then
  ok "虛擬環境已存在"
else
  $PYTHON -m venv venv
  ok "虛擬環境建立完成"
fi
source venv/bin/activate

# ── 步驟 8：安裝 Python 套件 ──────────────────────────────
echo ""
echo "📥 步驟 8：安裝 Python 套件（約需 1-3 分鐘）..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "所有套件安裝完成"

# ── 驗證 ──────────────────────────────────────────────────
echo ""
echo "🔍 驗證安裝..."
python -c "import fastapi, sqlalchemy, feedparser, apscheduler; print('核心套件 ✅')"
python -c "import google.generativeai; print('Gemini SDK ✅')"
python -c "import psycopg2; conn = psycopg2.connect('${DB_URL}'); conn.close(); print('資料庫連線 ✅')"

echo ""
echo "========================================"
echo -e "${GREEN}🎉 安裝完成！${NC}"
echo ""
echo "啟動服務："
echo "  source venv/bin/activate"
echo "  uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload"
echo ""
echo "或直接執行："
echo "  bash start_macos.sh"
echo ""
echo "🌐 前台 Dashboard : http://localhost:8000"
echo "⚙️  管理介面       : http://localhost:8000/admin"
echo "📚 API 文件        : http://localhost:8000/docs"
echo "========================================"
