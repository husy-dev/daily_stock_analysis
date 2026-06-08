# -*- coding: utf-8 -*-
"""Portfolio endpoints (P0 core account + snapshot workflow)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.portfolio import (
    PortfolioAccountCreateRequest,
    PortfolioAccountItem,
    PortfolioAccountListResponse,
    PortfolioAccountUpdateRequest,
    PortfolioCashLedgerListResponse,
    PortfolioCashLedgerCreateRequest,
    PortfolioCorporateActionListResponse,
    PortfolioCorporateActionCreateRequest,
    PortfolioDeleteResponse,
    PortfolioEventCreatedResponse,
    PortfolioFxRefreshResponse,
    PortfolioImportBrokerListResponse,
    PortfolioImportCommitResponse,
    PortfolioImportParseResponse,
    PortfolioImportTradeItem,
    PortfolioRiskResponse,
    PortfolioSnapshotResponse,
    PortfolioTradeListResponse,
    PortfolioTradeCreateRequest,
)
from src.services.portfolio_import_service import PortfolioImportService
from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import (
    PortfolioBusyError,
    PortfolioConflictError,
    PortfolioOversellError,
    PortfolioService,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# 全局数据库管理器实例
db_manager = DatabaseManager.get_instance()

router = APIRouter()


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "validation_error", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error(f"{message}: {exc}", exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: {str(exc)}"},
    )


def _conflict_error(*, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": error, "message": message},
    )


def _serialize_import_record(item: dict) -> PortfolioImportTradeItem:
    payload = dict(item)
    trade_date = payload.get("trade_date")
    if isinstance(trade_date, date):
        payload["trade_date"] = trade_date.isoformat()
    else:
        payload["trade_date"] = str(trade_date)
    return PortfolioImportTradeItem(**payload)


@router.post(
    "/accounts",
    response_model=PortfolioAccountItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Create portfolio account",
)
def create_account(request: PortfolioAccountCreateRequest) -> PortfolioAccountItem:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        row = service.create_account(
            name=request.name,
            broker=request.broker,
            market=request.market,
            base_currency=request.base_currency,
            owner_id=request.owner_id,
        )
        return PortfolioAccountItem(**row)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Create account failed", exc)


@router.get(
    "/accounts",
    response_model=PortfolioAccountListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List portfolio accounts",
)
def list_accounts(is_active: Optional[bool] = Query(None)) -> PortfolioAccountListResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        # Note: PortfolioService.list_accounts does not accept is_active parameter
        # We'll return all accounts and let frontend handle filtering if needed
        rows = service.list_accounts()
        if is_active is not None:
            # Filter accounts based on is_active flag if specified
            rows = [row for row in rows if row['is_active'] == is_active]
        return PortfolioAccountListResponse(accounts=[PortfolioAccountItem(**row) for row in rows])
    except Exception as exc:
        raise _internal_error("List accounts failed", exc)


@router.put(
    "/accounts/{account_id}",
    response_model=PortfolioAccountItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Update portfolio account",
)
def update_account(account_id: int, request: PortfolioAccountUpdateRequest) -> PortfolioAccountItem:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        row = service.update_account(
            account_id=account_id,
            name=request.name,
            broker=request.broker,
            market=request.market,
            base_currency=request.base_currency,
            owner_id=request.owner_id,
            is_active=request.is_active,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        return PortfolioAccountItem(**row)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Update account failed", exc)


@router.delete(
    "/accounts/{account_id}",
    response_model=PortfolioDeleteResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Delete portfolio account and all related events",
)
def delete_account(account_id: int) -> PortfolioDeleteResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        count = service.delete_account(account_id=account_id)
        return PortfolioDeleteResponse(deleted_count=count)
    except PortfolioBusyError:
        raise HTTPException(
            status_code=409,
            detail="Account is busy with ongoing operations, please try again later",
        )
    except Exception as exc:
        raise _internal_error("Delete account failed", exc)


@router.post(
    "/trades",
    response_model=PortfolioEventCreatedResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Record portfolio trade event",
)
def create_trade(request: PortfolioTradeCreateRequest) -> PortfolioEventCreatedResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        row = service.record_trade(
            account_id=request.account_id,
            symbol=request.symbol,
            trade_date=request.trade_date,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            fee=request.fee,
            tax=request.tax,
            market=request.market,
            currency=request.currency,
            trade_uid=request.trade_uid,
            note=request.note,
        )
        return PortfolioEventCreatedResponse(id=row["id"])
    except (ValueError, PortfolioConflictError) as exc:
        raise _bad_request(exc)
    except PortfolioBusyError:
        raise HTTPException(
            status_code=409,
            detail="Account is busy with ongoing operations, please try again later",
        )
    except Exception as exc:
        raise _internal_error("Create trade failed", exc)


@router.get(
    "/trades",
    response_model=PortfolioTradeListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List portfolio trade events with pagination",
)
def list_trades(
    account_id: Optional[int] = Query(None),
    symbol: Optional[str] = Query(None),
    side: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PortfolioTradeListResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        result = service.list_trade_events(
            account_id=account_id,
            symbol=symbol,
            side=side,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        return PortfolioTradeListResponse(
            items=result["items"],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List trades failed", exc)


@router.post(
    "/cash-ledger",
    response_model=PortfolioEventCreatedResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Record portfolio cash ledger event",
)
def create_cash_ledger(request: PortfolioCashLedgerCreateRequest) -> PortfolioEventCreatedResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        row = service.record_cash_ledger(
            account_id=request.account_id,
            event_date=request.event_date,
            direction=request.direction,
            amount=request.amount,
            currency=request.currency,
            note=request.note,
        )
        return PortfolioEventCreatedResponse(id=row["id"])
    except (ValueError, PortfolioConflictError) as exc:
        raise _bad_request(exc)
    except PortfolioBusyError:
        raise HTTPException(
            status_code=409,
            detail="Account is busy with ongoing operations, please try again later",
        )
    except Exception as exc:
        raise _internal_error("Create cash ledger failed", exc)


@router.get(
    "/cash-ledger",
    response_model=PortfolioCashLedgerListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List portfolio cash ledger events with pagination",
)
def list_cash_ledger(
    account_id: Optional[int] = Query(None, description="Optional account ID to filter cash ledger events, omit for all accounts"),
    direction: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PortfolioCashLedgerListResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        result = service.list_cash_ledger_events(
            account_id=account_id,
            direction=direction,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        return PortfolioCashLedgerListResponse(
            items=result["items"],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List cash ledger failed", exc)


@router.post(
    "/corporate-actions",
    response_model=PortfolioEventCreatedResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Record corporate action event",
)
def create_corporate_action(request: PortfolioCorporateActionCreateRequest) -> PortfolioEventCreatedResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        row = service.record_corporate_action(
            account_id=request.account_id,
            symbol=request.symbol,
            effective_date=request.effective_date,
            action_type=request.action_type,
            market=request.market,
            currency=request.currency,
            cash_dividend_per_share=request.cash_dividend_per_share,
            split_ratio=request.split_ratio,
            note=request.note,
        )
        return PortfolioEventCreatedResponse(id=row["id"])
    except (ValueError, PortfolioConflictError) as exc:
        raise _bad_request(exc)
    except PortfolioBusyError:
        raise HTTPException(
            status_code=409,
            detail="Account is busy with ongoing operations, please try again later",
        )
    except Exception as exc:
        raise _internal_error("Create corporate action failed", exc)


@router.get(
    "/corporate-actions",
    response_model=PortfolioCorporateActionListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List corporate action events with pagination",
)
def list_corporate_actions(
    account_id: Optional[int] = Query(None, description="Optional account ID to filter corporate actions, omit for all accounts"),
    symbol: Optional[str] = Query(None),
    action_type: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PortfolioCorporateActionListResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        result = service.list_corporate_actions(
            account_id=account_id,
            symbol=symbol,
            action_type=action_type,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        return PortfolioCorporateActionListResponse(
            items=result["items"],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List corporate actions failed", exc)


@router.get(
    "/risk",
    response_model=PortfolioRiskResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get portfolio risk analysis",
)
def get_portfolio_risk(
    account_id: Optional[int] = Query(None, description="Optional account ID to analyze specific account"),
    as_of: Optional[date] = Query(None, description="Date to calculate risk for (default: today)"),
    cost_method: str = Query("fifo", description="Cost calculation method: fifo or avg"),
) -> PortfolioRiskResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    risk_service = PortfolioRiskService()
    try:
        risk_analysis = risk_service.get_risk_report(
            account_id=account_id,
            as_of=as_of,
            cost_method=cost_method,
        )
        return PortfolioRiskResponse(**risk_analysis)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get portfolio risk analysis failed", exc)


@router.post(
    "/fx/refresh",
    response_model=PortfolioFxRefreshResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Refresh FX rates for portfolio accounts",
)
def refresh_fx_rates(
    account_id: Optional[int] = Query(None, description="Optional account ID to refresh specific account FX rates"),
    as_of: Optional[date] = Query(None, description="Date to refresh FX rates for (default: today)"),
) -> PortfolioFxRefreshResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        fx_refresh_result = service.refresh_fx_rates(
            account_id=account_id,
            as_of=as_of,
        )
        return PortfolioFxRefreshResponse(**fx_refresh_result)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Refresh FX rates failed", exc)


@router.get(
    "/imports/csv/brokers",
    response_model=PortfolioImportBrokerListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List supported broker CSV parsers",
)
def list_csv_brokers() -> PortfolioImportBrokerListResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    importer = PortfolioImportService()
    try:
        return PortfolioImportBrokerListResponse(brokers=importer.list_supported_brokers())
    except Exception as exc:
        raise _internal_error("List CSV brokers failed", exc)


@router.get(
    "/imports/positions/brokers",
    response_model=PortfolioImportBrokerListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List supported broker position snapshot parsers",
)
def list_position_brokers() -> PortfolioImportBrokerListResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    importer = PortfolioImportService()
    try:
        return PortfolioImportBrokerListResponse(brokers=importer.list_supported_position_parsers())
    except Exception as exc:
        raise _internal_error("List position snapshot brokers failed", exc)


@router.get(
    "/snapshot",
    response_model=PortfolioSnapshotResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get portfolio snapshot for all accounts",
)
def get_portfolio_snapshot(
    cost_method: str = Query("fifo", description="Cost calculation method: fifo/avg"),
    as_of: Optional[date] = Query(None, description="Date to calculate snapshot for (defaults to today)"),
) -> PortfolioSnapshotResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        snapshot = service.get_portfolio_snapshot(
            cost_method=cost_method,
            as_of=as_of,
        )
        return PortfolioSnapshotResponse(**snapshot)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get portfolio snapshot failed", exc)


@router.get(
    "/snapshot/{account_id}",
    response_model=PortfolioSnapshotResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get portfolio snapshot for specific account",
)
def get_account_snapshot(
    account_id: int,
    cost_method: str = Query("fifo", description="Cost calculation method: fifo/avg"),
    as_of: Optional[date] = Query(None, description="Date to calculate snapshot for (defaults to today)"),
) -> PortfolioSnapshotResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    service = PortfolioService()
    try:
        snapshot = service.get_portfolio_snapshot(
            account_id=account_id,
            cost_method=cost_method,
            as_of=as_of,
        )
        return PortfolioSnapshotResponse(**snapshot)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get account snapshot failed", exc)


@router.post(
    "/imports/csv/parse",
    response_model=PortfolioImportParseResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Parse broker CSV/Excel with preview (no commit)",
)
def parse_csv_import(
    broker: str = Form(..., description="Broker id: huatai/citic/cmb"),
    file: UploadFile = File(..., description="CSV or Excel file (.csv, .xlsx)"),
) -> PortfolioImportParseResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    importer = PortfolioImportService()
    try:
        content = file.file.read()
        parsed = importer.parse_trade_csv(broker=broker, content=content)
        return PortfolioImportParseResponse(
            broker=parsed["broker"],
            record_count=parsed["record_count"],
            skipped_count=parsed["skipped_count"],
            error_count=parsed["error_count"],
            records=[_serialize_import_record(item) for item in parsed.get("records", [])],
            errors=list(parsed.get("errors", [])),
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Parse CSV/Excel import failed", exc)


@router.post(
    "/imports/csv/commit",
    response_model=PortfolioImportCommitResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Parse and commit broker CSV/Excel with dedup",
)
def commit_csv_import(
    account_id: int = Form(...),
    broker: str = Form(..., description="Broker id: huatai/citic/cmb"),
    dry_run: bool = Form(False),
    file: UploadFile = File(..., description="CSV or Excel file (.csv, .xlsx)"),
) -> PortfolioImportCommitResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    importer = PortfolioImportService()
    try:
        content = file.file.read()
        parsed = importer.parse_trade_csv(broker=broker, content=content)
        result = importer.commit_trade_records(
            account_id=account_id,
            broker=parsed["broker"],
            records=list(parsed.get("records", [])),
            dry_run=dry_run,
        )
        return PortfolioImportCommitResponse(**result)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Commit CSV/Excel import failed", exc)


@router.post(
    "/imports/positions/parse",
    response_model=PortfolioImportParseResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Parse broker position snapshot CSV/Excel with preview (no commit)",
)
def parse_position_import(
    broker: str = Form(..., description="Broker id: cmb_snapshot"),
    file: UploadFile = File(..., description="CSV or Excel file (.csv, .xlsx)"),
) -> PortfolioImportParseResponse:
    # 确保数据库已初始化
    db_manager = DatabaseManager.get_instance()
    importer = PortfolioImportService()
    try:
        content = file.file.read()
        parsed = importer.parse_position_csv(broker=broker, content=content)
        # Convert position records to the same format as trade records for consistency
        records = []
        for item in parsed.get("records", []):
            # Map position data to trade record format for display purposes
            trade_format_item = {
                "trade_date": "N/A",  # Placeholder for position data
                "symbol": item.get("symbol", ""),
                "side": "hold",  # Indicates holding position
                "quantity": item.get("quantity", 0),
                "price": item.get("current_price", 0),  # Use current price as placeholder
                "fee": 0.0,
                "tax": 0.0,
                "dedup_hash": f"pos_{item.get('symbol', '')}_{item.get('quantity', 0)}",
                "currency": "CNY"  # Default currency
            }
            records.append(PortfolioImportTradeItem(**trade_format_item))
        
        return PortfolioImportParseResponse(
            broker=parsed["broker"],
            record_count=parsed["record_count"],
            skipped_count=parsed["skipped_count"],
            error_count=parsed["error_count"],
            records=records,
            errors=list(parsed.get("errors", [])),
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Parse position CSV/Excel import failed", exc)