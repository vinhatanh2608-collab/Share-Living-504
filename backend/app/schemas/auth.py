from pydantic import BaseModel, Field, ConfigDict, field_validator


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def bcrypt_length(cls, value):
        if len(value.encode()) > 72 or "\0" in value:
            raise ValueError("Invalid password")
        return value


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class Profile(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    name: str


class VietQR(BaseModel):
    bank_bin: str = Field(pattern=r"^\d{6}$")
    account_number: str = Field(pattern=r"^\d{4,32}$")
    account_name: str = Field(min_length=1, max_length=100)
