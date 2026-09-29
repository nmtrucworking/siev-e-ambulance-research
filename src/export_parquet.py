#!/usr/bin/env python3
import argparse, gzip, pandas as pd
from pathlib import Path
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    args=ap.parse_args()
    sample=args.root/"data"/"sample"; out=args.root/"data"/"parquet"; out.mkdir(parents=True,exist_ok=True)
    for p in sample.glob("*.csv*"):
        df=pd.read_csv(p)
        target=out/(p.name.replace(".csv.gz","").replace(".csv","")+".parquet")
        df.to_parquet(target,index=False,compression="zstd")
        print(target)
if __name__=="__main__": main()
