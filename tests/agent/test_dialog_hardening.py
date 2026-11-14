"""User-visible dialogue regressions, isolated from working data and paid models."""
import asyncio
from datetime import date

import httpx
import pytest

from apps.api.app import agent
from apps.bot.service import BotService, StateStore
from tests.agent.test_agent import catalog_event
from tests.agent.test_private_context_and_empty_evidence import catalog_app


@pytest.mark.parametrize('today', [date(2026, 11, 15), date(2026, 11, 16)])
@pytest.mark.parametrize('weekday,offset', [('понедельник', 0), ('вторник', 1), ('среду', 2), ('четверг', 3), ('пятницу', 4), ('субботу', 5), ('воскресенье', 6)])
def test_weekdays_are_single_dates_and_not_rolling_weeks(today, weekday, offset):
    from datetime import timedelta
    filters, _ = agent.extract_filters('В Уфе в ' + weekday, {}, ['Уфа'], today)
    expected = (today + timedelta(days=(offset-today.weekday()) % 7)).isoformat()
    assert filters.date_from == filters.date_to == expected


@pytest.mark.parametrize('phrase,expected', [('в понедельник на следующей неделе', '2026-11-16'), ('в следующий понедельник', '2026-11-16'), ('во вторник на следующей неделе', '2026-11-17')])
def test_named_weekday_in_explicit_next_week(phrase, expected):
    filters, _ = agent.extract_filters('В Уфе ' + phrase, {}, ['Уфа'], date(2026, 11, 10))
    assert filters.date_from == filters.date_to == expected


@pytest.mark.parametrize('phrase,age', [('Мне 18 лет', 18), ('Мне 18 лкт', 18), ('мне 18', 18), ('ребёнку 8', 8), ('нам 20 лет', 20), ('Возраст 35 лет', 35)])
def test_age_answers_are_not_event_titles(phrase, age):
    filters, _ = agent.extract_filters(phrase, {'filters':{'city':'Уфа'}}, ['Уфа'], date(2026, 11, 15))
    assert filters.age == age
    assert filters.q is None


def test_old_corrupt_title_is_healed_without_dropping_real_constraints():
    state = {'filters':{'city':'Уфа', 'q':'Мне', 'age':8, 'price_max':1000, 'group_size':2, 'category':'спектакль'}}
    filters, _ = agent.extract_filters('а завтра', state, ['Уфа'], date(2026, 11, 15))
    assert filters.q is None
    assert filters.age == 8 and filters.price_max == 1000 and filters.group_size == 2
    assert filters.date_from == filters.date_to == '2026-11-16'


@pytest.mark.parametrize('message', ['Новый поиск: в Уфе завтра', 'Начни новый поиск в Уфе завтра', 'Сбрось фильтры, в Уфе завтра'])
def test_explicit_new_search_clears_previous_constraints(message):
    state = {'filters':{'city':'Стерлитамак', 'q':'Щелкунчик', 'age':8, 'price_max':1000, 'group_size':2, 'category':'спектакль'}}
    filters, _ = agent.extract_filters(message, state, ['Уфа', 'Стерлитамак'], date(2026, 11, 15))
    assert filters.city == 'Уфа' and filters.date_from == filters.date_to == '2026-11-16'
    assert filters.q is filters.age is filters.price_max is filters.category is None
    assert filters.group_size == 1


@pytest.mark.parametrize('message', ['подешевле', 'а ещё', 'только бесплатные', 'в другой день'])
def test_refinements_keep_real_named_title_and_family(message):
    state = {'filters':{'city':'Уфа', 'q':'Щелкунчик', 'age':8, 'group_size':2, 'group_size_known':True, 'price_max':15000}}
    filters, _ = agent.extract_filters(message, state, ['Уфа'], date(2026, 11, 15))
    assert filters.q == 'Щелкунчик' and filters.age == 8 and filters.group_size == 2


