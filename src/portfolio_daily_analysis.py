# -*- coding: utf-8 -*-
"""
持仓感知的每日股票分析系统

该模块提供了将持仓信息集成到每日股票分析推送的功能，
可以根据用户的持仓情况提供个性化的分析建议。
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.config import get_config
from src.core.pipeline import StockAnalysisPipeline
from src.services.portfolio_service import PortfolioService
from src.storage import get_db
from src.notification import NotificationService
from data_provider.base import normalize_stock_code


logger = logging.getLogger(__name__)


@dataclass
class PortfolioAwareAnalysisResult:
    """持仓感知分析结果"""
    stock_code: str
    stock_name: str
    analysis_result: Optional[Dict[str, Any]]
    position_info: Optional[Dict[str, Any]]
    portfolio_recommendation: str
    risk_factors: List[str]


class PortfolioAwareAnalysisPipeline:
    """
    持仓感知的股票分析流水线
    
    该流水线在标准分析基础上，集成持仓信息提供个性化建议
    """
    
    def __init__(self):
        self.config = get_config()
        self.portfolio_service = PortfolioService()
        self.notification_service = NotificationService(self.config)
        self.db = get_db()
    
    def analyze_with_portfolio_context(self, stock_codes: List[str], account_id: Optional[int] = None) -> List[PortfolioAwareAnalysisResult]:
        """
        基于持仓信息分析股票
        
        Args:
            stock_codes: 要分析的股票代码列表
            account_id: 持仓账户ID，如果不指定则查询所有账户
            
        Returns:
            包含持仓信息的分析结果列表
        """
        logger.info(f"开始持仓感知分析，股票列表: {stock_codes}")
        
        # 获取标准分析结果
        # 创建新的分析流水线实例
        pipeline = StockAnalysisPipeline()
        standard_results = pipeline.run(
            stock_codes=stock_codes,
            send_notification=False  # 不在此处发送通知
        )
        
        # 将 AnalysisResult 对象转换为字典格式
        analysis_result_map = {}
        for result in standard_results:
            # 获取股票代码，优先使用code属性
            stock_code = None
            if hasattr(result, 'code'):
                stock_code = result.code
            elif hasattr(result, 'stock_code'):
                stock_code = result.stock_code
            else:
                # 尝试其他可能的属性名
                for attr_name in ['symbol', 'stock_symbol', 'code_value']:
                    if hasattr(result, attr_name):
                        stock_code = getattr(result, attr_name)
                        break
            
            if stock_code:
                analysis_result_map[normalize_stock_code(str(stock_code))] = self._analysis_result_to_dict(result)
        
        # 获取持仓信息
        positions = {}
        if account_id:
            account_positions = self.portfolio_service.get_positions(account_id)
            positions = {normalize_stock_code(pos.symbol): pos for pos in account_positions}
        else:
            # 如果没有指定账户ID，尝试获取所有活动账户的持仓
            accounts = self.portfolio_service.list_accounts(include_inactive=False)
            for account in accounts:
                account_positions = self.portfolio_service.get_positions(account.id)
                for pos in account_positions:
                    normalized_symbol = normalize_stock_code(pos.symbol)
                    positions[normalized_symbol] = pos
        
        # 整合持仓信息和分析结果
        portfolio_results = []
        for code in stock_codes:
            normalized_code = normalize_stock_code(code)
            
            # 获取标准分析结果
            analysis_result = analysis_result_map.get(normalized_code)
            
            # 获取持仓信息
            position = positions.get(normalized_code)
            
            # 获取股票名称
            stock_name = code
            if analysis_result:
                stock_name = analysis_result.get('stock_name', code)
            
            # 生成持仓相关的建议
            portfolio_rec = self._generate_portfolio_recommendation(normalized_code, position, analysis_result)
            
            # 评估持仓风险
            risk_factors = self._assess_portfolio_risks(normalized_code, position, analysis_result)
            
            portfolio_results.append(
                PortfolioAwareAnalysisResult(
                    stock_code=normalized_code,
                    stock_name=stock_name,
                    analysis_result=analysis_result,
                    position_info=self._format_position_info(position) if position else {},
                    portfolio_recommendation=portfolio_rec,
                    risk_factors=risk_factors
                )
            )
        
        return portfolio_results
    
    def _analysis_result_to_dict(self, analysis_result: Any) -> Dict[str, Any]:
        """将AnalysisResult对象转换为字典"""
        result_dict = {}
        
        # 提取对象的属性
        for attr_name in dir(analysis_result):
            if not attr_name.startswith('_'):  # 跳过私有属性
                attr_value = getattr(analysis_result, attr_name)
                if not callable(attr_value):  # 跳过方法
                    try:
                        # 尝试序列化，避免复杂对象
                        if isinstance(attr_value, (str, int, float, bool, type(None))):
                            result_dict[attr_name] = attr_value
                        elif isinstance(attr_value, (list, tuple)):
                            result_dict[attr_name] = str(attr_value)  # 简单处理列表
                        elif hasattr(attr_value, '__dict__'):
                            # 对于复杂对象，尝试提取其基本属性
                            result_dict[attr_name] = str(attr_value)
                        else:
                            result_dict[attr_name] = str(attr_value)
                    except:
                        result_dict[attr_name] = str(attr_value)
        
        return result_dict
    
    def _generate_portfolio_recommendation(self, stock_code: str, position: Optional[Any], analysis_result: Optional[Dict]) -> str:
        """根据持仓情况生成个性化建议"""
        if not position or (hasattr(position, 'quantity') and getattr(position, 'quantity', 0) == 0):
            # 无持仓
            if analysis_result and analysis_result.get('operation_advice'):
                return f"【新推荐】{analysis_result.get('operation_advice', '可关注')}"
            else:
                return "【新推荐】建议关注"
        else:
            # 有持仓
            avg_cost = getattr(position, 'avg_cost', 0) if hasattr(position, 'avg_cost') else 0
            quantity = getattr(position, 'quantity', 0) if hasattr(position, 'quantity') else 0
            
            # 从持仓中获取当前市场价格
            current_price = 0
            if hasattr(position, 'current_market_value') and quantity > 0:
                current_price = getattr(position, 'current_market_value', 0) / quantity
            elif hasattr(position, 'current_price'):
                current_price = getattr(position, 'current_price', 0)
            
            if current_price > 0 and avg_cost > 0:
                profit_loss_rate = (current_price - avg_cost) / avg_cost * 100
                if profit_loss_rate > 5:  # 盈利超过5%
                    recommendation = f"【持仓建议】当前盈利{profit_loss_rate:.2f}%，"
                    if analysis_result and analysis_result.get('target_price'):
                        target_price = analysis_result.get('target_price', current_price * 1.1)
                        if current_price >= target_price * 0.9:  # 接近目标价
                            recommendation += "建议适当止盈"
                        else:
                            recommendation += "可继续持有观察"
                    else:
                        recommendation += "可继续持有或部分止盈"
                elif profit_loss_rate < -5:  # 亏损超过5%
                    recommendation = f"【持仓建议】当前亏损{abs(profit_loss_rate):.2f}%，"
                    if analysis_result and analysis_result.get('operation_advice', '').startswith('买入'):
                        recommendation += "可考虑补仓摊薄成本"
                    else:
                        recommendation += "谨慎持有或考虑止损"
                else:
                    recommendation = f"【持仓建议】当前盈亏{profit_loss_rate:.2f}%，"
                    if analysis_result:
                        recommendation += analysis_result.get('operation_advice', '持有观察')
                    else:
                        recommendation += "持有观察"
            else:
                recommendation = "【持仓建议】持有中，"
                if analysis_result:
                    recommendation += analysis_result.get('operation_advice', '持有观察')
                else:
                    recommendation += "持有观察"
            
            return recommendation
    
    def _assess_portfolio_risks(self, stock_code: str, position: Optional[Any], analysis_result: Optional[Dict]) -> List[str]:
        """评估持仓风险"""
        risks = []
        
        if position and (not hasattr(position, 'quantity') or getattr(position, 'quantity', 0) > 0):
            # 检查持仓集中度风险
            quantity = getattr(position, 'quantity', 0) if hasattr(position, 'quantity') else 0
            avg_cost = getattr(position, 'avg_cost', 0) if hasattr(position, 'avg_cost') else 0
            current_market_value = getattr(position, 'current_market_value', 0) if hasattr(position, 'current_market_value') else 0
            
            if current_market_value > 0:
                risks.append(f"单股持仓金额: ¥{current_market_value:,.2f}")
        
        # 添加来自分析结果的风险提示
        if analysis_result:
            # 尝试不同的风险字段名
            for risk_field in ['risk_warnings', 'risk_warning', 'risk_flag', 'risk_level', 'risk_assessment']:
                risk_warnings = analysis_result.get(risk_field)
                if risk_warnings:
                    if isinstance(risk_warnings, list):
                        risks.extend([f"分析风险: {tag}" for tag in risk_warnings])
                    else:
                        risks.append(f"分析风险: {risk_warnings}")
        
        return risks
    
    def _format_position_info(self, position: Any) -> Dict[str, Any]:
        """格式化持仓信息"""
        if not position:
            return {}
        
        info = {
            'quantity': getattr(position, 'quantity', 0),
            'avg_cost': getattr(position, 'avg_cost', 0),
            'current_market_value': getattr(position, 'current_market_value', 0),
        }
        
        # 计算当前价格
        if info['quantity'] > 0:
            info['current_price'] = info['current_market_value'] / info['quantity']
        else:
            info['current_price'] = getattr(position, 'current_price', 0)
        
        # 计算盈亏
        if info['avg_cost'] > 0 and info['current_price'] > 0:
            info['profit_loss_rate'] = (info['current_price'] - info['avg_cost']) / info['avg_cost'] * 100
            info['profit_loss_amount'] = (info['current_price'] - info['avg_cost']) * info['quantity']
        else:
            info['profit_loss_rate'] = 0
            info['profit_loss_amount'] = 0
        
        return info
    
    def generate_portfolio_report(self, portfolio_results: List[PortfolioAwareAnalysisResult]) -> str:
        """生成持仓感知的分析报告"""
        report_lines = ["# 持仓感知股票分析报告", ""]
        report_lines.append(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("")
        
        # 持仓股票分析
        holding_stocks = [r for r in portfolio_results if r.position_info and r.position_info.get('quantity', 0) > 0]
        new_recommendations = [r for r in portfolio_results if not r.position_info or r.position_info.get('quantity', 0) == 0]
        
        if holding_stocks:
            report_lines.append("## 持仓股票分析")
            report_lines.append("")
            
            for result in holding_stocks:
                report_lines.append(f"### {result.stock_code} ({result.stock_name}) - 持仓中")
                if result.position_info:
                    pos = result.position_info
                    report_lines.append(f"- 持仓数量: {pos['quantity']}")
                    report_lines.append(f"- 平均成本: ¥{pos['avg_cost']:.2f}")
                    report_lines.append(f"- 当前价格: ¥{pos['current_price']:.2f}")
                    report_lines.append(f"- 市值: ¥{pos['current_market_value']:.2f}")
                    report_lines.append(f"- 盈亏比例: {pos['profit_loss_rate']:.2f}%")
                    report_lines.append(f"- 盈亏金额: ¥{pos['profit_loss_amount']:.2f}")
                
                report_lines.append(f"- **持仓建议**: {result.portfolio_recommendation}")
                
                if result.analysis_result:
                    operation_advice = result.analysis_result.get('operation_advice', '无')
                    report_lines.append(f"- **技术分析**: {operation_advice}") 
                
                if result.risk_factors:
                    report_lines.append("- **风险提示**:")
                    for risk in result.risk_factors:
                        report_lines.append(f"  - {risk}")
                
                report_lines.append("")
        
        # 新股推荐
        if new_recommendations:
            report_lines.append("## 新股推荐")
            report_lines.append("")
            
            for result in new_recommendations:
                report_lines.append(f"### {result.stock_code} ({result.stock_name}) - 无持仓")
                report_lines.append(f"- **推荐建议**: {result.portfolio_recommendation}")
                
                if result.analysis_result:
                    operation_advice = result.analysis_result.get('operation_advice', '无')
                    report_lines.append(f"- **技术分析**: {operation_advice}")
                
                if result.risk_factors:
                    report_lines.append("- **风险提示**:")
                    for risk in result.risk_factors:
                        report_lines.append(f"  - {risk}")
                
                report_lines.append("")
        
        # 整体持仓建议
        if holding_stocks or new_recommendations:
            report_lines.extend(self._generate_portfolio_overview(holding_stocks, new_recommendations))
        
        return "\n".join(report_lines)
    
    def _generate_portfolio_overview(self, holding_stocks: List[PortfolioAwareAnalysisResult], 
                                   new_recommendations: List[PortfolioAwareAnalysisResult]) -> List[str]:
        """生成整体持仓概览"""
        overview_lines = ["## 组合配置建议", ""]
        
        if holding_stocks:
            total_value = sum((r.position_info['current_market_value'] for r in holding_stocks if r.position_info and r.position_info.get('current_market_value')), 0)
            overview_lines.append(f"**当前持仓总市值: ¥{total_value:,.2f}**")
        
        if holding_stocks and new_recommendations:
            overview_lines.append("**持仓优化建议:**")
            # 简单的配置建议
            holding_symbols = [r.stock_code for r in holding_stocks]
            new_symbols = [r.stock_code for r in new_recommendations]
            
            overview_lines.append(f"- 当前持仓: {', '.join(holding_symbols)}")
            overview_lines.append(f"- 建议新增: {', '.join(new_symbols) if new_symbols else '无'}")
        
        return overview_lines
    
    def run_portfolio_daily_analysis(self, account_id: Optional[int] = None) -> None:
        """运行持仓感知的每日分析"""
        logger.info("开始执行持仓感知的每日分析")
        
        # 获取要分析的股票列表（从配置中获取）
        stock_list = self.config.stock_list
        if not stock_list:
            logger.warning("未配置股票列表，跳过分析")
            return
        
        # 执行分析
        results = self.analyze_with_portfolio_context(stock_list, account_id)
        
        # 生成报告
        report = self.generate_portfolio_report(results)
        
        # 发送通知
        self.notification_service.send_notification(report)
        
        logger.info("持仓感知每日分析完成")


def run_daily_portfolio_analysis(account_id: Optional[int] = None):
    """运行持仓感知的每日分析的便捷函数"""
    pipeline = PortfolioAwareAnalysisPipeline()
    pipeline.run_portfolio_daily_analysis(account_id)