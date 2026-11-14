"""Shared, deterministic distinction between a new request and a refinement."""
import re


CITY_FORMS = {
    'Уфа':r'уф[аеуы]', 'Стерлитамак':r'стерлитамак[ае]?',
    'Салават':r'салават[ае]?', 'Нефтекамск':r'нефтекамск[ае]?',
    'Октябрьский':r'октябрьск(?:ий|ом)', 'Белорецк':r'белорецк[ае]?',
    'Бирск':r'бирск[ае]?', 'Ишимбай':r'ишимба[йе]',
    'Кумертау':r'кумертау', 'Туймазы':r'туймаз(?:ы|ах)',
}


def _text(message):
    return re.sub(r'[^а-яa-z0-9]+',' ',message.lower().replace('ё','е')).strip()


def mentioned_city(message, cities=()):
    text=_text(message)
    patterns=[*CITY_FORMS.values(),*(re.escape(_text(city)) for city in cities)]
    return any(re.search(rf'\b(?:{pattern})\b',text) for pattern in patterns)


def starts_new_search(message, previous=None, cities=()):
    text=_text(message)
    if re.search(r'^(?:давай\s+)?(?:новый поиск|нач(?:ни|ать|нем) (?:новый )?поиск|сброс(?:ь|ить|ьте) (?:поиск|фильтры|условия)|начнем заново)\b',text):
        return True
    # References, short answers and changes to one condition retain context.
    if (re.search(r'^(?:а|тогда|теперь|лучше|нет|также|еще|дальше|на|до|после|без|только|дешевле|подешевле|ближе|рядом|мне|нам|ребенку|возраст|вернемся|вернись|вернуться|снова)\b',text)
            or re.search(r'\b(?:еще|похожие|похожее|другие|другой|эти|это|тот же|те же|сохрани|подтверждаю)\b',text)
            or text.isdecimal()):
        return False
    if re.search(r'^(?:куда (?:можно )?(?:пойти|сходить)|что (?:есть|посмотреть|интересного)|какие (?:события|мероприятия|спектакли|концерты)|посоветуй|порекомендуй|подбери|хочу (?:сходить|пойти)|найди|найти|ищу)\b',text):
        return True
    if mentioned_city(message,cities):
        filters=(previous or {}).get('filters',previous or {})
        # A city alone can be an answer to the initial city question.
        other_condition=bool(re.search(r'завтра|сегодня|понедельник|вторник|сред[ауе]|четверг|пятниц|суббот|воскресен|выходн|недел|\d|театр|спектак|концерт|выстав|ребен|дет|бюджет',text))
        return bool(filters.get('city') or other_condition)
    return False


def fresh_search_state(message, previous=None, cities=()):
    """Keep a known city only when the new request does not name another city."""
    filters=(previous or {}).get('filters',previous or {})
    city=filters.get('city')
    state = {'filters':{'city':city}} if city and not mentioned_city(message,cities) else {}
    turns = (previous or {}).get('turns')
    if isinstance(turns,list) and turns:
        state['turns'] = turns[-20:]
    return state