def test_multiple_blockers_explain_age_with_catalog_evidence():
    event = catalog_event('adult-only', age_limit=12, price_min=None, price_max=None)
    app, traces = catalog_app([event])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            body = (await client.post('/api/v1/agent/query', json={'message':'В Уфе 14 ноября 2026 с ребёнком 8 лет до 1000 рублей на двоих', 'offline':True})).json()
            assert body['events'] == []
            assert '12+' in body['text'] and '8' in body['text']
            assert 'Не могу обосновать' not in body['text']
            assert traces[-1]['event_ids'] == ['adult-only']
            assert body['tool_calls'] <= 5
    asyncio.run(scenario())


def test_no_data_explains_catalog_coverage_without_fabricated_alternatives():
    app, traces = catalog_app([])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            body = (await client.post('/api/v1/agent/query', json={'message':'В Уфе 14 ноября 2026', 'offline':True})).json()
            assert body['events'] == [] and traces[-1]['event_ids'] == []
            assert 'Не могу обосновать' not in body['text']
            assert 'Уфа' in body['text'] and 'каталог' in body['text'].lower()
    asyncio.run(scenario())


def test_reset_keeps_unsubscribe_capabilities_and_discards_pending_search(tmp_path):
    store = StateStore(tmp_path/'TEST_DATA-bot.sqlite')
    subscription = {'id':'TEST_DATA-sub', 'delete_token':'TEST_DATA-capability'}
    store.put(10, {'search':{'filters':{'q':'Мне'}}, 'subscriptions':[subscription], 'dialog_id':'TEST_DATA-dialog', 'agent_pending':{'nonce':'TEST_DATA-nonce'}})
    async def scenario():
        async with httpx.AsyncClient(base_url='http://test') as client:
            await BotService(client, store).cancel(10)
    asyncio.run(scenario())
    assert store.get(10) == {'subscriptions':[subscription]}


@pytest.mark.parametrize('message', ['Покажи в Уфе завтра', 'Найди для ребёнка 8 лет в Уфе', 'Покажи мне события в Уфе', 'Найди В Уфе завтра', 'Привет, в Уфе завтра', 'Мне 18 лет, что есть в Уфе?', 'до 1000 рублей', 'Бюджет 2000 рублей', 'Цена до 1000 рублей', 'Стерлитамак завтра', 'Двое взрослых и ребёнок 8 лет'])
def test_search_instructions_never_become_title_filters(message):
    filters, _ = agent.extract_filters(message, {}, ['Уфа'], date(2026, 11, 15))
    assert filters.q is None


@pytest.mark.parametrize('title', ['Три мушкетёра', 'Аленький цветочек', 'Лето человеческое'])
def test_real_bare_titles_are_still_searchable(title):
    filters, _ = agent.extract_filters(title, {'filters':{'city':'Уфа'}}, ['Уфа'], date(2026,11,15))
    assert filters.q == title


@pytest.mark.parametrize('message', ['все события', 'Покажи все события', 'без названия'])
def test_explicitly_removing_title_preserves_other_constraints(message):
    filters, _ = agent.extract_filters(message, {'filters':{'city':'Уфа','age':8,'price_max':1000,'q':'Щелкунчик'}}, ['Уфа'], date(2026, 11, 15))
    assert filters.q is None and filters.age == 8 and filters.price_max == 1000


@pytest.mark.parametrize('bad_query', ['Мне', '18', 'завтра', 'Джаз из фантазии'])
def test_model_cannot_invent_title_constraints_for_structured_request(monkeypatch, bad_query):
    async def bad_extract(*args, **kwargs):
        return agent.SearchFilters(city='Уфа',q=bad_query), {'model':'TEST_DATA'}
    monkeypatch.setattr(agent, 'llm_extract', bad_extract)
    app, _ = catalog_app([catalog_event('actual')])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            body=(await client.post('/api/v1/agent/query',json={'message':'В Уфе 14 ноября 2026'})).json()
            assert body['state']['filters'].get('q') is None
            assert [item['id'] for item in body['events']] == ['actual']
    asyncio.run(scenario())


