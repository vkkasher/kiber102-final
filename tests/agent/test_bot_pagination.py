import asyncio
import json

import httpx
import pytest

from apps.bot.service import BotService, StateStore, search_presentation
from tests.agent.test_agent import catalog_event
from tests.agent.test_private_context_and_empty_evidence import catalog_app


@pytest.mark.parametrize(('index','pages','labels'),[(0,3,['1/3','→']),(1,3,['←','2/3','→']),(2,3,['←','3/3']),(0,2,['1/2','→']),(1,2,['←','2/2'])])
def test_pagination_buttons_match_page_edges(index,pages,labels):
    body={'text':'TEST_DATA','events':[catalog_event('e')],'state':{},'page_navigation':{'index':index,'pages':pages,'nonce':'TEST_DATA_nonce'}}
    rows=search_presentation(body,'https://example.org')['buttons']
    row=next(row for row in rows if any(button.get('callback_data','').startswith('page:') for button in row))
    assert [button['text'] for button in row]==labels


def test_api_page_size_and_total_are_based_on_validated_catalog():
    events=[catalog_event(str(i),date_start=f'2026-11-14T{10+i:02}:00:00+05:00') for i in range(7)]
    app,_=catalog_app(events)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            first=(await client.post('/api/v1/agent/query',json={'message':'В Уфе 14.11.2026','offline':True,'result_limit':3})).json()
            assert len(first['events'])==3 and first['state']['result_count']==7
            second=(await client.post('/api/v1/agent/query',json={'message':'а ещё','state':first['state'],'offline':True,'result_limit':3})).json()
            assert len(second['events'])==3 and second['state']['result_count']==7
            assert not set(e['id'] for e in first['events']) & set(e['id'] for e in second['events'])
    asyncio.run(scenario())


def test_pages_cache_back_navigation_and_bind_buttons_to_user_and_search(tmp_path):
    calls=[]
    def handle(request):
        body=json.loads(request.content)
        assert body['result_limit']==3
        index=len(calls)
        calls.append(body)
        events=[catalog_event(str(i)) for i in range(index*3,index*3+3)]
        return httpx.Response(200,json={'text':'TEST_DATA','events':events,'mode':'fallback','state':{'filters':{'city':'Уфа'},'result_count':9,'last_ids':[e['id'] for e in events]}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://test') as client:
            bot=BotService(client,StateStore(tmp_path/'TEST_DATA-pages.sqlite'))
            first=await bot.query(10,'В Уфе')
            nav=first['page_navigation']
            assert nav['index']==0 and nav['pages']==3
            second=await bot.page(10,nav['nonce'],1)
            assert second['page_navigation']['index']==1 and len(calls)==2
            back=await bot.page(10,nav['nonce'],0)
            assert [e['id'] for e in back['events']]==['0','1','2'] and len(calls)==2
            await bot.page(10,nav['nonce'],1)
            assert len(calls)==2
            last=await bot.page(10,nav['nonce'],2)
            assert last['page_navigation']['index']==2 and len(calls)==3
            with pytest.raises(ValueError):
                await bot.page(11,nav['nonce'],1)
            with pytest.raises(ValueError):
                await bot.page(10,nav['nonce'],3)
            await bot.query(10,'В Уфе завтра')
            with pytest.raises(ValueError):
                await bot.page(10,nav['nonce'],1)
    asyncio.run(scenario())


def test_four_event_day_plan_is_split_locally_without_replanning(tmp_path):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'text':'TEST_DATA план','events':[catalog_event(str(i)) for i in range(4)],'state':{'filters':{'city':'Уфа'}},'mode':'fallback','plan':{'feasible':True}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://test') as client:
            bot=BotService(client,StateStore(tmp_path/'TEST_DATA-plan-pages.sqlite'))
            first=await bot.query(10,'Составь план на день')
            assert len(first['events'])==3 and first['page_navigation']['pages']==2
            last=await bot.page(10,first['page_navigation']['nonce'],1)
            assert [event['id'] for event in last['events']]==['3'] and len(calls)==1
    asyncio.run(scenario())


def test_page_callback_edits_the_same_article_without_sending_new_messages(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from types import SimpleNamespace
    import aiogram
    from aiogram.types import CallbackQuery,Chat,Message,User
    from apps.bot import main,notifications
    edits,acks=[],[]
    user=User(id=100,is_bot=False,first_name='TEST_DATA')
    message=Message(message_id=77,date=datetime.now(timezone.utc),chat=Chat(id=100,type='private'),from_user=user,text='TEST_DATA')
    callback=CallbackQuery(id='TEST_DATA',from_user=user,chat_instance='TEST_DATA',data='page:TEST_DATA_nonce:1',message=message)
    result={'text':'TEST_DATA','events':[catalog_event(str(i)) for i in range(3)],'state':{},'page_navigation':{'index':1,'pages':3,'nonce':'TEST_DATA_nonce'}}
    async def page(self,user_id,nonce,index):
        assert (user_id,nonce,index)==(100,'TEST_DATA_nonce',1)
        return {**result,'page_unchanged':bool(edits)}
    async def edit_message_text(**kwargs):
        edits.append(kwargs)
    async def close():
        pass
    async def acknowledge(self,*args,**kwargs):
        acks.append(kwargs)
    async def forbidden(*args,**kwargs):
        raise AssertionError('Pagination must not send another message')
    async def delivery(*args):
        await asyncio.sleep(3600)
    async def polling(self,bot,**kwargs):
        handler=next(h.callback for h in self.callback_query.handlers if h.callback.__name__=='page')
        await handler(callback)
        await handler(callback)
    monkeypatch.setattr(main,'load_dotenv',lambda *args,**kwargs:False)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','TEST_DATA-no-network')
    monkeypatch.setenv('BOT_STATE_PATH',str(tmp_path/'TEST_DATA-bot.sqlite'))
    monkeypatch.setenv('WEB_PUBLIC_URL','https://example.org')
    monkeypatch.setattr(aiogram,'Bot',lambda *args,**kwargs:SimpleNamespace(session=SimpleNamespace(close=close),edit_message_text=edit_message_text,send_rich_message=forbidden))
    monkeypatch.setattr(aiogram.Dispatcher,'start_polling',polling)
    monkeypatch.setattr(BotService,'page',page)
    monkeypatch.setattr(CallbackQuery,'answer',acknowledge)
    monkeypatch.setattr(Message,'answer',forbidden)
    monkeypatch.setattr(notifications,'delivery_loop',delivery)
    assert asyncio.run(main.run())==0
    assert len(edits)==1 and len(acks)==2
    assert edits[0]['chat_id']==100 and edits[0]['message_id']==77 and edits[0]['rich_message']
    rows=edits[0]['reply_markup'].inline_keyboard
    assert [button.text for button in rows[0]]==['←','2/3','→']
