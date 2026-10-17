"""Version 1 wire contracts for the independent synchronization projects.

No persistence or HTTP handlers live here. Stateful checks are specified in
docs/sync-api-v1.md; schemas can be consumed without starting the legacy app.
"""
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, field_validator, model_validator

API_VERSION = 1
MAX_BODY_BYTES = 2_000_000
MAX_MIGRATION_BYTES = 64 * 1024 * 1024
MAX_PAGE_RECORDS = 500

Identifier = Annotated[str, Field(min_length=1, max_length=240)]
Text = Annotated[str, Field(max_length=2000)]
Currency = Annotated[str, Field(pattern=r'^[A-Z]{3,12}$')]
Counter = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, ge=1)]
Money = Annotated[str, Field(pattern=r'^(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?$')]


def calendar_date(value):
    if type(value) is date or (isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)):
        return value
    raise ValueError('Expected a calendar date in YYYY-MM-DD format')


def timestamp(value):
    if isinstance(value, datetime) or (isinstance(value, str) and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value
    )):
        return value
    raise ValueError('Expected an RFC3339 timestamp with offset')


WireDate = Annotated[date, BeforeValidator(calendar_date)]
WireTime = Annotated[AwareDatetime, BeforeValidator(timestamp)]
Selection = Literal['unconfigured', 'enabled', 'ignored']
CaptureState = Literal['planned', 'collecting', 'paused', 'complete', 'cancelled', 'failed']
DeliveryState = Literal['waiting', 'running', 'complete', 'blocked']
ErrorCode = Literal[
    'unauthorized', 'unsupported_api_version', 'validation_error', 'payload_too_large',
    'bank_login_required', 'unsupported_bank', 'unsupported_currency', 'account_unconfigured',
    'account_disabled', 'settings_conflict', 'account_busy', 'capture_busy', 'lease_expired',
    'idempotency_conflict', 'history_incomplete', 'capture_context_changed',
    'zenmoney_unavailable', 'zenmoney_account_unavailable', 'delivery_uncertain', 'existing_ownership_unknown', 'not_found',
    'method_not_allowed', 'internal_error',
    'migration_busy', 'migration_paused', 'migration_not_empty', 'migration_invalid',
    'migration_wrong_account', 'migration_incomplete',
]


class WireModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DateRange(WireModel):
    start: WireDate
    end: WireDate

    @model_validator(mode='after')
    def ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError('Range start must not follow end')
        return self


class Capabilities(WireModel):
    timezone: Identifier
    currencies: list[Currency] = Field(min_length=1, max_length=100)
    max_window_days: Annotated[int, Field(strict=True, ge=1, le=366)]
    earliest_date: WireDate | None = None
    details: Literal['included', 'separate', 'unavailable']

    @field_validator('timezone')
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Expected an IANA timezone') from None
        return value


class DiscoveredAccount(WireModel):
    source_key: Identifier
    identity_scope: Literal['bank', 'profile']
    label: Text
    masked_number: Annotated[str, Field(max_length=120)]
    currency: Currency
    scale: Annotated[int, Field(strict=True, ge=0, le=12)]
    supported: StrictBool
    unsupported_reason: Text | None = None

    @model_validator(mode='after')
    def support_reason(self) -> Self:
        if not self.supported and not self.unsupported_reason:
            raise ValueError('Unsupported account requires a reason')
        return self


class DiscoverConnection(WireModel):
    bank: Annotated[str, Field(pattern=r'^[a-z0-9_-]{1,40}$')]
    connector_version: Annotated[str, Field(min_length=1, max_length=40)]
    profile_key: Identifier
    profile_identity: Literal['bank', 'user_selected']
    capabilities: Capabilities
    accounts_complete: StrictBool
    accounts: list[DiscoveredAccount] = Field(max_length=200)
    label: Annotated[str, Field(min_length=1, max_length=120)] | None = None

    @field_validator('label')
    @classmethod
    def clean_label(cls, value):
        if value is not None:
            value = value.strip()
            if not value or any(ord(c) < 32 for c in value):
                raise ValueError('Expected a nonempty connection name without control characters')
        return value

    @model_validator(mode='after')
    def unique_accounts(self) -> Self:
        keys = [(a.identity_scope, a.source_key, a.currency) for a in self.accounts]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate account identity')
        if any(a.supported and a.currency not in self.capabilities.currencies for a in self.accounts):
            raise ValueError('Supported account currency is absent from capabilities')
        return self


class AccountSettings(WireModel):
    expected_version: Counter
    selection: Selection
    zenmoney_account_id: Identifier | None = None
    import_start: WireDate | None = None

    @model_validator(mode='after')
    def enabled_target(self) -> Self:
        if self.selection == 'enabled':
            if self.zenmoney_account_id is None or self.import_start is None:
                raise ValueError('Enabled account requires a target and import start')
        elif self.zenmoney_account_id is not None:
            raise ValueError('Inactive account must not carry a target')
        return self


class AccountView(WireModel):
    id: UUID
    source_key: Identifier
    label: Text
    masked_number: Annotated[str, Field(max_length=120)]
    currency: Currency
    scale: Annotated[int, Field(strict=True, ge=0, le=12)]
    supported: StrictBool
    unsupported_reason: Text | None = None
    settings_version: Counter
    selection: Selection
    zenmoney_account_id: Identifier | None = None
    import_start: WireDate | None = None
    last_complete_end: WireDate | None = None


