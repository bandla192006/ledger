from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

# 10^12 paise = ₹10,000,000,000 per transaction. Well inside JavaScript's safe
# integer range, so amounts survive a round-trip through the browser.
MAX_AMOUNT = 1_000_000_000_000

# Strict: rejects floats (100.5), numeric strings ("100"), booleans (true), and <= 0.
Amount = Annotated[StrictInt, Field(gt=0, le=MAX_AMOUNT, description="Integer minor units (paise / cents)")]
Currency = Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")]
Description = Annotated[StrictStr, Field(max_length=280)]
ExternalRef = Annotated[StrictStr, Field(max_length=120)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------ money movement --

class DepositRequest(StrictModel):
    account_id: UUID
    amount: Amount
    currency: Currency = "INR"
    description: Description | None = None
    external_ref: ExternalRef | None = Field(default=None, description="Reference from the external payment rail")


class WithdrawalRequest(StrictModel):
    account_id: UUID
    amount: Amount
    currency: Currency = "INR"
    description: Description | None = None
    external_ref: ExternalRef | None = None


class TransferRequest(StrictModel):
    source_account_id: UUID
    destination_account_id: UUID
    amount: Amount
    currency: Currency = "INR"
    description: Description | None = None


# ------------------------------------------------------------------ identity --

class LoginRequest(StrictModel):
    email: Annotated[StrictStr, Field(min_length=3, max_length=254)]
    password: Annotated[StrictStr, Field(min_length=1, max_length=256)]


class CreateUserRequest(StrictModel):
    email: Annotated[StrictStr, Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=254)]
    full_name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    role: Literal["ADMIN", "ACCOUNT_HOLDER", "AUDITOR"]
    password: Annotated[StrictStr, Field(min_length=8, max_length=256)]


# ------------------------------------------------------------------ accounts --

class CreateAccountRequest(StrictModel):
    owner_id: UUID
    name: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    currency: Currency = "INR"


class AccountStatusRequest(StrictModel):
    status: Literal["ACTIVE", "FROZEN"]
    reason: Annotated[StrictStr, Field(max_length=280)] | None = None


# --------------------------------------------------------------------- audit --

class AnomalyRequest(StrictModel):
    target_type: Literal["ACCOUNT", "TRANSACTION", "USER", "LEDGER_ENTRY"]
    target_id: UUID
    severity: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    reason: Annotated[StrictStr, Field(min_length=1, max_length=500)]