def test_typed_new_search_discards_old_confirmation_and_dialog(tmp_path):
    import json
    store = StateStore(tmp_path/'TEST_DATA-bot.sqlite')
    store.put(10, {'search':{'filters':{'q':'Мне'}}, 'agent_pending':{'nonce':'TEST_DATA-old'}, 'dialog_id':'TEST_DATA-old'})
    received = []
    def handle(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json={'events':[],'text':'Укажите город','state':{'filters':{}},'mode':'fallback'})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
            await BotService(client,store).query(10,'Новый поиск')
    asyncio.run(scenario())
    assert received[0]['state'] == {} and received[0]['dialog_id'] is None
    assert 'agent_pending' not in store.get(10)


@pytest.mark.parametrize('answer', ['8', '8 лет', 'ребёнку 8'])
def test_short_age_answer_resolves_pending_question(answer):
    filters, question = agent.extract_filters(answer, {'filters':{'city':'Уфа','age_pending':True}}, ['Уфа'], date(2026,11,15))
    assert filters.age == 8 and not filters.age_pending and question is None
    assert filters.q is None


def test_short_group_answer_resolves_budget_question():
    filters, question = agent.extract_filters('2', {'filters':{'city':'Уфа','age':8,'price_max':1000,'needs_group_size':True}}, ['Уфа'], date(2026,11,15))
    assert filters.group_size == 2 and filters.group_size_known and not filters.needs_group_size
    assert question is None


@pytest.mark.parametrize('message', [
    'В Уфе завтра', 'Уфа завтра', 'В Уфе', 'Стерлитамак в субботу',
    'Куда пойти завтра?', 'Что есть в Уфе?', 'Найди концерт в Уфе',
    'Хочу сходить в театр завтра', 'Посоветуй выставку',
    'В Уфе с ребёнком 8 лет в субботу до 1000 на двоих',
])
def test_standalone_request_resets_unmentioned_constraints_naturally(message):
    state={'filters':{'city':'Уфа','q':'Щелкунчик','age':12,'group_size':3,'price_max':5000,'category':'кино'}}
    filters, _=agent.extract_filters(message,state,['Уфа','Стерлитамак'],date(2026,11,15))
    assert filters.q is None
    assert filters.city in {'Уфа','Стерлитамак'}
    if 'ребёнком' not in message:
        assert filters.age is None and filters.price_max is None and filters.group_size==1
    if 'концерт' not in message and 'театр' not in message and 'выставку' not in message:
        assert filters.category is None


@pytest.mark.parametrize('message', ['а в Уфе завтра', 'тогда на субботу', 'на субботу', 'а в Стерлитамаке?', 'Мне 18 лет', 'до 2000 рублей', 'покажи ещё', 'найди похожие', 'покажи другие события'])
def test_natural_refinements_keep_constraints(message):
    state={'filters':{'city':'Уфа','q':'Щелкунчик','age':8,'group_size':2,'price_max':15000}}
    filters,_=agent.extract_filters(message,state,['Уфа','Стерлитамак'],date(2026,11,15))
    assert filters.q=='Щелкунчик' and filters.group_size==2
    assert filters.age==(18 if message=='Мне 18 лет' else 8)


def test_standalone_bot_request_discards_stale_pending_action(tmp_path):
    import json
    store=StateStore(tmp_path/'TEST_DATA-bot.sqlite')
    store.put(10,{'search':{'filters':{'city':'Уфа','age':8,'q':'Щелкунчик'}},'agent_pending':{'nonce':'TEST_DATA-old'},'dialog_id':'TEST_DATA-old','subscriptions':[{'id':'TEST_DATA-sub'}]})
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'text':'TEST_DATA','events':[],'state':{'filters':{'city':'Уфа'}},'mode':'fallback'})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://test') as client:
            await BotService(client,store).query(10,'В Уфе завтра')
    asyncio.run(scenario())
    assert requests[0]['dialog_id'] is None
    assert not requests[0]['state'].get('filters',{}).get('age')
    assert 'agent_pending' not in store.get(10) and store.get(10)['subscriptions']
