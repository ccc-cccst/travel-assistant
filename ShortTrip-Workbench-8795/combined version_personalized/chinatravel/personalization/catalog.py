"""Versioned attributes from the active planning tables; no name-based guesses."""
import re
from collections import Counter

VERSION = 'table_attributes_v1'
INTERESTS = {'museum': ('博物馆/纪念馆','美术馆/艺术馆','history_museum'),
             'history': ('历史古迹','红色景点','人文景观','monument'),
             'nature': ('自然风光',), 'park': ('公园','园林'),
             'city': ('商业街区',), 'amusement': ('游乐园/体育娱乐',)}
LABELS = {'museum':'博物馆与艺术馆','history':'历史文化','nature':'自然风光',
          'park':'公园园林','city':'城市街区','amusement':'游乐体验'}


def split_values(value):
    if isinstance(value,(list,tuple,set)): return {str(v).strip() for v in value if str(v).strip()}
    if value is None or str(value).lower() in ('nan','none','unknown',''): return set()
    return {v.strip() for v in re.split(r'[|,，;；、]',str(value)) if v.strip()}


class PreferenceCatalog:
    def __init__(self, env, city):
        self.city, self.version = city, VERSION
        self.env, self._points = env, {}
        self.records = {}
        for module, kind in [('attractions','attraction'),('restaurants','restaurant'),('accommodations','accommodation')]:
            self.records[kind] = {}
            frame=getattr(env,module).data.get(city)
            if frame is not None:
                for row in frame.to_dict('records'):
                    self.records[kind].setdefault(row['name'],[]).append(row)

    @classmethod
    def from_records(cls, city, attractions=(), restaurants=(), accommodations=()):
        value=cls.__new__(cls); value.city=city; value.version=VERSION; value.records={}; value.env=None; value._points={}
        for kind,rows in [('attraction',attractions),('restaurant',restaurants),('accommodation',accommodations)]:
            value.records[kind]={}
            for row in rows: value.records[kind].setdefault(row['name'],[]).append(dict(row))
        return value

    def distance(self, first, second):
        from experiments.timing_probe.day_sets import geo_distance
        import math
        for name in (first,second):
            if name not in self._points:
                try: self._points[name]=self.env.poi.search(self.city,name) if self.env else None
                except (ValueError,KeyError,TypeError): self._points[name]=None
        value=geo_distance(self._points[first],self._points[second])
        return value if math.isfinite(value) else None

    def attributes(self, kind, name):
        rows=self.records.get(kind,{}).get(name,[])
        if len(rows)!=1: return {'status':'unknown','reason':'missing_or_ambiguous_entity','tags':[],'values':[]}
        row=rows[0]
        field={'attraction':'type','restaurant':'cuisine','accommodation':'featurehoteltype'}[kind]
        values=split_values(row.get(field))
        if kind=='restaurant': values={'咖啡店' if v=='咖啡' else '快餐简餐' if v=='快餐' else v for v in values}
        tags=[key for key,aliases in INTERESTS.items() if values.intersection(aliases)] if kind=='attraction' else []
        return {'status':'known' if values else 'unknown','tags':tags,'values':sorted(values),
                'source_field':field,'source_kind':'snapshot_attribute','entity':name,
                'snapshot_scope':'benchmark_not_live','mapping_version':self.version}

    def options(self):
        interests=Counter(); cuisines=Counter(); hotels=Counter()
        unknown=Counter()
        for kind,names in self.records.items():
            for name in names:
                a=self.attributes(kind,name)
                if a['status']=='unknown': unknown[kind]+=1
                if kind=='attraction': interests.update(a['tags'])
                elif kind=='restaurant': cuisines.update(a['values'])
                else: hotels.update(a['values'])
        return {'city':self.city,'mapping_version':self.version,
                'interests':[{'id':k,'label':LABELS[k],'count':interests[k]} for k in INTERESTS],
                'cuisines':dict(cuisines),'hotel_features':dict(hotels),'unknown_attributes':dict(unknown),
                'walk_scope':'access_legs_only','live_data':False}
