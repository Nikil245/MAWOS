from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class BookInput(Input):
    isbn: str = Field(min_length=1, max_length=32, pattern=r'^[0-9Xx -]+$')
    title: str = Field(min_length=1, max_length=256)
    author: str = Field(min_length=1, max_length=256)
    publisher: str | None = Field(None, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    departments: list[str] = Field(default_factory=list, max_length=100)
    description: str | None = Field(None, max_length=10000)
    total_copies: int = Field(ge=0, le=100000, strict=True)
    is_active: bool = True

    @field_validator('isbn')
    @classmethod
    def isbn_normalized(cls, value):
        value = value.replace('-', '').replace(' ', '').upper()
        if len(value) not in (10, 13) or not value[:-1].isdigit() or not (value[-1].isdigit() or len(value) == 10 and value[-1] == 'X'):
            raise ValueError('ISBN must contain 10 or 13 digits (ISBN-10 may end in X)')
        return value

    @field_validator('departments')
    @classmethod
    def canonical_departments(cls, values):
        values = sorted({value.strip().upper() for value in values})
        if any(not value or len(value) > 8 for value in values):
            raise ValueError('Department codes must be 1–8 characters')
        return values


class ReserveInput(Input):
    book_id: int = Field(gt=0, strict=True)


class DirectIssueInput(ReserveInput):
    student_usn: str = Field(min_length=1, max_length=16)

    @field_validator('student_usn')
    @classmethod
    def usn(cls, value):
        return value.upper()


class ReturnInput(Input):
    rating: int | None = Field(None, ge=1, le=5, strict=True)
    comment: str | None = Field(None, max_length=2000)

    @model_validator(mode='after')
    def comment_needs_rating(self):
        if self.comment and self.rating is None:
            raise ValueError('Choose a rating when submitting a review')
        return self


class RejectInput(Input):
    reason: str = Field(min_length=1, max_length=1000)


class SlipInput(Input):
    slip_code: str = Field(pattern=r'^\d{6}$')


class LibrarianCreate(Input):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-z0-9._@-]+$')
    display_name: str = Field(min_length=2, max_length=128)


class LibrarianUpdate(Input):
    display_name: str = Field(min_length=2, max_length=128)
    active: bool
