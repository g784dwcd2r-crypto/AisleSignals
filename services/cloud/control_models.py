"""Strict cloud identity inputs. Client fields never establish authority."""

import re
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["OWNER", "MANAGER", "REVIEWER"]
Name = Annotated[str, Field(min_length=1, max_length=120)]
Token = Annotated[str, Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
Password = Annotated[str, Field(min_length=12, max_length=128)]
Code = Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value, info):
        if isinstance(value, str) and info.field_name not in {"password", "token", "challenge_token", "code"}:
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Control characters are not allowed")
            value = value.strip()
            if not value and info.field_name != "address":
                raise ValueError("Text cannot be empty")
        return value


class EmailInput(Input):
    email: Annotated[str, Field(min_length=3, max_length=254)]

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        value = value.lower()
        if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value):
            raise ValueError("Enter a valid email address")
        return value


class SetupBegin(EmailInput):
    token: Token
    organisation_name: Name
    name: Name
    password: Password


class ChallengeComplete(Input):
    challenge_token: Token
    code: Code


class Login(EmailInput):
    password: Annotated[str, Field(min_length=1, max_length=128)]


class Empty(Input):
    pass


class PharmacyCreate(Input):
    name: Name
    address: Annotated[str, Field(max_length=500)] = ""
    timezone: Literal["Europe/Dublin"] = "Europe/Dublin"


class PharmacyUpdate(Input):
    expected_version: Annotated[int, Field(ge=1)]
    name: Name | None = None
    address: Annotated[str, Field(max_length=500)] | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not (self.model_fields_set - {"expected_version"}) or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("Provide a non-null change")
        return self


def pharmacy_identifiers(values):
    if len(values) > 100 or len(set(values)) != len(values):
        raise ValueError("Provide distinct pharmacy identifiers")
    for value in values:
        if str(UUID(value)) != value:
            raise ValueError("Provide canonical pharmacy identifiers")
    return values


class InvitationCreate(EmailInput):
    name: Name
    role: Role
    pharmacy_ids: list[str]

    @field_validator("pharmacy_ids")
    @classmethod
    def identifiers(cls, values):
        return pharmacy_identifiers(values)

    @model_validator(mode="after")
    def scope(self):
        if (self.role == "OWNER") != (not self.pharmacy_ids):
            raise ValueError("Owners have organisation access; other roles require pharmacies")
        return self


class InvitationBegin(Input):
    token: Token
    name: Name
    password: Password


class UserUpdate(Input):
    expected_version: Annotated[int, Field(ge=1)]
    active: bool | None = None
    role: Role | None = None
    pharmacy_ids: list[str] | None = None

    @field_validator("pharmacy_ids")
    @classmethod
    def identifiers(cls, values):
        return pharmacy_identifiers(values) if values is not None else None

    @model_validator(mode="after")
    def has_change(self):
        if not (self.model_fields_set - {"expected_version"}) or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("Provide a non-null change")
        return self
