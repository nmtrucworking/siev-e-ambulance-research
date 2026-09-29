#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, gzip, json, sqlite3
from pathlib import Path

def rows(path):
    opener=gzip.open if path.suffix==".gz" else open
    with opener(path,"rt",encoding="utf-8",newline="") as f:
        yield from csv.DictReader(f)

def main():
    ap=argparse.ArgumentParser(description="Validate E-Ambulance V2 synthetic benchmark.")
    ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    args=ap.parse_args()
    r=args.root; s=r/"data"/"sample"
    checks=[]
    def add(name,ok,detail): checks.append({"check":name,"pass":bool(ok),"detail":detail})

    reps=list(rows(s/"replications.csv.gz")); inc=list(rows(s/"incidents.csv.gz")); fleet=list(rows(s/"fleet_initial.csv.gz"))
    add("replication_count_90",len(reps)==90,len(reps))
    add("incident_count_9000",len(inc)==9000,len(inc))
    add("incident_ids_unique",len({x["incident_id"] for x in inc})==len(inc),len({x["incident_id"] for x in inc}))
    from collections import Counter
    c=Counter((x["scenario_id"],x["replication_id"]) for x in inc)
    add("100_incidents_per_scenario_rep",all(v==100 for v in c.values()) and len(c)==90,{"groups":len(c),"min":min(c.values()),"max":max(c.values())})
    add("90_unique_seeds",len({x["random_seed"] for x in reps})==90,len({x["random_seed"] for x in reps}))
    add("initial_soc_range",all(0<=float(x["soc_init_pct"])<=100 for x in fleet),"0..100")
    add("battery_capacity_positive",all(float(x["battery_capacity_kwh"])>0 for x in fleet),">0")

    caps=list(rows(s/"hospital_capacity_timeseries.csv.gz"))
    add("capacity_row_count_51840",len(caps)==51840,len(caps))
    add("capacity_nonnegative",all(int(x["capacity_t"])>=0 for x in caps),">=0")
    nominal={x["hospital_id"]:int(x["nominal_model_capacity"]) for x in rows(s/"hospitals.csv")}
    add("capacity_not_above_nominal",all(int(x["capacity_t"])<=nominal[x["hospital_id"]] for x in caps),"<= nominal")

    cand_count=0; ids=set(); unique=True
    for x in rows(s/"candidate_index.csv.gz"):
        cand_count+=1
        if x["candidate_id"] in ids: unique=False
        ids.add(x["candidate_id"])
    add("candidate_count_540000",cand_count==540000,cand_count)
    add("candidate_ids_unique",unique and len(ids)==cand_count,len(ids))

    # Reference candidates
    ref_files=[s/"candidate_reference_example_normal.csv.gz",s/"candidate_reference_example_peak.csv.gz",s/"candidate_reference_example_high_demand.csv.gz"]
    ref=list(rows(ref_files[0])); ref+=list(rows(ref_files[1])); ref+=list(rows(ref_files[2]))
    selected=[x for x in ref if x["candidate_feasible_hard"]=="1"]
    add("reference_no_charging_assignments",all(x["vehicle_state"]!="charging" for x in selected),"hard feasibility excludes charging")
    add("reference_soc_valid",all(float(x["soc_at_nearest_charger_pct"])>=float(x["reserve_soc_pct"]) for x in selected),"reserve respected")
    add("reference_capacity_valid",all(int(x["capacity_t"])>0 for x in selected),"capacity respected")
    add("reference_role_labeled",all(x["record_role"]=="INITIAL_STATE_REFERENCE_ONLY" for x in ref),"reference-only")

    all_pass=all(x["pass"] for x in checks)
    (r/"validation").mkdir(exist_ok=True)
    (r/"validation"/"audit_results.json").write_text(json.dumps({"all_pass":all_pass,"checks":checks},indent=2),encoding="utf-8")
    summary={"all_pass":all_pass,"checks_passed":sum(x["pass"] for x in checks),"checks_total":len(checks),"note":"QA validates synthetic generator consistency; it does not establish calibration to real HCMC EMS."}
    (r/"validation"/"validation_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))
    raise SystemExit(0 if all_pass else 1)
if __name__=="__main__": main()
