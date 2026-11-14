"""Deterministic full-catalog schedule search with evidence-bound travel legs."""
from __future__ import annotations

import math
from datetime import timedelta


def build_plan(payload, events, travel_provider=None):
    from .agent import SearchFilters, confirmed_price, event_datetime, event_end, normalized, validate_events

    people = payload.adults + len(payload.children_ages)
    filters = SearchFilters(city=payload.city, date_from=payload.date.isoformat(), date_to=payload.date.isoformat(),
                            age=min(payload.children_ages) if payload.children_ages else None)
    # Budget and window must not remove the very candidates needed to diagnose
    # their incompatibility. There is no former 40-record truncation.
    candidates = validate_events(events, filters)
    if payload.interests:
        candidates = [e for e in candidates if any(normalized(i) in normalized(str(e.get('category')) + ' ' + str(e.get('tags'))) for i in payload.interests)]
    candidates = [e for e in candidates if event_end(e) is not None and confirmed_price(e) is not None]
    candidates.sort(key=lambda e: event_datetime(e['date_start']))
    routes, unknown = {}, False

    def route(a, b):
        nonlocal unknown
        key = (a['id'], b['id'])
        if key in routes:
            return routes[key]
        same = (a.get('venue') and a.get('address') and normalized(a['venue']) == normalized(b.get('venue'))
                and normalized(a['address']) == normalized(b.get('address')))
        leg = {'minutes':0, 'source':'same_venue', 'checked_at':None} if same else None
        if not same and travel_provider:
            try:
                supplied = travel_provider(a, b)
                if (isinstance(supplied, dict) and isinstance(supplied.get('minutes'), (int, float))
                        and math.isfinite(supplied['minutes']) and 0 <= supplied['minutes'] <= 1440
                        and supplied.get('source') and event_datetime(supplied.get('checked_at'))):
                    leg = {k:supplied[k] for k in ['minutes', 'source', 'checked_at']}
                    for field in ['mode', 'note']:
                        if isinstance(supplied.get(field), str):
                            leg[field] = supplied[field][:500]
            except (ValueError, TimeoutError):
                pass
        if leg is None:
            unknown = True
        routes[key] = leg
        return leg

    def link(a, b, enforce_travel=True):
        gap = (event_datetime(b['date_start']) - event_end(a)).total_seconds() / 60
        if gap < 0:
            return None
        leg = route(a, b)
        if leg is None or leg['minutes'] > gap:
            return None
        if enforce_travel and payload.max_travel_minutes is not None and leg['minutes'] > payload.max_travel_minutes:
            return None
        return leg

    def in_window(event):
        return event_datetime(event['date_start']).time() >= payload.window_start and event_end(event).time() <= payload.window_end and event_end(event).date() == payload.date

    window = [e for e in candidates if in_window(e)]

    # For a fixed final event and count, the cheapest prefix dominates every
    # more expensive prefix. This interval DP examines all candidates in O(4n²)
    # rather than enumerating O(n⁴) combinations.
    paths = {1:{position:(confirmed_price(event)[1]*people, [event]) for position, event in enumerate(window)}}
    for count in range(2, 5):
        paths[count] = {}
        for position, event in enumerate(window):
            best = None
            for prior, (cost, prefix) in paths[count-1].items():
                next_cost = cost + confirmed_price(event)[1]*people
                if prior >= position:
                    continue
                if link(prefix[-1], event) is None:
                    continue
                if best is None or next_cost < best[0]:
                    best = (next_cost, [*prefix, event])
            if best:
                paths[count][position] = best
    desired_count = getattr(payload, 'event_count', None)
    counts = [desired_count] if desired_count else range(4, 1, -1)
    for count in counts:
        affordable_paths = [item for item in paths[count].values() if item[0] <= payload.budget]
        if not affordable_paths:
            continue
        _, found = min(affordable_paths, key=lambda item:item[0])
        legs = []
        for a, b in zip(found, found[1:]):
            evidence = route(a, b)
            departure = event_end(a)
            arrival = departure + timedelta(minutes=evidence['minutes'])
            legs.append({**evidence, 'from_event_id':a['id'], 'to_event_id':b['id'],
                         'departure_at':departure.isoformat(), 'arrival_at':arrival.isoformat(),
                         'buffer_minutes':(event_datetime(b['date_start'])-arrival).total_seconds()/60})
        modes = {leg.get('mode', 'unknown') for leg in legs if leg['source'] != 'same_venue'}
        text = 'Подобрал события без пересечений. Стоимость рассчитана по верхней и нижней границам опубликованных цен на всю группу. Цену конкретного сеанса и льготы уточняйте у организатора.'
        if 'driving' in modes:
            text += ' Переезды оценены для автомобиля без учёта пробок и парковки. При другом способе передвижения время пути не подтверждено. Стоимость относится к билетам, расходы на дорогу не рассчитаны.'
        elif modes:
            text += ' Оценка пути и способ передвижения указаны для каждого переезда. Стоимость относится к билетам, расходы на дорогу не рассчитаны.'
        return {'text':text,
                'events':found, 'total_min':sum(confirmed_price(e)[0] for e in found)*people,
                'total_max':sum(confirmed_price(e)[1] for e in found)*people,
                'travel_status':'same_venue' if all(leg['source'] == 'same_venue' for leg in legs) else 'estimated',
                'travel_mode':next(iter(modes)) if len(modes) == 1 else 'mixed' if modes else 'none',
                'budget_scope':'event_tickets_only', 'travel_cost_status':'not_calculated' if modes else 'not_applicable',
                'travel_legs':legs, 'feasible':True, 'minimal_change':None}

    min_cost, min_travel, min_window = None, None, None
    cost_witness, travel_witness, window_witness = [], [], []
    target_count = desired_count or 2
    if paths[target_count]:
        cheapest, cheapest_path = min(paths[target_count].values(), key=lambda item:item[0])
        if cheapest > payload.budget:
            min_cost = cheapest
            cost_witness = cheapest_path
    for position, a in enumerate(candidates):
        for b in candidates[position+1:]:
            if target_count > 2:
                continue
            cost = (confirmed_price(a)[1]+confirmed_price(b)[1])*people
            leg = link(a, b, enforce_travel=False)
            if leg is None:
                continue
            travel_ok = payload.max_travel_minutes is None or leg['minutes'] <= payload.max_travel_minutes
            if in_window(a) and in_window(b):
                if travel_ok and cost > payload.budget:
                    if min_cost is None or cost < min_cost:
                        min_cost, cost_witness = cost, [a, b]
                if cost <= payload.budget and not travel_ok:
                    if min_travel is None or leg['minutes'] < min_travel:
                        min_travel, travel_witness = leg['minutes'], [a, b]
            if cost <= payload.budget and travel_ok:
                before = max(0, (payload.window_start.hour*60 + payload.window_start.minute) - (event_datetime(a['date_start']).hour*60 + event_datetime(a['date_start']).minute))
                after = max(0, (event_end(b).hour*60 + event_end(b).minute) - (payload.window_end.hour*60 + payload.window_end.minute))
                extension = before + after
                if extension and (min_window is None or extension < min_window[0]):
                    min_window = (extension, event_datetime(a['date_start']).strftime('%H:%M'), event_end(b).strftime('%H:%M'))
                    window_witness = [a, b]
    change, witness = None, []
    if min_cost is not None:
        change = {'field':'budget', 'value':min_cost, 'delta':min_cost-payload.budget}
        witness = cost_witness
        text = f'Бюджета не хватает. Для {target_count} событий с остальными условиями увеличьте бюджет до {min_cost:g} ₽ на всю группу, на {min_cost-payload.budget:g} ₽.'
    elif min_travel is not None:
        change = {'field':'max_travel_minutes', 'value':min_travel, 'delta':min_travel-payload.max_travel_minutes}
        witness = travel_witness
        text = f'Для двух событий с остальными условиями минимальное допустимое время переезда нужно увеличить до {min_travel:g} минут.'
    elif min_window:
        change = {'field':'window', 'extension_minutes':min_window[0], 'start':min_window[1], 'end':min_window[2]}
        witness = window_witness
        text = f'Не хватает свободного времени. Ближайший план из двух событий требует расширить окно на {min_window[0]:g} минут, до {min_window[1]}–{min_window[2]}.'
    elif unknown:
        text = 'Есть события, но путь между площадками не подтверждён. Не могу подтвердить план или минимальное изменение ограничения переезда без маршрута.'
    else:
        affordable = any(confirmed_price(e)[1]*people <= payload.budget for e in window)
        text = f'Не хватает {target_count} подтверждённых событий без пересечений с известной длительностью и полной ценой.'
        if affordable:
            fewer = max(count for count in range(1, target_count) if any(item[0] <= payload.budget for item in paths[count].values()))
            change = {'field':'event_count', 'value':fewer}
            _, witness = min((item for item in paths[fewer].values() if item[0] <= payload.budget), key=lambda item:item[0])
            text += f' Можно уменьшить число событий до {fewer}, сохранив остальные условия.'
        else:
            text += ' Не могу обосновать одно минимальное изменение без дополнительных данных.'
    if change:
        change['evidence_event_ids'] = [event['id'] for event in witness]
        change['evidence_source_urls'] = list(dict.fromkeys(event['source_url'] for event in witness))
    if unknown and change:
        change['scope'] = 'confirmed_routes_only'
        text += ' Это минимальное изменение среди вариантов с подтверждённым маршрутом. Для остальных площадок путь неизвестен.'
    return {'text':text, 'events':[], 'total_min':None, 'total_max':None,
            'travel_status':'unknown' if unknown else 'not_applicable', 'travel_legs':[], 'feasible':False, 'minimal_change':change}
