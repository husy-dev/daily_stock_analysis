# -*- coding: utf-8 -*-
"""Portfolio CSV import service with extensible parser registry."""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from data_provider.base import canonical_stock_code
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import (
    PortfolioBusyError,
    PortfolioConflictError,
    PortfolioOversellError,
    PortfolioService,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class CsvParserSpec:
    """CSV parser specification for one broker."""

    broker: str
    aliases: Tuple[str, ...]
    display_name: str
    column_hints: Dict[str, Tuple[str, ...]]

@dataclass(frozen=True)
class PositionParserSpec:
    """Position parser specification for one broker."""

    broker: str
    aliases: Tuple[str, ...]
    display_name: str
    column_hints: Dict[str, Tuple[str, ...]]


DEFAULT_PARSER_SPECS: Tuple[CsvParserSpec, ...] = (
    CsvParserSpec(
        broker="huatai",
        aliases=(),
        display_name="华泰",
        column_hints={
            "trade_date": ("成交日期", "成交时间", "发生日期", "日期"),
            "symbol": ("证券代码", "股票代码", "代码"),
            "side": ("买卖标志", "买卖方向", "操作"),
            "quantity": ("成交数量", "数量", "成交股数"),
            "price": ("成交均价", "成交价格", "价格", "成交价", "均价"),
            "trade_uid": ("成交编号", "成交序号", "流水号"),
        },
    ),
    CsvParserSpec(
        broker="citic",
        aliases=("zhongxin",),
        display_name="中信",
        column_hints={
            "trade_date": ("发生日期", "成交日期", "日期"),
            "symbol": ("证券代码", "股票代码", "代码"),
            "side": ("买卖方向", "买卖标志", "业务名称"),
            "quantity": ("成交数量", "数量", "成交股数"),
            "price": ("成交价格", "成交均价", "价格", "成交价"),
            "trade_uid": ("合同编号", "成交编号", "委托编号"),
        },
    ),
    CsvParserSpec(
        broker="cmb",
        aliases=("zhaoshang", "cmbchina"),
        display_name="招商",
        column_hints={
            "trade_date": ("日期", "成交日期", "发生日期"),
            "symbol": ("证券代码", "股票代码", "代码"),
            "side": ("交易方向", "买卖方向", "买卖标志"),
            "quantity": ("成交股数", "成交数量", "数量"),
            "price": ("成交价", "成交价格", "成交均价", "均价"),
            "trade_uid": ("流水号", "成交编号", "成交序号"),
        },
    ),
)

# 持仓快照解析规格
DEFAULT_POSITION_PARSER_SPECS: Tuple[PositionParserSpec, ...] = (
    PositionParserSpec(
        broker="cmb_snapshot",  # 招商证券持仓快照
        aliases=("zhaoshang_snapshot", "cmbchina_snapshot"),
        display_name="招商证券持仓快照",
        column_hints={
            "symbol": ("证券代码", "股票代码", "代码"),
            "quantity": ("证券数量", "持股数量", "持有数量", "持仓数量"),
            "current_price": ("当前价", "现价", "最新价", "市场价格"),
            "market_value": ("最新市值", "持仓市值", "市值"),
            "cost_price": ("成本价", "买入成本", "平均成本"),
            "cost_amount": ("成本金额", "总成本"),
            "profit_today": ("今日盈亏", "当日盈亏"),
            "profit_today_pct": ("今日盈亏比例(%)", "当日盈亏比例(%)"),
            "profit_total": ("持仓盈亏", "累计盈亏"),
            "profit_total_pct": ("持仓盈亏比例(%)", "累计盈亏比例(%)"),
            "available_quantity": ("可卖数量", "可用数量", "可售数量"),
        },
    ),
)


class PortfolioImportService:
    """Parse broker CSV and commit normalized trade records with dedup."""
    _shared_parser_registry: Dict[str, CsvParserSpec] = {}
    _shared_broker_alias_map: Dict[str, str] = {}
    _shared_position_parser_registry: Dict[str, PositionParserSpec] = {}
    _shared_position_broker_alias_map: Dict[str, str] = {}
    _shared_registry_initialized: bool = False

    def __init__(
        self,
        *,
        portfolio_service: Optional[PortfolioService] = None,
        repo: Optional[PortfolioRepository] = None,
    ):
        self.portfolio_service = portfolio_service or PortfolioService()
        self.repo = repo or PortfolioRepository()
        self._parser_registry = self.__class__._shared_parser_registry
        self._broker_alias_map = self.__class__._shared_broker_alias_map
        self._position_parser_registry = self.__class__._shared_position_parser_registry
        self._position_broker_alias_map = self.__class__._shared_position_broker_alias_map
        if not self.__class__._shared_registry_initialized:
            self._init_default_parsers()
            self._init_default_position_parsers()
            self.__class__._shared_registry_initialized = True

    def _init_default_parsers(self) -> None:
        for spec in DEFAULT_PARSER_SPECS:
            self.register_parser(spec)

    def _init_default_position_parsers(self) -> None:
        for spec in DEFAULT_POSITION_PARSER_SPECS:
            self.register_position_parser(spec)

    def register_parser(self, spec: CsvParserSpec) -> None:
        """Register or replace one broker parser spec."""
        broker = (spec.broker or "").strip().lower()
        if not broker:
            raise ValueError("broker is required")
        new_aliases = tuple(sorted({alias.strip().lower() for alias in spec.aliases if alias}))
        for alias in new_aliases:
            if alias == broker:
                raise ValueError(f"alias '{alias}' cannot be the same as broker id")
            existing_target = self._broker_alias_map.get(alias)
            if existing_target and existing_target != broker:
                raise ValueError(
                    f"alias '{alias}' already registered by broker '{existing_target}'"
                )
        for alias, target in list(self._broker_alias_map.items()):
            if target == broker and alias not in new_aliases:
                self._broker_alias_map.pop(alias, None)
        self._parser_registry[broker] = CsvParserSpec(
            broker=broker,
            aliases=new_aliases,
            display_name=spec.display_name or broker,
            column_hints=dict(spec.column_hints or {}),
        )
        for alias in self._parser_registry[broker].aliases:
            self._broker_alias_map[alias] = broker

    def register_position_parser(self, spec: PositionParserSpec) -> None:
        """Register or replace one broker position parser spec."""
        broker = (spec.broker or "").strip().lower()
        if not broker:
            raise ValueError("broker is required")
        new_aliases = tuple(sorted({alias.strip().lower() for alias in spec.aliases if alias}))
        for alias in new_aliases:
            if alias == broker:
                raise ValueError(f"alias '{alias}' cannot be the same as broker id")
            existing_target = self._position_broker_alias_map.get(alias)
            if existing_target and existing_target != broker:
                raise ValueError(
                    f"alias '{alias}' already registered by broker '{existing_target}'"
                )
        for alias, target in list(self._position_broker_alias_map.items()):
            if target == broker and alias not in new_aliases:
                self._position_broker_alias_map.pop(alias, None)
        self._position_parser_registry[broker] = PositionParserSpec(
            broker=broker,
            aliases=new_aliases,
            display_name=spec.display_name or broker,
            column_hints=dict(spec.column_hints or {}),
        )
        for alias in self._position_parser_registry[broker].aliases:
            self._position_broker_alias_map[alias] = broker

    def list_supported_brokers(self) -> List[Dict[str, Any]]:
        """List canonical broker ids and aliases for frontend selector."""
        items: List[Dict[str, Any]] = []
        for broker in sorted(self._parser_registry.keys()):
            aliases = sorted(alias for alias, target in self._broker_alias_map.items() if target == broker)
            items.append(
                {
                    "broker": broker,
                    "aliases": aliases,
                    "display_name": self._parser_registry[broker].display_name,
                }
            )
        return items

    def list_supported_position_brokers(self) -> List[Dict[str, Any]]:
        """List canonical position broker ids and aliases for frontend selector."""
        items: List[Dict[str, Any]] = []
        for broker in sorted(self._position_parser_registry.keys()):
            aliases = sorted(alias for alias, target in self._position_broker_alias_map.items() if target == broker)
            items.append(
                {
                    "broker": broker,
                    "aliases": aliases,
                    "display_name": self._position_parser_registry[broker].display_name,
                }
            )
        return items

    def parse_trade_csv(
        self,
        *,
        broker: str,
        content: bytes,
    ) -> Dict[str, Any]:
        broker_norm = self._normalize_broker(broker)
        parser_spec = self._parser_registry[broker_norm]
        df = self._read_csv(content)

        records: List[Dict[str, Any]] = []
        skipped = 0
        errors: List[str] = []

        for idx, row in df.iterrows():
            normalized = self._normalize_trade_row(row=row, parser_spec=parser_spec)
            if normalized is None:
                skipped += 1
                continue
            try:
                # Keep a stable line-level marker so repeated imports of the same
                # file remain idempotent, while identical split fills on separate
                # CSV lines do not collapse into one dedup key.
                normalized["_source_line_number"] = int(idx) + 2
                normalized["dedup_hash"] = self._build_dedup_hash(normalized)
                records.append(normalized)
            except Exception as exc:  # pragma: no cover - defensive path
                skipped += 1
                errors.append(f"row={idx + 1}: {exc}")

        return {
            "broker": broker_norm,
            "record_count": len(records),
            "skipped_count": skipped,
            "error_count": len(errors),
            "records": records,
            "errors": errors[:20],
        }

    def parse_position_csv(
        self,
        *,
        broker: str,
        content: bytes,
    ) -> Dict[str, Any]:
        """Parse position snapshot CSV/Excel file."""
        broker_norm = self._normalize_position_broker(broker)
        parser_spec = self._position_parser_registry[broker_norm]
        df = self._read_csv(content)

        records: List[Dict[str, Any]] = []
        skipped = 0
        errors: List[str] = []

        for idx, row in df.iterrows():
            normalized = self._normalize_position_row(row=row, parser_spec=parser_spec)
            if normalized is None:
                skipped += 1
                continue
            try:
                records.append(normalized)
            except Exception as exc:  # pragma: no cover - defensive path
                skipped += 1
                errors.append(f"row={idx + 1}: {exc}")

        return {
            "broker": broker_norm,
            "record_count": len(records),
            "skipped_count": skipped,
            "error_count": len(errors),
            "records": records,
            "errors": errors[:20],
        }

    @staticmethod
    def _read_csv(content: bytes) -> pd.DataFrame:
        # Check if file is Excel format by extension or magic bytes
        # Excel files typically start with PK (zip format)
        looks_like_excel = len(content) >= 4 and content[:4] == b"PK\x03\x04"
        
        if looks_like_excel:
            try:
                # Try to read as Excel file
                df = pd.read_excel(io.BytesIO(content), sheet_name=0, engine="openpyxl", dtype=str, keep_default_na=False)
                return df
            except Exception as e:
                logger.warning(f"Excel format detection failed, falling back to CSV parsing: {e}")
                # If Excel parsing fails, fall back to CSV
        
        # CSV / text processing
        for encoding in ("utf-8-sig", "gbk", "gb18030"):
            try:
                return pd.read_csv(
                    io.BytesIO(content),
                    encoding=encoding,
                    dtype=str,
                    keep_default_na=False,
                )
            except UnicodeDecodeError:
                continue
        return pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)

    def _normalize_trade_row(
        self,
        *,
        row: Any,
        parser_spec: CsvParserSpec,
    ) -> Optional[Dict[str, Any]]:
        broker_hints = parser_spec.column_hints

        trade_date_raw = self._pick(
            row,
            *(broker_hints.get("trade_date") or ()),
            "成交日期",
            "发生日期",
            "日期",
            "成交时间",
        )
        trade_date_obj = self._parse_date(trade_date_raw)
        if trade_date_obj is None:
            return None

        symbol_raw = self._pick(
            row,
            *(broker_hints.get("symbol") or ()),
            "证券代码",
            "股票代码",
            "代码",
        )
        symbol = canonical_stock_code(str(symbol_raw or "").strip())
        if not symbol:
            return None

        side_raw = self._pick(
            row,
            *(broker_hints.get("side") or ()),
            "买卖标志",
            "买卖方向",
            "交易方向",
            "业务名称",
            "操作",
        )
        side = self._normalize_side(side_raw)
        if side is None:
            return None

        quantity = self._parse_float(
            self._pick(row, *(broker_hints.get("quantity") or ()), "可卖数量","成交数量", "数量", "成交股数")
        )
        price = self._parse_float(
            self._pick(row, *(broker_hints.get("price") or ()), "成本价","成交均价", "成交价格", "价格", "成交价", "均价")
        )
        if quantity is None or quantity <= 0 or price is None or price <= 0:
            return None

        fee = 0.0
        for col in ("手续费", "佣金", "交易费", "规费", "过户费"):
            value = self._parse_float(self._pick(row, col))
            if value is not None:
                fee += value

        trade_uid = self._pick(row, *(broker_hints.get("trade_uid") or ()), "成交编号", "成交序号", "流水号", "合同编号")

        return {
            "trade_date": trade_date_obj,
            "symbol": symbol,
            "side": side,
            "quantity": float(quantity),
            "price": float(price),
            "fee": fee,
            "tax": 0.0,
            "trade_uid": trade_uid,
        }

    def _normalize_position_row(
        self,
        *,
        row: Any,
        parser_spec: PositionParserSpec,
    ) -> Optional[Dict[str, Any]]:
        """Normalize position snapshot row."""
        broker_hints = parser_spec.column_hints

        symbol_raw = self._pick(
            row,
            *(broker_hints.get("symbol") or ()),
            "证券代码",
            "股票代码",
            "代码",
        )
        symbol = canonical_stock_code(str(symbol_raw or "").strip())
        if not symbol:
            return None

        quantity = self._parse_float(
            self._pick(row, *(broker_hints.get("quantity") or ()), "证券数量", "持股数量", "持有数量", "持仓数量")
        )
        if quantity is None or quantity <= 0:
            return None

        current_price = self._parse_float(
            self._pick(row, *(broker_hints.get("current_price") or ()), "当前价", "现价", "最新价", "市场价格")
        )
        
        market_value = self._parse_float(
            self._pick(row, *(broker_hints.get("market_value") or ()), "最新市值", "持仓市值", "市值")
        )
        
        cost_price = self._parse_float(
            self._pick(row, *(broker_hints.get("cost_price") or ()), "成本价", "买入成本", "平均成本")
        )
        
        cost_amount = self._parse_float(
            self._pick(row, *(broker_hints.get("cost_amount") or ()), "成本金额", "总成本")
        )
        
        available_quantity = self._parse_float(
            self._pick(row, *(broker_hints.get("available_quantity") or ()), "可卖数量", "可用数量", "可售数量")
        )

        return {
            "symbol": symbol,
            "quantity": float(quantity),
            "current_price": float(current_price) if current_price is not None else 0.0,
            "market_value": float(market_value) if market_value is not None else 0.0,
            "cost_price": float(cost_price) if cost_price is not None else 0.0,
            "cost_amount": float(cost_amount) if cost_amount is not None else 0.0,
            "available_quantity": float(available_quantity) if available_quantity is not None else float(quantity),
        }

    def commit_trade_records(
        self,
        *,
        account_id: int,
        broker: str,
        records: List[Dict[str, Any]],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        broker_norm = self._normalize_broker(broker)

        inserted_count = 0
        duplicate_count = 0
        failed_count = 0
        errors: List[str] = []
        seen_trade_uids: set[str] = set()
        seen_dedup_hashes: set[str] = set()

        for i, record in enumerate(records):
            try:
                trade_uid = (record.get("trade_uid") or "").strip() or None
                dedup_hash = record.get("dedup_hash")

                if dedup_hash and dedup_hash in seen_dedup_hashes:
                    duplicate_count += 1
                    continue
                if trade_uid and trade_uid in seen_trade_uids:
                    duplicate_count += 1
                    continue

                if not dry_run:
                    try:
                        result = self.portfolio_service.record_trade(
                            account_id=account_id,
                            symbol=record["symbol"],
                            trade_date=record["trade_date"],
                            side=record["side"],
                            quantity=record["quantity"],
                            price=record["price"],
                            fee=record.get("fee", 0.0),
                            tax=record.get("tax", 0.0),
                            trade_uid=trade_uid,
                            market="cn",  # Default market, could be inferred from symbol
                            currency="CNY",  # Default currency, could be inferred from data
                        )
                        if dedup_hash:
                            seen_dedup_hashes.add(dedup_hash)
                        if trade_uid:
                            seen_trade_uids.add(trade_uid)
                    except (PortfolioConflictError, DuplicateTradeUidError, DuplicateTradeDedupHashError):
                        duplicate_count += 1
                        continue

                inserted_count += 1
            except Exception as exc:
                failed_count += 1
                errors.append(f"record[{i}]={record.get('symbol', '?')}: {exc}")

        return {
            "inserted_count": inserted_count,
            "duplicate_count": duplicate_count,
            "failed_count": failed_count,
            "errors": errors[:20],
        }

    def _normalize_broker(self, broker: str) -> str:
        broker_norm = (broker or "").strip().lower()
        if not broker_norm:
            supported = ", ".join(sorted(self._parser_registry.keys()))
            raise ValueError(f"broker must be one of: {supported}")
        target = self._broker_alias_map.get(broker_norm)
        if target:
            return target
        if broker_norm not in self._parser_registry:
            supported = ", ".join(sorted(self._parser_registry.keys()))
            raise ValueError(f"broker must be one of: {supported}")
        return broker_norm

    def _normalize_position_broker(self, broker: str) -> str:
        broker_norm = (broker or "").strip().lower()
        if not broker_norm:
            supported = ", ".join(sorted(self._position_parser_registry.keys()))
            raise ValueError(f"broker must be one of: {supported}")
        target = self._position_broker_alias_map.get(broker_norm)
        if target:
            return target
        if broker_norm not in self._position_parser_registry:
            supported = ", ".join(sorted(self._position_parser_registry.keys()))
            raise ValueError(f"broker must be one of: {supported}")
        return broker_norm

    @staticmethod
    def _pick(row: Any, *names: str) -> Optional[str]:
        """Pick first non-empty value from row by name."""
        for name in names:
            try:
                val = row.get(name)
                if val is not None and str(val).strip():
                    return str(val).strip()
            except AttributeError:
                try:
                    val = row[name]
                    if val is not None and str(val).strip():
                        return str(val).strip()
                except (TypeError, KeyError):
                    continue
        return None

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        """Parse date string in various formats."""
        if not date_str:
            return None
        date_str = str(date_str).strip()
        if not date_str:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"):
            try:
                return date.fromisoformat(date_str.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", ""))
            except ValueError:
                continue
        return None

    def _normalize_side(self, raw: Optional[str]) -> Optional[str]:
        """Normalize buy/sell side indicator."""
        if not raw:
            return None
        raw = str(raw).strip().lower()
        
        if raw in ("买入", "买", "buy", "b", "bought", "purchase"):
            return "buy"
        elif raw in ("卖出", "卖", "sell", "s", "sold", "redeem"):
            return "sell"
        return None

    def _parse_float(self, raw: Optional[str]) -> Optional[float]:
        """Parse float value, handling common formats."""
        if not raw:
            return None
        try:
            val = str(raw).strip().replace(",", "").replace(" ", "")
            if val.lower() in ("", "nan", "none", "null", "-"):
                return None
            return float(val)
        except (ValueError, TypeError):
            return None

    def _build_dedup_hash(self, record: Dict[str, Any]) -> str:
        """Build a hash for deduplication."""
        content = (
            f"{record['trade_date']!s}|"
            f"{record['symbol']}|"
            f"{record['side']}|"
            f"{record['quantity']}|"
            f"{record['price']}|"
            f"{record.get('_source_line_number', 0)}"
        )
        return hashlib.sha256(content.encode()).hexdigest()[:16]