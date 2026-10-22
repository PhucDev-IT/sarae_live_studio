"""Structured product facts the AI and the zero-token rules may state: size chart, colors, material, shipping.

TikTok owns a product's name, price and stock; these facts are Sarae's own, edited by the owner next to the AI
description and stored as JSON in products.ai_facts. A fact that is not here is never stated by a rule.
"""
import json

from pydantic import BaseModel, Field, field_validator, model_validator


class SizeRow(BaseModel):
    size: str = Field(min_length=1, max_length=10)
    weight_min: float | None = Field(default=None, ge=20, le=200)
    weight_max: float | None = Field(default=None, ge=20, le=200)
    height_min: float | None = Field(default=None, ge=90, le=230)
    height_max: float | None = Field(default=None, ge=90, le=230)
    note: str = Field(default="", max_length=80)

    @field_validator("size")
    @classmethod
    def upper(cls, value: str) -> str:
        value = " ".join(value.split()).upper()
        if not value:
            raise ValueError("Thiếu tên size")
        return value

    @model_validator(mode="after")
    def ranges(self):
        for low, high in ((self.weight_min, self.weight_max), (self.height_min, self.height_max)):
            if low is not None and high is not None and low > high:
                raise ValueError("Khoảng size không hợp lệ (từ > đến)")
        return self


class ProductFacts(BaseModel):
    sizes: list[SizeRow] = Field(default_factory=list, max_length=12)
    colors: list[str] = Field(default_factory=list, max_length=20)
    material: str = Field(default="", max_length=200)
    shipping: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=500)

    @field_validator("colors")
    @classmethod
    def clean_colors(cls, values: list[str]) -> list[str]:
        out = []
        for value in values:
            value = " ".join(str(value).split())[:30]
            if value and value.casefold() not in {v.casefold() for v in out}:
                out.append(value)
        return out

    @field_validator("material", "shipping", "notes")
    @classmethod
    def strip(cls, value: str) -> str:
        return " ".join(value.split())


def parse(raw) -> dict:
    """products.ai_facts (JSON text, possibly empty or from an older shape) -> validated dict."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raw = {}
    try:
        return ProductFacts.model_validate(raw or {}).model_dump()
    except ValueError:
        return ProductFacts().model_dump()


def sizes_for(facts: dict, weight: float | None, height: float | None) -> list[str]:
    """Sizes whose chart range contains the viewer's weight (and height, where the row has one)."""
    matches = []
    for row in facts.get("sizes") or []:
        if weight is None or row["weight_min"] is None or row["weight_max"] is None:
            continue
        if not row["weight_min"] <= weight <= row["weight_max"]:
            continue
        if height is not None and row["height_min"] is not None and row["height_max"] is not None \
                and not row["height_min"] <= height <= row["height_max"]:
            continue
        matches.append(row["size"])
    return matches
