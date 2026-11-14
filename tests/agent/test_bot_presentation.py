"""One well-formed Telegram response instead of a stream of separate cards."""
from html import unescape
from html.parser import HTMLParser
import re

import pytest

from apps.bot import service
from tests.agent.test_agent import catalog_event


def result(**changes):
    value = {'text':'Нашёл подтверждённые варианты по вашим условиям.', 'events':[catalog_event(str(i)) for i in range(5)],
             'mode':'llm', 'state':{'filters':{'city':'Уфа','age':8,'group_size':2,'price_max':1000}},
             'explanations':{}, 'more_nonce':'TEST_DATA_nonce'}
    return {**value, **changes}


class ValidHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
    def handle_starttag(self, tag, attrs):
        assert tag in {'b','i','a','code'}
        self.stack.append(tag)
    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


@pytest.mark.parametrize('mode', ['llm','fallback'])
def test_five_cards_are_rendered_as_one_safe_message(mode):
    presentation = service.search_presentation(result(mode=mode), 'https://example.org')
    assert '<b>' in presentation['text']
    assert all(f'{i}.' in presentation['text'] for i in range(1,6))
    assert len(presentation['text']) <= 4096
    parser = ValidHTML()
    parser.feed(presentation['text'])
    assert not parser.stack
    buttons = [button for row in presentation['buttons'] for button in row]
    assert sum('/events/' in button.get('url','') for button in buttons) == 5
    assert any(button.get('callback_data')=='more:TEST_DATA_nonce' for button in buttons)


def test_empty_response_shows_retained_constraints_and_reset_action():
    view = service.search_presentation(result(events=[], more_nonce=None, text='Нет событий по всем условиям.'), 'https://example.org')
    plain = unescape(re.sub('<[^>]*>', '', view['text']))
    assert 'Уфа' in plain and '1000' in plain and '8' in plain and '2' in plain
    assert any(button.get('callback_data')=='search' for row in view['buttons'] for button in row)


def test_long_untrusted_text_cannot_break_html_or_telegram_limit():
    events = [catalog_event(str(i),title='🎭 <&>'*300,venue='<&>'*300,description='DO_NOT_DUMP_DESCRIPTION'*100) for i in range(5)]
    view = service.search_presentation(result(events=events, text='<script>'*500, explanations={str(i):'<b>'*300 for i in range(5)}), 'https://example.org')
    assert '<script>' not in view['text'] and 'DO_NOT_DUMP_DESCRIPTION' not in view['text']
    plain = unescape(re.sub('<[^>]*>', '', view['text']))
    assert len(plain.encode('utf-16-le'))//2 <= 4096
    assert len(view['text']) <= 4096
    parser = ValidHTML()
    parser.feed(view['text'])
    assert not parser.stack


def test_question_and_confirmation_are_in_same_response_and_not_duplicated():
    question='Сколько лет ребёнку?'
    view = service.search_presentation(result(events=[], text=question, question=question, pending_action={'nonce':'TEST_DATA_confirm_nonce'},more_nonce=None), 'https://example.org')
    assert view['text'].count(question) == 1
    assert any(button.get('callback_data')=='agentconfirm:TEST_DATA_confirm_nonce' for row in view['buttons'] for button in row)


def test_clarification_does_not_claim_no_events_before_age_is_known():
    view = service.search_presentation(result(events=[],text='Нет подтверждённых событий по всем условиям.', question='Сколько лет ребёнку?',pending_action=None,more_nonce=None), 'https://example.org')
    assert 'Сколько лет ребёнку?' in view['text']
    assert 'Нет подтверждённых событий' not in view['text']


@pytest.mark.parametrize(('dates', 'expected'), [
    ({'date_from':'2026-11-15'}, 'с 15.11.2026'),
    ({'date_to':'2026-11-21'}, 'до 21.11.2026'),
    ({'date_from':'2026-11-15','date_to':'2026-11-15'}, '15.11.2026'),
    ({'date_from':'2026-11-15','date_to':'2026-11-21'}, '15.11.2026–21.11.2026'),
])
def test_search_period_distinguishes_one_day_from_open_ended_search(dates, expected):
    view = service.search_presentation(result(state={'filters':{'city':'Уфа',**dates}}), 'https://example.org')
    assert f'<i>Уфа · {expected}</i>' in view['text']


def test_rich_article_embeds_only_real_event_photos_and_local_event_buttons():
    from aiogram.types import InputRichMessage
    events=[catalog_event(str(i),title=f'TEST_DATA событие {i}',image_url=f'https://example.org/{i}.jpg') for i in range(3)]
    events.append(catalog_event('no-photo',title='TEST_DATA без фотографии'))
    view=service.rich_search_presentation(result(events=events),'https://example.org')
    InputRichMessage.model_validate(view['rich_message'])
    blocks=view['rich_message']['blocks']
    photos=[b['photo']['media'] for b in blocks if b['type']=='photo']
    assert photos==[f'https://example.org/{i}.jpg' for i in range(3)]
    headings=[b['text'] for b in blocks if b['type']=='heading']
    assert any('без фотографии' in title for title in headings)
    assert all(len(b['buttons'])<=2 for b in blocks if b['type']=='buttons')
    assert all(len(row)<=2 for row in view['buttons'])


