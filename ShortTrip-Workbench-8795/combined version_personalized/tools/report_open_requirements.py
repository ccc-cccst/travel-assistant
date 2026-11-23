"""Summarize the frozen P037 run and review explicit constraints against its itineraries."""
import argparse
from pathlib import Path

from audit_new_trip_delivery import ROOT, read, save
from report_p036 import summarize


BUDGETS = {'easy_6': 1500, 'easy_27': 1500, 'medium_31': 3000, 'medium_69': 12000,
           'medium_99': 2000, 'medium_112': 6000, 'medium_77': 3500, 'medium_72': 7000,
           'medium_90': 4200, 'medium_82': 7000, 'medium_37': 3500, 'medium_117': 9000,
           'human_76': 2000, 'human_36': 2000}
ROOMS = {**dict.fromkeys(('medium_31', 'medium_69', 'medium_112', 'medium_77', 'medium_72',
                         'medium_90', 'medium_82', 'human_149'), 1),
         **dict.fromkeys(('medium_99', 'medium_37', 'medium_117'), 2)}
MODES = {**dict.fromkeys(('easy_292', 'medium_69', 'medium_72'), ('airplane', 'airplane')),
         **dict.fromkeys(('easy_272', 'medium_77', 'medium_90', 'medium_82'), ('train', 'train')),
         'medium_112': ('airplane', None), 'medium_117': ('train', None)}


def inspect(run, manifest):
    core = {row['case']: row['corpus_core'] for row in read(manifest)['cases']}
    audited = []
    for metric in read(run/'SUMMARY.json')['rows']:
        case = metric['case']
        if metric['outcome'] != 'original':
            continue
        folder = run/case
        if not (folder/'PLAN_VIEW.json').is_file() and (folder/'RECOVERY.json').is_file():
            attempt = read(folder/'RECOVERY.json').get('restored')
            if attempt:
                folder = ROOT/'result/manual'/metric['job']/attempt
        view = read(folder/'PLAN_VIEW.json')
        archive = read(folder/'CANDIDATE_ARCHIVE.json')['archive']
        plan = next(item['itinerary'] for item in archive if item['id'] == view['recommended_id'])
        activities = [a for day in plan['itinerary'] for a in day['activities']]
        expected = core[case]
        hotels = [a for a in activities if a['type']=='accommodation']
        tickets = [a['type'] for a in activities if a['type'] in ('train','airplane')]
        legs = [leg for activity in activities for leg in activity['transports']]
        checks = {
            'origin': plan['start_city']==expected['origin_city'],
            'destination': plan['target_city']==expected['destination_city'],
            'days': len(plan['itinerary'])==expected['days'],
            'people': plan['people_number']==expected['people'],
        }
        if case in BUDGETS:
            checks['budget'] = sum(a['cost'] for a in activities)+sum(leg['cost'] for leg in legs)<=BUDGETS[case]+.01
        if case in ROOMS:
            checks['rooms'] = len(hotels)==expected['days']-1 and all(h['rooms']==ROOMS[case] for h in hotels)
        if case in MODES:
            checks['intercity'] = len(tickets)==2 and all(
                required is None or actual==required for required,actual in zip(MODES[case],tickets))
        if case in ('easy_272','medium_99'):
            checks['local_mode'] = all(leg['mode'] in ('metro','walk') for leg in legs)
        if case=='easy_153':
            checks['hotel'] = len(hotels)==expected['days']-1 and all('深圳悦林酒店' in h['position'] for h in hotels)
        if case=='human_36':
            outbound = next(a for a in activities if a['type'] in ('train','airplane'))
            checks['outbound_evening'] = '18:00' <= outbound['start_time'] < '24:00'
        audited.append(dict(case=case,checks=checks))
    failed = [(row['case'], key) for row in audited for key,value in row['checks'].items() if not value]
    return save(run/'RAW_CHECKS.json', {'checked':audited,'failed':failed,
                'check_count':sum(len(row['checks']) for row in audited),
                'scope':'Core fields, named hotel, explicit numeric budgets, rooms and modes on original deliveries.'})


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    print(summarize(args.run))
    review=inspect(args.run,args.run.parent/'FROZEN30.json')
    print(review['check_count'], review['failed'])
