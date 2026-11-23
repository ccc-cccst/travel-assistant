"""Attach explicit preferences to the copied combination/day-set planner."""
from copy import deepcopy
from .catalog import PreferenceCatalog
from .compiler import compile_request
from .evaluation import evaluate_preferences
from .policy import PreferencePolicy


def personalized_agent(original,on_instance,request,candidate_recall='baseline'):
    from experiments.timing_probe.combo_schedule import combination_agent
    base=combination_agent(original,lambda agent:None)

    class PersonalizedAgent(base):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.personalization_policy=PreferencePolicy(request,PreferenceCatalog(self.env,request['trip']['destination_city']))
            self.personalization_policy.dedupe_before_recall=candidate_recall=='dedupe_only'
            self.trip_recall_report={'mode':candidate_recall,'state':'not_requested'}
            if candidate_recall=='dedupe_only':self.trip_recall_report['state']='filter_only'
            self._trip_recall_active=False
            on_instance(self)

        def limit_model_request(self,request_args):
            args=super().limit_model_request(request_args)
            if self._trip_recall_active:
                args['timeout']=min(args.get('timeout',30),30)
                args['max_tokens']=min(args.get('max_tokens',900),900)
            return args

        def prepare_trip_recall(self):
            import time
            from .trip_recall import context, propose, read_directions, VERSION
            from openai import APIError
            rows=self.memory['attractions']
            payload=context(request,rows,self.personalization_policy.catalog)
            self.trip_recall_report.update(version=VERSION,state='running',context=payload)
            before=time.perf_counter();self._trip_recall_active=True
            try:
                answer=propose(self.backbone_llm,payload)
                directions=read_directions(answer,set(range(len(rows))))
                self.trip_recall_report.update(answer=answer,directions=directions,
                    state='active' if directions else 'fallback',
                    reason=None if directions else 'no_valid_direction_ids')
                self.personalization_policy.trip_directions=directions
            except APIError as error:
                # Model unavailability is not evidence that a destination is bad.
                self.trip_recall_report.update(state='fallback',reason=type(error).__name__)
            finally:
                self._trip_recall_active=False
                self.trip_recall_report['seconds']=time.perf_counter()-before
                # Legacy search counters are created/reset by the outer search.
                # This earlier call is accounted in trip_recall.json and profiler.

        def generate_plan_with_search(self,query):
            if candidate_recall=='trip_api' and self.trip_recall_report['state']=='not_requested':
                self.prepare_trip_recall()
            return super().generate_plan_with_search(query)

        def translate_nl2sl(self,query,load_cache=False):
            # Compile only public, evidence-bearing request; no dataset reference is consumed.
            translated=compile_request(request)['legacy_query']
            translated['time_policy']=query.get('time_policy','flexible')
            return translated

        def ranking_hotel(self,rows,query):
            proposed=super().ranking_hotel(rows,query)
            return self.personalization_policy.hotel_rank(proposed,rows)

        def decide_rooms(self,query):
            return request['facts']['rooms'],request['facts']['room_type']

        def extract_budget(self,query):
            return request['facts']['budget']

        def ranking_innercity_transport_from_query(self,query):
            modes=list(request['facts']['local_modes'])
            if any(p['target']=='less_walking' for p in request['preferences'] if p['dimension']=='mobility'):
                modes.sort(key=lambda m: {'taxi':0,'metro':1,'walk':2}[m])
            return modes

        def reranking_hotel_with_constraints(self,proposed,rows,query,rooms):
            kept=super().reranking_hotel_with_constraints(proposed,rows,query,rooms)
            features=set(request.get('required_hotel_features',[]))
            if not features: return kept
            return [i for i in kept if features<=set(self.personalization_policy.catalog.attributes('accommodation',rows.iloc[i]['name'])['values'])]

        def filter_intercity(self,indices,rows,query,go=None):
            kept=super().filter_intercity(indices,rows,query,go)
            allowed=request.get('directional_modes',{}).get('outbound' if go is None else 'return')
            if allowed is None: return kept
            import pandas as pd
            return [i for i in kept if ('train' if pd.notna(rows.iloc[i].get('TrainID')) else 'airplane') in allowed]

        def constraints_validation(self,query,plan,root):
            report=evaluate_preferences({'itinerary':plan},request,self.personalization_policy.catalog)
            if not report['eligible']:
                self.record_prune({'rule':'personalization_hard_check','constraint':'confirmed_request','details':report['violations']})
                return False,plan
            return super().constraints_validation(query,plan,root)

        def _score(self,plan):
            score=super()._score(plan)
            report=evaluate_preferences(plan,request,self.personalization_policy.catalog)
            score['personalization']=report
            if request['preferences']:
                # Retain the old cost/traffic/rhythm penalty and add a bounded preference tradeoff.
                score['selection_key']=[score['total_penalty']+20*(1-(report['utility'] or 0)),score['total_penalty']]
            return score

        def _prefix_rank(self,prefix):
            old=super()._prefix_rank(prefix)
            if not request['preferences']: return old
            affinity=sum(self.personalization_policy.affinity('attraction',a.get('position'))
                         for d in prefix['plan'] for a in d['activities'] if a['type']=='attraction')
            return (*old,-affinity)

    return PersonalizedAgent
