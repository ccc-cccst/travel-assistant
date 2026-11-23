"""Apply the same repaired planning visibility to model and rule APIs."""
from functools import lru_cache
import os
import sys
import pandas as pd

_PROFILE = os.environ.get('CHINATRAVEL_PROFILE')
_ENABLED = _PROFILE in ('expanded11', 'expanded12')

@lru_cache(maxsize=1)
def _sandbox():
    if not _ENABLED:
        raise RuntimeError('Expanded sandbox profile is not active')
    if _PROFILE == 'expanded12':
        runtime = os.environ['CT_SANDBOX_RUNTIME_DIR']
        if runtime not in sys.path:
            sys.path.insert(0, runtime)
        from sandbox_adapter import Sandbox
    else:
        from .sandbox.sandbox_adapter import Sandbox

    return Sandbox('G3')

def scoped_frame(frame, kind, slug, lang):
    if not _ENABLED or lang != 'zh':
        return frame.dropna() if kind == 'accommodation' else frame
    sandbox = _sandbox()
    city = next(c for c,s in sandbox.cities.items() if s == slug)
    items = sandbox._eligible(kind,city)
    rows = []
    for _, item in items:
        payload = item['payload']
        access = payload['access']
        if not access['planning_position_available']:
            continue
        row = dict(payload['data'])
        if kind == 'accommodation' and pd.isna(row.get('price')):
            # Estimates are available for reference through the evidence catalog.
            # A numeric midpoint alone cannot certify a hard travel budget.
            continue
        rows.append(row)
    columns = list(frame.columns)
    if _PROFILE == 'expanded12':
        extra = {'attraction': ('category_values',), 'restaurant': ('cuisine_values',)}
        columns.extend(key for key in extra.get(kind, ()) if key not in columns)
    output = pd.DataFrame(rows, columns=columns)
    for key in ('id','lat','lon','price','numbed','recommendmintime','recommendmaxtime'):
        if key in output:
            output[key] = pd.to_numeric(output[key], errors='coerce')
    return output.reset_index(drop=True)

@lru_cache(maxsize=None)
def blocked_poi_names(city):
    if not _ENABLED:
        return set()
    sandbox = _sandbox()
    if city not in sandbox.cities:
        city = next((name for name,slug in sandbox.cities.items() if slug == city), city)
    if city not in sandbox.cities:
        return set()
    excluded, admitted = set(), set()
    for kind in ('attraction','restaurant','accommodation'):
        _, _, _, exact, _, _, _ = sandbox._catalog(kind,city)
        for index,_ in exact:
            item = sandbox._record(kind,city,index)
            access = item['payload']['access']
            # Directory visibility is broader than routeability. Do not expose
            # derived coordinates through the raw POI table and let the rule
            # planner bypass the same on-route verification used by the model.
            routeable = access['main_path'] and access['planning_position_available']
            (admitted if routeable else excluded).add(item['name'])
    # Keep a name if a different, independently eligible record uses it.
    return excluded - admitted