class ConnectionView(WireModel):
    id: UUID
    bank: Identifier
    accounts_complete: StrictBool
    accounts: list[AccountView]
    profile_key: Identifier | None = None
    profile_identity: Literal['bank', 'user_selected'] | None = None
    label: Text = ''


class ConnectionCatalog(WireModel):
    connections: list[ConnectionView]


class ConnectionLabel(WireModel):
    label: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator('label')
    @classmethod
    def clean_label(cls, value):
        return DiscoverConnection.clean_label(value)


class ZenAccount(WireModel):
    id: Identifier
    title: Text
    currency: Currency
    instrument: Positive


class AccountCatalog(WireModel):
    refreshed_at: WireTime | None
    accounts: list[ZenAccount]


class ServerInfo(WireModel):
    api_version: Literal[1] = API_VERSION
    service_version: Identifier
    zenmoney_state: Literal['ready', 'not_configured', 'unavailable']
    last_zenmoney_refresh: WireTime | None
    max_body_bytes: Positive = MAX_BODY_BYTES
    max_page_records: Positive = MAX_PAGE_RECORDS


class StartSync(WireModel):
    connection_id: UUID
    request_id: UUID
    reimport_from: WireDate | None = None


class CaptureLeaseRequest(WireModel):
    worker_id: UUID


class CaptureLease(WireModel):
    worker_id: UUID
    epoch: Positive
    expires_at: WireTime


class CaptureWrite(WireModel):
    worker_id: UUID
    lease_epoch: Positive


class BankRecord(WireModel):
    external_id: Identifier
    identity_kind: Literal['bank', 'fingerprint']
    identity_version: Annotated[str, Field(min_length=1, max_length=40)]
    occurred_at: WireTime
    local_date: WireDate
    direction: Literal['expense', 'income']
    amount: Money
    currency: Currency
    description: Text
    beneficiary_account: Annotated[str, Field(max_length=240)] | None = None
    beneficiary_name: Text | None = None
    balance: Annotated[str, Field(pattern=r'^-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?$')] | None = None
    details_state: Literal['complete', 'unavailable']
    details: dict[Identifier, Text] = Field(default_factory=dict, max_length=64)

    @field_validator('amount')
    @classmethod
    def nonzero_amount(cls, value: str) -> str:
        if Decimal(value) <= 0:
            raise ValueError('Amount must be positive; direction is separate')
        return value


class CapturePage(CaptureWrite):
    page_number: Positive
    total_pages: Annotated[int, Field(strict=True, ge=1, le=10000)]
    total_records: Annotated[int, Field(strict=True, ge=0, le=1000000)]
    records: list[BankRecord] = Field(max_length=MAX_PAGE_RECORDS)

    @model_validator(mode='after')
    def page_counts(self) -> Self:
        if self.page_number > self.total_pages or len(self.records) > self.total_records:
            raise ValueError('Invalid page counts')
        if self.total_records == 0 and (self.total_pages != 1 or self.page_number != 1 or self.records):
            raise ValueError('Empty interval uses a single empty page')
        if self.total_records and not self.records:
            raise ValueError('Nonempty history cannot contain an empty page')
        ids = [r.external_id for r in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate record identity within a page')
        return self


class CaptureComplete(CaptureWrite):
    total_pages: Annotated[int, Field(strict=True, ge=1, le=10000)]
    total_records: Annotated[int, Field(strict=True, ge=0, le=1000000)]


class PageReceipt(WireModel):
    page_number: Positive
    received_records: Counter
    repeated: StrictBool


class CaptureStatus(CaptureWrite):
    state: Literal['paused', 'failed']
    code: Literal['bank_login_required', 'capture_context_changed', 'history_incomplete']


class IntervalView(WireModel):
    id: UUID
    period: DateRange
    state: CaptureState
    received_pages: Counter
    received_records: Counter


class AccountProgress(WireModel):
    account_id: UUID
    settings_version: Counter
    capture_state: CaptureState
    intervals: list[IntervalView]


class DeliveryCounts(WireModel):
    pending: Counter
    delivered: Counter
    suppressed: Counter
    uncertain: Counter


class ApiError(WireModel):
    code: ErrorCode
    message: Text
    retryable: StrictBool
    request_id: UUID


class JobView(WireModel):
    id: UUID
    connection_id: UUID
    capture_state: CaptureState
    delivery_state: DeliveryState
    end_date: WireDate
    accounts: list[AccountProgress]
    delivery: DeliveryCounts
    errors: list[ApiError]


class ErrorResponse(WireModel):
    error: ApiError


CONTRACT_MODELS = (
    DiscoverConnection, ConnectionView, ConnectionCatalog, ConnectionLabel, AccountSettings, AccountView, ServerInfo,
    AccountCatalog, StartSync, CaptureLeaseRequest, CaptureLease, CapturePage,
    CaptureComplete, CaptureStatus, PageReceipt, IntervalView, JobView, ErrorResponse,
)


def json_schemas() -> dict:
    """Generate self-contained JSON Schemas for a mock/extension consumer."""
    return {model.__name__: model.model_json_schema() for model in CONTRACT_MODELS}
