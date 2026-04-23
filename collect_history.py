#!/usr/bin/env python3
"""
歷史數據收集 — 獨立執行腳本
用法：
  python collect_history.py           # 收集近 30 天
  python collect_history.py --days 60 # 收集近 60 天
"""

import sys
import os
import argparse

# 把專案根目錄加入 Python 路徑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 載入 .env
from dotenv import load_dotenv
load_dotenv()

# 初始化資料庫（建立資料表 + 預設資料）
from backend.database import init_db
print("🔧 初始化資料庫...")
init_db()
print("✅ 資料庫就緒\n")

# 執行歷史收集
from backend.historical_collector import run_historical_collection

def main():
    parser = argparse.ArgumentParser(description="資安新聞歷史數據收集")
    parser.add_argument(
        "--days", type=int, default=30,
        help="收集幾天的歷史數據（預設 30）"
    )
    args = parser.parse_args()

    run_historical_collection(days=args.days, verbose=True)


if __name__ == "__main__":
    main()
