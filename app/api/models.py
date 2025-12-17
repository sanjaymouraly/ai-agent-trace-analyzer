"""Request/response models for the public API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class AnnotationRequest(BaseModel):
    author: str = Field(default="reviewer", max_length=120)
    note: str = Field(min_length=1, max_length=5000)
    finding_id: str | None = Field(default=None, max_length=200)
    disposition: str | None = Field(default=None, max_length=40)


class CompareRequest(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=8)

    @field_validator("run_ids")
    @classmethod
    def unique_runs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("run_ids must be unique")
        return value


class ExperimentRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=200)
    source_span_id: str = Field(min_length=1, max_length=200)
    replacement_output: Any
    title: str = Field(default="Counterfactual fixture check", min_length=1, max_length=180)
    confirm_side_effect_free: bool = False


class RedactionRequest(BaseModel):
    enabled: bool
    confirm_sensitive: bool = False


class LLMSettingsRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(default="", max_length=200)


class LLMCredentialRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    api_key: str | None = Field(default=None, max_length=400)
    base_url: str | None = Field(default=None, max_length=400)
    model: str | None = Field(default=None, max_length=200)


class LLMAssistRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=3, max_length=1200)
    authorize_external_transfer: bool = False


class LLMEstimateRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=3, max_length=1200)


class WorkspaceCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]{0,118}[a-z0-9]$")
    name: str = Field(default="", max_length=200)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="", max_length=120)
