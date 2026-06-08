#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
持仓感知的每日股票分析运行脚本

该脚本将持仓信息与每日股票分析相结合，
提供个性化的分析推送服务。
"""

import sys
import os
import argparse
from datetime import datetime

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import setup_env
from src.portfolio_daily_analysis import run_daily_portfolio_analysis

def main():
    parser = argparse.ArgumentParser(description='持仓感知的每日股票分析')
    parser.add_argument('--account-id', type=int, help='指定持仓账户ID进行分析')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')
    
    args = parser.parse_args()
    
    if args.debug:
        import logging
        logging.basicConfig(level=logging.DEBUG)
    
    print(f"开始执行持仓感知的每日分析 - {datetime.now()}")
    print("=" * 50)
    
    try:
        # 初始化环境变量
        setup_env()
        
        # 运行持仓感知的每日分析
        run_daily_portfolio_analysis(account_id=args.account_id)
        print("持仓感知的每日分析执行完成！")
    except Exception as e:
        print(f"执行过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()