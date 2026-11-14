"""Build formal planner input from dialogue without inventing missing inputs."""
import re

NUMBERS = {'один':1, 'одна':1, 'одного':1, 'одним':1, 'одной':1,
           'два':2, 'двое':2, 'двумя':2, 'двух':2, 'две':2,
           'три':3, 'трое':3, 'тремя':3, 'трех':3, 'четыре':4, 'четырех':4}


def compose_plan(message, filters, previous):
    from .agent import PlanRequest, normalized
    values = dict(previous.get('plan_inputs') or {})
    text = normalized(message)
    if filters.city:
        values['city'] = filters.city
    date_hint = re.search(r'20\d{2}|\d[./]\d|сегодня|завтра|недел|выходн|суббот|воскрес|понедель|вторник|сред|четвер|пятниц|январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр', message, re.I)
    prior_dates = previous.get('filters', {}).get('date_from')
    if filters.date_from and (date_hint or prior_dates):
        if filters.date_to and filters.date_to != filters.date_from:
            values.pop('date', None)
        else:
            values['date'] = filters.date_from
    if filters.price_max is not None:
        values['budget'] = filters.price_max
    window = re.search(r'\bс\s*(\d{1,2})(?::(\d{2}))?\s*(?:час(?:а|ов)?)?\s+до\s*(\d{1,2})(?::(\d{2}))?\s*(?:час(?:а|ов)?)?', message, re.I)
    if window:
        values['window_start'] = f'{int(window[1]):02}:{window[2] or "00"}'
        values['window_end'] = f'{int(window[3]):02}:{window[4] or "00"}'
    if filters.start_after:
        values['window_start'] = filters.start_after
    if filters.end_before:
        values['window_end'] = filters.end_before
    number = r'\d{1,2}|' + '|'.join(NUMBERS)
    adult = re.search(rf'\b({number})\s+взросл\w*', text)
    if adult:
        values['adults'] = int(adult[1]) if adult[1].isdigit() else NUMBERS[adult[1]]
    age_groups = re.finditer(r'(?:дет(?:ьми|ям|и|ей)|реб[её]н\w*)\s*((?:\d{1,2}\s*(?:лет|года?)?\s*(?:,|и|/|\s)\s*)*\d{1,2})\s*(?:лет|года?)', message.lower())
    ages = [int(age) for group in age_groups for age in re.findall(r'\d{1,2}', group[1])]
    if ages:
        values['children_ages'] = ages
    child_hint = bool(re.search(r'ребен|дет(?:ям|и|ьми|ей)', text))
    if filters.group_size_known and not adult:
        if values.get('children_ages'):
            values['adults'] = filters.group_size-len(values['children_ages'])
        elif not child_hint and filters.age is None:
            values['adults'] = filters.group_size
            values['children_ages'] = []
    if adult and not child_hint and 'children_ages' not in values:
        values['children_ages'] = []
    travel = re.search(r'(?:переезд\w*|перемещен\w*|дорог\w*|ехать)\s*(?:не более|не больше|до|максимум)?\s*(\d{1,3})\s*мин', text)
    if not travel:
        travel = re.search(r'(\d{1,3})\s*мин\w*\s*(?:на\s+)?(?:переезд|дорог|перемещ)', text)
    if travel:
        values['max_travel_minutes'], values['max_travel_known'] = int(travel[1]), True
    elif re.search(r'без ограничен.*(?:переезд|дорог|перемещ)', text):
        values['max_travel_minutes'], values['max_travel_known'] = None, True
    count = re.search(rf'\b({number})\s+событи', text)
    if count:
        values['event_count'] = int(count[1]) if count[1].isdigit() else NUMBERS[count[1]]
    if filters.category:
        values['interests'] = [filters.category]
    questions = [
        ('city', 'В каком городе составить план?'),
        ('date', 'На какой один день составить план?'),
        ('budget', 'Какой бюджет на билеты для всей группы?'),
        ('window_start', 'Какое свободное окно? Например, с 10:00 до 20:00.'),
        ('window_end', 'До какого времени должен закончиться план?'),
        ('adults', 'Сколько взрослых и детей в группе? Укажите возраст каждого ребёнка.'),
        ('children_ages', 'Каков возраст каждого ребёнка? Если детей нет, напишите «без детей».'),
        ('max_travel_known', 'Сколько минут допустимо на один переезд? Можно написать «без ограничений на переезды».'),
    ]
    if re.search(r'без детей|только взросл', text):
        values['children_ages'] = []
    for field, question in questions:
        if field not in values or values[field] is None:
            return None, values, question
    try:
        request = PlanRequest.model_validate(values)
    except ValueError:
        return None, values, 'Проверьте состав группы, дату, бюджет и окно времени. Конец окна должен быть позже начала.'
    return request, {**request.model_dump(mode='json'), 'max_travel_known':True}, None
