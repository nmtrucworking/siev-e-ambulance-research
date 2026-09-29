#!/usr/bin/env python3
from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--experiment', default='experiments/smoke_1rep')
    ap.add_argument('--expected-incidents-per-run', type=int, default=100)
    args=ap.parse_args()
    exp=Path(args.experiment)
    manifest=pd.read_csv(exp/'run_manifest.csv')
    metrics=pd.read_csv(exp/'replication_metrics.csv')
    checks=[]
    def add(name, passed, detail):
        checks.append({'check':name,'passed':bool(passed),'detail':detail})

    # Pairing
    pair=(manifest.groupby(['scenario_id','replication_id']).agg(policy_count=('policy_id','nunique'),seed_count=('random_seed','nunique'),group_count=('common_random_group','nunique')).reset_index())
    add('common_random_pairing', ((pair.policy_count==4)&(pair.seed_count==1)&(pair.group_count==1)).all(), pair.to_dict('records')[:5])
    add('metrics_count_matches_completed_runs', len(metrics)==len(manifest), f'{len(metrics)}/{len(manifest)}')

    run_dirs=list((exp/'runs').glob('*/*/*'))
    bad_counts=[]; bad_soc=[]; bad_events=[]; bad_policy=[]; charging_bad=[]
    for rd in run_dirs:
        if not rd.is_dir(): continue
        dec=pd.read_csv(rd/'dispatch_decisions.csv.gz')
        mis=pd.read_csv(rd/'mission_metrics.csv.gz')
        ev=pd.read_csv(rd/'des_event_log.csv.gz')
        policy=rd.name
        if len(dec)!=args.expected_incidents_per_run or len(mis)!=args.expected_incidents_per_run:
            bad_counts.append((str(rd),len(dec),len(mis)))
        soc_cols=[c for c in ['soc_before_pct','soc_after_hospital_pct','soc_final_pct'] if c in mis]
        for c in soc_cols:
            x=pd.to_numeric(mis[c], errors='coerce').dropna()
            if ((x<0)|(x>100)).any(): bad_soc.append((str(rd),c,int(((x<0)|(x>100)).sum())))
        # selected safety rules by policy
        served=mis[mis.served==1]
        if policy in {'B3','B4','B6'}:
            if (served.capability_pass==0).any() or (served.capacity_pass==0).any(): bad_policy.append((str(rd),'hospital hard rule'))
        if policy in {'B4','B6'} and (served.soc_pass==0).any(): bad_policy.append((str(rd),'SOC hard rule'))
        # event timestamp parse + incident ordering
        if len(ev):
            ev['timestamp']=pd.to_datetime(ev['timestamp'], utc=True, format='mixed')
            order=['IncidentCreated','AmbulanceDispatched','AmbulanceArrivesPatient','TransportStarts','HospitalArrival','HandoverComplete','ChargingStarts','ChargingEnds','AmbulanceAvailable']
            rank={x:i for i,x in enumerate(order)}
            for iid,g in ev[ev.incident_id.notna()].groupby('incident_id'):
                seq=[rank.get(x,999) for x in g.sort_values('timestamp').event_type]
                # Ignore IncidentUnserved; for served missions order should not go backwards.
                seq=[x for x in seq if x<999]
                if any(b<a for a,b in zip(seq,seq[1:])):
                    bad_events.append((str(rd),iid,seq)); break
            # charging monotonic within same incident/vehicle
            starts=ev[ev.event_type=='ChargingStarts'][['incident_id','vehicle_id','soc_before_pct']] if 'soc_before_pct' in ev else pd.DataFrame()
            ends=ev[ev.event_type=='ChargingEnds'][['incident_id','vehicle_id','soc_after_pct']] if 'soc_after_pct' in ev else pd.DataFrame()
            if len(starts) and len(ends):
                z=starts.merge(ends,on=['incident_id','vehicle_id'],how='inner')
                if (z.soc_after_pct < z.soc_before_pct).any(): charging_bad.append(str(rd))

    add('100_incident_rows_per_run', not bad_counts, bad_counts[:5])
    add('SOC_bounds_0_100', not bad_soc, bad_soc[:5])
    add('policy_hard_constraints', not bad_policy, bad_policy[:5])
    add('event_order_monotonic', not bad_events, bad_events[:5])
    add('charging_completion_SOC_nondecreasing', not charging_bad, charging_bad[:5])

    result={'all_pass':all(c['passed'] for c in checks),'checks_passed':sum(c['passed'] for c in checks),'checks_total':len(checks),'checks':checks}
    out=exp/'runtime_validation.json'; out.write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,indent=2,default=str))

if __name__=='__main__': main()
