"""Authenticated, exact-session enrichment with immutable raw provenance."""
import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from .models import Event, EventSource, EventTicketEvidence, RawSourceRecord, now
from .schemas import normalize
from .services import audit


class TicketSnapshotInput(BaseModel):
    source_url: str = Field(max_length=2000)
    content: str = Field(max_length=2_000_000)
    sha256: str = Field(pattern='^[a-f0-9]{64}$')


def apply_snapshot(session, snapshot):
    from apps.worker.kassy import parse_session
    if hashlib.sha256(snapshot.content.encode()).hexdigest() != snapshot.sha256:
        raise ValueError('Хеш снимка не совпадает с содержимым')
    item = parse_session(snapshot.content, snapshot.source_url)
    if item is None:
        return {'matched':0,'reason':'Сеанс не подтверждён структурированными данными страницы'}
    start = datetime.fromisoformat(item['date_start']).astimezone(timezone.utc)
    candidates = session.scalars(select(Event).where(Event.city == item['city'],Event.date_start == start)).all()
    matches = [event for event in candidates if normalize(event.title) == normalize(item['title'])
               and any(normalize(event.venue) == normalize(alias) or
                       (item['venue_kind']=='bashopera' and normalize(event.venue).startswith(normalize(alias)))
                       for alias in item['venue_aliases'])]
    if len(matches) != 1:
        return {'matched':0,'reason':'Нет единственного совпадения названия, города, площадки и времени'}
    event = matches[0]
    row = event.ticket_evidence
    if row is None:
        row = EventTicketEvidence(event_id=event.id)
        event.ticket_evidence = row
    # Unknown stock never clears an explicit previous withdrawal.
    if item['availability'] is not None:
        row.availability = item['availability']
    row.source_url, row.source_hash = snapshot.source_url, snapshot.sha256
    row.image_url, row.facts, row.checked_at = item['image_url'],item,now()
    source = next((source for source in event.sources if source.url==snapshot.source_url),None)
    if source is None:
        source=EventSource(url=snapshot.source_url,name='Кассы.ру: конкретный сеанс')
        event.sources.append(source)
    source.verified_at=row.checked_at
    if not session.scalar(select(RawSourceRecord.id).where(RawSourceRecord.event_id==event.id,RawSourceRecord.source_hash==snapshot.sha256)):
        session.add(RawSourceRecord(payload=snapshot.content,source_url=snapshot.source_url,
                                    source_hash=snapshot.sha256,event_id=event.id,outcome='ticket_verified',
                                    reason='Точное совпадение названия, города, площадки и времени сеанса'))
    audit(session,'ticket.observe',event.id,{'source_url':row.source_url,'source_hash':row.source_hash,
                                         'availability':row.availability,'matched_fields':['title','city','venue','date_start']})
    session.commit()
    return {'matched':1,'event_id':event.id,'availability':row.availability}
