"""Validation boundary for imported, edited and public records."""
import re
import os
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CATEGORIES = ['концерт', 'спектакль', 'выставка', 'фестиваль', 'кино', 'экскурсия', 'другое']
STATUSES = {'опубликовано': 'published', 'черновик': 'draft', 'отменено': 'cancelled',
            'published': 'published', 'draft': 'draft', 'cancelled': 'cancelled', 'rescheduled': 'rescheduled'}
# The collection region is explicit. Unknown settlements require regional metadata.
CITIES = {'Уфа', 'Стерлитамак', 'Салават', 'Нефтекамск', 'Октябрьский', 'Туймазы', 'Белорецк',
          'Ишимбай', 'Сибай', 'Кумертау', 'Мелеуз', 'Белебей', 'Бирск', 'Учалы', 'Благовещенск',
          'Дюртюли', 'Янаул', 'Давлеканово', 'Баймак', 'Агидель', 'Межгорье', 'Чишмы', 'Иглино',
          'Раевский', 'Приютово', 'Красноусольский', 'Мишкино', 'Аскарово', 'Бураево', 'Кушнаренково'}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {'script', 'style'}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag.lower() in {'script', 'style'}:
            self.ignored = max(0, self.ignored - 1)
        if tag.lower() in {'p', 'br', 'div'}:
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def clean_text(value):
    parser = PlainText()
    parser.feed(str(value))
    return ' '.join(''.join(parser.parts).split()).strip()


def normalize(value):
    return re.sub(r'[^\w]+', ' ', value.casefold().replace('ё', 'е')).strip()


CITY_ALIASES = {normalize(city): city for city in CITIES}
# Only known names are canonicalized. No inferred coordinates or districts.
VENUE_ALIASES = {
    normalize('Башкирский государственный театр оперы и балета'): 'Башкирский театр оперы и балета',
    normalize('Башкирский театр оперы и балета'): 'Башкирский театр оперы и балета',
    normalize('Государственный академический русский драматический театр Республики Башкортостан'): 'Русский драматический театр Башкортостана',
    normalize('Русский драматический театр Республики Башкортостан'): 'Русский драматический театр Башкортостана',
}


def canonical_city(value):
    value = clean_text(value)
    key = normalize(re.sub(r'(?i)^(?:г\.?\s+|город\s+)', '', value))
    return CITY_ALIASES.get(key, value)


def canonical_venue(value):
    value = clean_text(value)
    return VENUE_ALIASES.get(normalize(value), value)


def safe_url(value):
    if value is None or value == '':
        return None
    parts = urlsplit(str(value))
    if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('Требуется HTTP(S) ссылка без учётных данных')
    if len(str(value)) > 2000:
        raise ValueError('Ссылка слишком длинная')
    return str(value)


class EventInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=300)
    category: str
    tags: list[str] = Field(default_factory=list, max_length=30)
    date_start: datetime
    date_end: datetime | None = None
    duration_min: int | None = Field(default=None, ge=1, le=10080)
    city: str = Field(min_length=2, max_length=120)
    region: str | None = None
    venue: str = Field(min_length=2, max_length=300)
    address: str | None = Field(default=None, max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lon: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    description: str = Field(default='', max_length=20000)
    price_min: float | None = Field(default=None, ge=0, le=10000000, allow_inf_nan=False)
    price_max: float | None = Field(default=None, ge=0, le=10000000, allow_inf_nan=False)
    is_free: bool = False
    age_limit: int | None = None
    language: str | None = Field(default=None, max_length=120)
    org_link: str | None = None
    poster_url: str | None = None
    poster_rights: str | None = Field(default=None, min_length=8, max_length=1000)
    source_url: str
    source_type: Literal['import', 'manual', 'official', 'website', 'rss', 'api', 'timepad', 'proculture', 'bashopera', 'rusdram'] = 'import'
    provider_id: str | None = Field(default=None, max_length=300)
    status: str = 'draft'

    @field_validator('source_type', mode='before')
    @classmethod
    def source_kind(cls, value):
        return {'страница': 'website', 'API': 'api', 'ручной': 'manual'}.get(value, value)

    @field_validator('title', 'venue', 'description', 'address', 'city', mode='before')
    @classmethod
    def text(cls, value):
        return clean_text(value) if value is not None else None

    @field_validator('city')
    @classmethod
    def city_alias(cls, value):
        return canonical_city(value)

    @field_validator('venue')
    @classmethod
    def venue_alias(cls, value):
        return canonical_venue(value)

    @field_validator('source_url', 'org_link')
    @classmethod
    def urls(cls, value):
        return safe_url(value)

    @field_validator('source_url')
    @classmethod
    def required_source(cls, value):
        if not value:
            raise ValueError('Нужна ссылка на первоисточник')
        return value

    @field_validator('poster_url')
    @classmethod
    def approved_poster(cls, value):
        if not value:
            return None
        value = safe_url(value)
        parsed = urlsplit(value)
        from .security import allowed_hosts
        hosts = {host.strip().lower() for host in os.getenv('POSTER_ALLOWED_HOSTS', '').split(',') if host.strip()}
        hosts |= allowed_hosts() | {'images.culture.ru', 'static.culture.ru', 'cdn.culture.ru'}
        if parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.port not in {None, 443}:
            raise ValueError('Афиша должна быть HTTPS изображением с разрешённого узла источника')
        return value

    @field_validator('date_start', 'date_end')
    @classmethod
    def timezone_required(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError('Нужен часовой пояс, например +05:00')
            if not 1900 <= value.year <= 9998:
                raise ValueError('Дата должна быть между 1900 и 9998 годами')
            return value.astimezone(timezone.utc)
        return value

    @field_validator('category')
    @classmethod
    def category_known(cls, value):
        if value not in CATEGORIES:
            raise ValueError('Неизвестная категория')
        return value

    @field_validator('age_limit')
    @classmethod
    def age_known(cls, value):
        if value not in {None, 0, 6, 12, 16, 18}:
            raise ValueError('Возраст должен быть 0, 6, 12, 16 или 18')
        return value

    @field_validator('status')
    @classmethod
    def status_known(cls, value):
        if value not in STATUSES:
            raise ValueError('Неизвестный статус')
        return STATUSES[value]

    @model_validator(mode='after')
    def consistency(self):
        if bool(self.poster_url) != bool(self.poster_rights):
            raise ValueError('Для афиши укажите подтверждение разрешения правообладателя или лицензии')
        if self.city not in CITIES and self.region not in {'Башкортостан', 'Республика Башкортостан', '02'}:
            raise ValueError('Укажите населённый пункт Башкортостана и регион для неизвестного справочнику посёлка')
        if self.date_end and self.date_end < self.date_start:
            raise ValueError('Конец события раньше начала')
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError('Нижняя цена превышает верхнюю')
        if self.is_free and any(value not in {None, 0} for value in [self.price_min, self.price_max]):
            raise ValueError('Бесплатное событие не может иметь платную цену')
        if self.is_free:
            self.price_min = self.price_max = 0
        return self


class ImportInput(BaseModel):
    format: str = Field(pattern='^(json|csv)$')
    content: str = Field(min_length=2, max_length=5_000_000)


class LoginInput(BaseModel):
    username: str = Field(default='admin', min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    password: str = Field(min_length=1, max_length=300)


class VerifyInput(BaseModel):
    note: str = Field(min_length=5, max_length=2000)
    fetch: bool = False


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    city: str = Field(min_length=2, max_length=120)
    category: str | None = Field(default=None, max_length=40)
    period: str | None = Field(default=None, max_length=40)

    @field_validator('city')
    @classmethod
    def city_alias(cls, value):
        return canonical_city(value)

    @field_validator('category')
    @classmethod
    def category_known(cls, value):
        if value is not None and value not in CATEGORIES:
            raise ValueError('Неизвестная категория')
        return value


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=5, max_length=5000)
    event_id: str | None = Field(default=None, max_length=36)
    contact: str | None = Field(default=None, max_length=300)
    website: str | None = Field(default=None, max_length=500)

    @field_validator('message', mode='before')
    @classmethod
    def plain_message(cls, value):
        return clean_text(value)


class NotificationClaimInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(default=20, ge=1, le=100)


class NotificationAckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lease_token: str = Field(min_length=1, max_length=64)
    success: bool
    uncertain: bool = False
    error: str | None = Field(default=None, max_length=500)

class SourceOutput(BaseModel):
    url: str
    name: str
    verified_at: datetime | None


class EventOutput(BaseModel):
    id: str
    title: str
    normalized_title: str
    category: str
    tags: list[str]
    date_start: datetime
    date_end: datetime | None
    duration_min: int | None
    city: str
    venue: str
    address: str | None
    lat: float | None
    lon: float | None
    description: str
    price_min: float | None
    price_max: float | None
    is_free: bool
    age_limit: int | None
    language: str | None
    org_link: str | None
    poster_url: str | None = None
    poster_rights: str | None = None
    image_url: str | None = None
    image_source_url: str | None = None
    ticket_availability: str | None = None
    ticket_checked_at: datetime | None = None
    ticket_links: list[dict[str, str]] = Field(default_factory=list)
    source_url: str
    source_type: str
    verified_at: datetime | None
    verification_expired: bool
    confidence: float
    status: str
    sources: list[SourceOutput]
    created_at: datetime
    updated_at: datetime


class EventListOutput(BaseModel):
    items: list[EventOutput]
    total: int
    page: int
    page_size: int


class SubscriptionOutput(BaseModel):
    id: str
    delete_token: str
    notifications: str


class FeedbackOutput(BaseModel):
    id: str
    status: str


class ErrorOutput(BaseModel):
    code: str
    message: str
    details: object | None = None
    request_id: str
