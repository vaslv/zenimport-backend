from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RawRow(StrictModel):
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


class BankImport(StrictModel):
    bank: str = Field(pattern=r'^[a-z0-9_-]{1,40}$')
    adapter_version: str = Field(min_length=1, max_length=30)
    account: str = Field(min_length=1, max_length=120)
    currency: Literal['VND', 'USD', 'EUR']
    transactions: list[BankTransaction] = Field(min_length=1, max_length=2000)


class Mapping(StrictModel):
    zenmoney_account: str


class Decision(StrictModel):
    action: Literal['create', 'link', 'ignore']
    category: str | None = None
    match_id: str | None = None
    # Optimistic concurrency: the operator must have seen these candidates.
    reviewed_candidates: list[str] = Field(default_factory=list, max_length=2000)