@pytest.mark.parametrize('url',['http://example.org/photo.jpg','https://127.0.0.1/a.jpg','https://localhost/a.jpg','https://user:pass@example.org/a.jpg','javascript:alert(1)','https://example.org:bad/a.jpg','https://[invalid/a.jpg',None])
def test_rich_article_does_not_insert_invalid_or_missing_photo(url):
    view=service.rich_search_presentation(result(events=[catalog_event('e',image_url=url)]),'https://example.org')
    assert not any(b['type']=='photo' for b in view['rich_message']['blocks'])


def test_each_session_has_its_photo_directly_after_its_title():
    from aiogram.types import InputRichMessage
    events=[catalog_event(str(i),title='<script>TEST_DATA</script>',image_url='https://example.org/shared.jpg') for i in range(2)]
    view=service.rich_search_presentation(result(events=events),'https://example.org')
    parsed=InputRichMessage.model_validate(view['rich_message'])
    assert parsed.html is None and parsed.markdown is None
    blocks=view['rich_message']['blocks']
    assert sum(b['type']=='photo' for b in blocks)==2
    for index,block in enumerate(blocks):
        if block['type']=='heading' and block['size']==3:
            assert blocks[index+1]['type']=='photo' and blocks[index+2]['type']=='paragraph'
    assert any('<script>TEST_DATA</script>' in b.get('text','') for b in view['rich_message']['blocks'])


@pytest.mark.parametrize('mode',['llm','fallback'])
def test_no_catalog_or_timezone_footer_in_messages(mode):
    view=service.search_presentation(result(mode=mode),'https://example.org')
    assert 'Данные каталога' not in view['text'] and 'время по Башкортостану' not in view['text']
    assert 'данные каталога' not in view['text']


def test_matching_editorial_photo_is_used_for_event_without_api_photo():
    event=catalog_event('opera',org_link='https://www.bashopera.ru/repertoire/ballet/278/15859/',image_url=None,poster_url=None)
    view=service.rich_search_presentation(result(events=[event]),'https://example.org')
    photos=[b['photo']['media'] for b in view['rich_message']['blocks'] if b['type']=='photo']
    assert photos==['https://example.org/editorial/701ee766dde398e8.jpg']


def test_fallback_keyboard_has_no_cramped_three_button_rows():
    view=service.search_presentation(result(),'https://example.org')
    assert all(len(row)<=2 for row in view['buttons'])


def test_photo_preparation_uploads_files_once_and_keeps_each_session_photo(monkeypatch):
    import asyncio
    from aiogram.types import BufferedInputFile
    calls=[]
    def read(url,*args):
        calls.append(url)
        return b'\xff\xd8\xffTEST_DATA'
    monkeypatch.setattr(service,'_photo_bytes',read)
    event=catalog_event('e',image_url='https://example.org/photo.jpg')
    view=service.rich_search_presentation(result(events=[event,{**event,'id':'e2'}]),'https://example.org')
    prepared=asyncio.run(service.prepare_rich_media(view['rich_message'],'https://example.org'))
    photos=[block.photo.media for block in prepared.blocks if block.type=='photo']
    assert len(calls)==1 and len(photos)==2
    assert all(isinstance(photo,BufferedInputFile) for photo in photos)


def test_photo_failure_does_not_remove_facts_or_buttons(monkeypatch):
    import asyncio
    def unavailable(*args):
        raise ValueError('TEST_DATA blocked source')
    monkeypatch.setattr(service,'_photo_bytes',unavailable)
    view=service.rich_search_presentation(result(events=[catalog_event('e',image_url='https://example.org/photo.jpg')]),'https://example.org')
    prepared=asyncio.run(service.prepare_rich_media(view['rich_message'],'https://example.org'))
    assert not any(block.type=='photo' for block in prepared.blocks)
    assert any(block.type=='heading' and 'TEST_DATA' in block.text for block in prepared.blocks)
    assert any(block.type=='buttons' for block in prepared.blocks)


@pytest.mark.parametrize('content_type,raw', [('text/html',b'<html>TEST_DATA'),('image/jpeg',b'not-an-image')])
def test_remote_photo_refuses_html_and_invalid_image_bytes(monkeypatch,content_type,raw):
    import httpx
    class Client:
        def __init__(self,**kwargs):
            assert not kwargs['follow_redirects'] and not kwargs['trust_env']
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def get(self,url,**kwargs):
            return httpx.Response(200,request=httpx.Request('GET',url),headers={'Content-Type':content_type},content=raw)
    monkeypatch.setattr(service.httpx,'Client',Client)
    service._photo_bytes.cache_clear()
    assert service._photo_bytes('https://example.org/TEST_DATA.jpg','https://example.org',0) is None
    service._photo_bytes.cache_clear()
