from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.auth_security import normalize_email


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=7, max_length=128)
    confirm_password: str = Field(min_length=7, max_length=128)
    next: str | None = Field(default=None, max_length=2048)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Enter your name.")
        return cleaned

    @model_validator(mode="after")
    def passwords_match(self) -> "RegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    next: str | None = Field(default=None, max_length=2048)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)


class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class VerifyEmailRequest(TokenRequest):
    password: str = Field(min_length=7, max_length=128)
    confirm_password: str = Field(min_length=7, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "VerifyEmailRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class ResetPasswordRequest(TokenRequest):
    password: str = Field(min_length=7, max_length=128)
    confirm_password: str = Field(min_length=7, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class ProfileUpdateRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Enter your name.")
        return cleaned


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    full_name: str
    email: str
    email_verified: bool
    onboarding_completed: bool


class CsrfRead(BaseModel):
    csrf_token: str


class AuthActionRead(BaseModel):
    message: str


class LoginResponse(BaseModel):
    user: UserRead
    next: str
    csrf_token: str


class AuthConfigurationRead(BaseModel):
    password_recovery_available: bool
