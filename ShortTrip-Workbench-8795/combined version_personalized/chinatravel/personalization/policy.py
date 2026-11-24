"""Preference recall and ranking with required and geographic alternatives retained."""
from .request import fingerprint


class PreferencePolicy:
    def __init__(self,request,catalog):
        self.request=request; self.catalog=catalog
        self.fingerprint=fingerprint([request,catalog.version])
        self.trip_directions=[]
        self.dedupe_before_recall=False

    def recall_day(self,rows,ordered,required,visited,other_days,start,end,limit=12):
        if not self.trip_directions and not self.dedupe_before_recall:
            return self.recall(rows,ordered,required,limit)
        from .trip_recall import blend
        eligible=[i for i in ordered if rows.iloc[i]['name'] not in visited | other_days]
        baseline=self.recall(rows,eligible,required,limit)
        if not self.trip_directions:return baseline
        return blend(rows,baseline,eligible,required,self.trip_directions,self.catalog,start,end,limit)

    def affinity(self,kind,name):
        a=self.catalog.attributes(kind,name); result=0
        for p in self.request['preferences']:
            target=p['target']; d=p['dimension']
            matched=(d=='interest' and target in a['tags']) or (d=='cuisine' and kind=='restaurant' and target in a['values']) or (d=='hotel_feature' and kind=='accommodation' and target in a['values']) or (d=='place' and target==name)
            if matched: result+=(3 if p['priority']=='high' else 1)*(-1 if p['direction']=='avoid' else 1)
            if d=='anchor':
                distance=self.catalog.distance(name,target)
                if distance is not None: result+=1/(1+distance/3)
        return result

    def recall(self,rows,ordered,required=(),limit=12,kind='attraction'):
        if not self.request['preferences']: return ordered[:limit]
        required=[int(i) for i in ordered if i in set(required)]
        preferred=sorted(ordered,key=lambda i:(-self.affinity(kind,rows.iloc[i]['name']),ordered.index(i)))
        preferred=[i for i in preferred if self.affinity(kind,rows.iloc[i]['name'])>0]
        result=list(required)
        # Reserve half of non-required slots for geographic/baseline diversity.
        for i in preferred[:max(1,(limit-len(result))//2)]+list(ordered):
            if i not in result and len(result)<max(limit,len(required)): result.append(int(i))
        return result

    def context(self):
        return {'fingerprint':self.fingerprint,'preferences':self.request['preferences'],
                'data_scope':'snapshot_attributes','unknowns':self.request['unmapped_requirements']}

    def enrich(self,cards,kind):
        for c in cards: c['features']=self.catalog.attributes(kind,c['name'])

    def hotel_rank(self,indices,rows):
        if not self.request['preferences']: return list(indices)
        focus=any(p['dimension']=='focus' and p['target'] in ('cost','lodging_cost') for p in self.request['preferences'])
        ranks={int(i):n for n,i in enumerate(indices)}
        return sorted(indices,key=lambda i:(-self.affinity('accommodation',rows.iloc[i]['name']),
                      float(rows.iloc[i]['price']) if focus else ranks[int(i)],ranks[int(i)]))
