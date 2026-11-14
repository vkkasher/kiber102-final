"""Persistent data, no synthetic catalog rows."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(300))
    normalized_title: Mapped[str] = mapped_column(String(300), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    date_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    date_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_min: Mapped[int | None] = mapped_column(Integer)
    city: Mapped[str] = mapped_column(String(120), index=True)
    venue: Mapped[str] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(String(500))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    price_min: Mapped[float | None] = mapped_column(Float)
    price_max: Mapped[float | None] = mapped_column(Float)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)
    age_limit: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(120))
    org_link: Mapped[str | None] = mapped_column(Text)
    poster_url: Mapped[str | None] = mapped_column(Text)
    poster_rights: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50), default='import')
    source_hash: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default='draft', index=True)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    sources: Mapped[list['EventSource']] = relationship(cascade='all, delete-orphan', lazy='selectin')
    ticket_evidence: Mapped['EventTicketEvidence | None'] = relationship(cascade='all, delete-orphan', lazy='selectin', uselist=False)


class EventTicketEvidence(Base):
    __tablename__ = 'event_ticket_evidence'
    event_id: Mapped[str] = mapped_column(ForeignKey('events.id'), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    availability: Mapped[str | None] = mapped_column(String(20), index=True)
    image_url: Mapped[str | None] = mapped_column(Text)
    facts: Mapped[dict] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EventSource(Base):
    __tablename__ = 'event_sources'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey('events.id'), index=True)
    url: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(200))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderIdentity(Base):
    """A provider's session identifier is stable across edits and moves."""
    __tablename__ = 'provider_identities'
    __table_args__ = (UniqueConstraint('source_scope', 'provider_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_scope: Mapped[str] = mapped_column(String(300))
    provider_id: Mapped[str] = mapped_column(String(300))
    event_id: Mapped[str] = mapped_column(ForeignKey('events.id'), index=True)


class RawSourceRecord(Base):
    __tablename__ = 'raw_source_records'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    payload: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey('events.id'))
    outcome: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = 'audit_log'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(80), default='moderator')
    action: Mapped[str] = mapped_column(String(80))
    event_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    url: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(40), default='awaiting_review')
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Subscription(Base):
    __tablename__ = 'subscriptions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64))
    idem_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    city: Mapped[str] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(40))
    period: Mapped[str | None] = mapped_column(String(40))
    transport: Mapped[str | None] = mapped_column(String(20))
    transport_target: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Feedback(Base):
    __tablename__ = 'feedback'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_id: Mapped[str | None] = mapped_column(String(36))
    message: Mapped[str] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(300))
    idem_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentTrace(Base):
    __tablename__ = 'agent_traces'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CostUsage(Base):
    __tablename__ = 'cost_usage'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    trace_id: Mapped[str] = mapped_column(String(36))
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LLMQuota(Base):
    __tablename__ = 'llm_quotas'
    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    reserved_cost: Mapped[float] = mapped_column(Float, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)


class AgentDialog(Base):
    __tablename__ = 'agent_dialogs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dialog_key: Mapped[str] = mapped_column(String(100), unique=True)
    identity_hash: Mapped[str] = mapped_column(String(64), index=True)
    currency: Mapped[str] = mapped_column(String(8), default='RUB')
    reserved_cost: Mapped[float] = mapped_column(Float, default=0)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentAction(Base):
    __tablename__ = 'agent_actions'
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64), index=True)
    tool: Mapped[str] = mapped_column(String(40))
    arguments: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationOutbox(Base):
    __tablename__ = 'notification_outbox'
    __table_args__ = (UniqueConstraint('subscription_id', 'event_id', 'version'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    subscription_id: Mapped[str] = mapped_column(ForeignKey('subscriptions.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey('events.id'))
    version: Mapped[str] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
