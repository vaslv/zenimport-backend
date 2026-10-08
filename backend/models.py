from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BankDetails(StrictModel):
    currency: str | None = Field(default=None, max_length=2000)
    remark: str | None = Field(default=None, max_length=2000)
    amount: str | None = Field(default=None, max_length=2000)
    balance: str | None = Field(default=None, max_length=2000)
    trxId: str | None = Field(default=None, max_length=2000)
    processDate: str | None = Field(default=None, max_length=2000)
    dorC: str | None = Field(default=None, max_length=2000)
    refType: str | None = Field(default=None, max_length=2000)
    refId: str | None = Field(default=None, max_length=2000)
    tellerId: str | None = Field(default=None, max_length=2000)
    corresponsiveAccount: str | None = Field(default=None, max_length=2000)
    corresponsiveName: str | None = Field(default=None, max_length=2000)
    channel: str | None = Field(default=None, max_length=2000)
    serviceBranchId: str | None = Field(default=None, max_length=2000)
    serviceBranchName: str | None = Field(default=None, max_length=2000)
    pmtType: str | None = Field(default=None, max_length=2000)
    sendingBankId: str | None = Field(default=None, max_length=2000)
    sendingBranchId: str | None = Field(default=None, max_length=2000)
    sendingBranchName: str | None = Field(default=None, max_length=2000)
    receivingBankId: str | None = Field(default=None, max_length=2000)
    receivingBranchId: str | None = Field(default=None, max_length=2000)
    receivingBranchName: str | None = Field(default=None, max_length=2000)


class RawRow(StrictModel):
    details: BankDetails | None = None
    reference_kind: Literal['bank', 'fingerprint'] = 'bank'
    balance: str | None = Field(default=None, max_length=100)
    reference: str = Field(min_length=1, max_length=200)
    date: str = Field(max_length=100)
    amount: str = Field(max_length=100)
    description: str = Field(max_length=2000)


class BankTransaction(StrictModel):
    external_id: str = Field(min_length=1, max_length=200)
    date: date
    amount_minor: int = Field(strict=True, ge=-9000000000000, le=9000000000000)
    description: str = Field(max_length=2000)
    raw: RawRow


class Capture(StrictModel):
    start_date: date | None = None
    end_date: date | None = None
    row_start: int = Field(ge=1)
    row_end: int = Field(ge=1)
    total_rows: int = Field(ge=1, le=100000)
    pages: int = Field(ge=1, le=2000)
    complete: bool


class BankImport(StrictModel):
    capture: Capture | None = None
    bank: str = Field(pattern=r'^[a-z0-9_-]{1,40}$')
    adapter_version: str = Field(min_length=1, max_length=30)
    account: str = Field(min_length=1, max_length=120)
    currency: Literal['VND', 'USD', 'EUR']
    transactions: list[BankTransaction] = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def validate_capture(self):
        c = self.capture
        if c:
            if not (c.row_start <= c.row_end <= c.total_rows) or c.row_end-c.row_start+1 != len(self.transactions):
                raise ValueError('Capture count differs from transactions')
            if c.complete != (c.row_start == 1 and c.row_end == c.total_rows):
                raise ValueError('Invalid completeness claim')
            if bool(c.start_date) != bool(c.end_date) or (c.start_date and c.start_date > c.end_date):
                raise ValueError('Invalid capture period')
            if c.start_date and any(not c.start_date <= t.date <= c.end_date for t in self.transactions):
                raise ValueError('Transaction outside selected period')
        if self.bank == 'vietinbank' and (self.currency != 'VND' or not c):
            raise ValueError('VietinBank requires VND and capture metadata')
        return self


class Mapping(StrictModel):
    zenmoney_account: str


class Decision(StrictModel):
    action: Literal['create', 'link', 'ignore']
    category: str | None = None
    match_id: str | None = None
    # Optimistic concurrency: the operator must have seen these candidates.
    reviewed_candidates: list[str] = Field(default_factory=list, max_length=2000)


class DayDecision(StrictModel):
    snapshot: str = Field(pattern=r'^[a-f0-9]{64}$')


class ReviewedDay(DayDecision):
    bank_account: str = Field(min_length=1, max_length=200)
    date: date


class BulkDayDecision(StrictModel):
    days: list[ReviewedDay] = Field(min_length=1, max_length=5000)


class SplitDecision(DayDecision):
    zen_ids: list[str] = Field(min_length=2, max_length=18)


class SettlementDecision(DayDecision):
    bank_ids: list[str] = Field(min_length=1, max_length=100)
    zen_ids: list[str] = Field(min_length=1, max_length=100)
