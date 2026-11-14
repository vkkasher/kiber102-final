"""Outbound-only ticket provider contract. No booking, payment or inventory claims."""
from typing import Protocol, TypedDict
from urllib.parse import urlsplit


class TicketLink(TypedDict):
    url: str
    label: str
    provider: str


class TicketProvider(Protocol):
    def links(self, event) -> list[TicketLink]: ...


class OutboundTicketProvider:
    """Use only a URL supplied by the source or confirmed by a moderator."""
    def links(self, event) -> list[TicketLink]:
        if not event.org_link:
            return []
        host = (urlsplit(event.org_link).hostname or '').lower()
        if host == 'kassy.ru' or host.endswith('.kassy.ru'):
            name, label = 'kassy', 'Страница продажи билетов'
        elif host == 'timepad.ru' or host.endswith('.timepad.ru'):
            name, label = 'timepad', 'Страница регистрации у партнёра'
        else:
            name, label = 'organizer', 'Страница организатора'
        return [{'url': event.org_link, 'label': label, 'provider': name}]


ticket_provider: TicketProvider = OutboundTicketProvider()
