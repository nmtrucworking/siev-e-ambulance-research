# E-Ambulance — Runbook từ OSMnx đến 30 paired replications

## 1. Trạng thái implementation

Đã có code chạy thực tế cho:

- synthetic dataset V2;
- EDA trước OSMnx;
- notebook build OSMnx network;
- travel provider có 2 mode:
  - `osmnx` cho experiment chính;
  - `haversine_smoke_test` chỉ để kiểm tra engine;
- rolling DES theo từng policy;
- B0, B3, B4;
- B6 dùng `scipy.optimize.milp` với ε-constraint operational point;
- DES event log;
- policy-specific vehicle/SOC/availability state;
- policy-specific synthetic hospital occupancy overlay;
- charging transition;
- replication metrics;
- runtime invariant validator;
- paired-run manifest;
- paired comparison analysis.

## 2. Trạng thái kiểm thử hiện tại

### 1-rep smoke test

- 3 scenarios × 1 replication × 4 policies = 12 policy runs.
- Completed: 12/12.
- Runtime validation: 7/7 PASS.
- Routing source: `haversine_smoke_test`.
- Không dùng làm kết quả khoa học cuối cùng.

### 3-rep scale test

- 3 scenarios × 3 replications × 4 policies = 36 policy runs.
- Completed: 36/36.
- Runtime validation: 7/7 PASS.
- 9/9 scenario-replication pairs complete.
- Routing source: `haversine_smoke_test`.
- Không dùng làm kết quả khoa học cuối cùng.

## 3. Các invariant validator đang kiểm tra

1. B0/B3/B4/B6 trong cùng replication dùng chung seed/common-random group.
2. Mỗi run có đúng 100 decision rows và 100 mission rows.
3. SOC nằm trong [0, 100].
4. B3/B4/B6 không chọn hospital sai capability/capacity hard rule.
5. B4/B6 không chọn assignment vi phạm deterministic SOC reserve.
6. Event order không đi ngược thời gian logic.
7. `ChargingEnds => SOC_after >= SOC_before`.

## 3.1. Policy nomenclature canonical

Tên policy dùng trong manuscript, code, output và phân tích phải giữ thống nhất là
`B0`, `B3`, `B4`, `B6`.

- `B0` — naive nearest-dispatch reference. Chỉ yêu cầu ambulance đang available,
  route reachable, hospital có emergency receiving, và đủ năng lượng vật lý để tới
  hospital. B0 cố ý **không** enforce specialty/capability, modeled capacity hoặc
  reserve-SOC. Vì vậy violation của B0 chỉ dùng để mô tả chi phí của một baseline
  đơn giản; không được diễn giải như một phép so sánh superiority dưới cùng feasible set.
- `B3` — hospital-aware baseline. Bổ sung hard capability và modeled-capacity
  feasibility lên các điều kiện cơ bản của B0.
- `B4` — deterministic EV-feasibility baseline. Bổ sung deterministic SOC reserve,
  bao gồm năng lượng từ vehicle tới incident, incident tới hospital và hospital tới
  charger gần nhất, trong khi vẫn giữ capability/capacity hard rules.
- `B6` — integrated uncertainty-aware ε-constraint MILP. Bổ sung uncertainty buffer
  cho energy feasibility; chọn một candidate bằng objective response-time +
  urgency-weighted SLA lateness dưới các ε-bound về transport+wait và energy fraction.
  Core experiment dùng một pre-specified ε operating rule, không phải full Pareto sweep.

Khi đánh giá marginal value của integration, `B6-B3` và `B6-B4` là các comparator
quan trọng hơn `B6-B0`, vì B3/B4 chia sẻ nhiều hard-feasibility rules với B6 hơn.

## 4. Cài môi trường

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
```

## 5. Chạy dataset + validator gốc

```bash
export PYTHONHASHSEED=0
python src/generate_dataset.py --output .
python src/validate_dataset.py
```

## 6. Chạy Notebook 01

Mở:

```text
notebooks/01_EDA_Pre_OSMnx.ipynb
```

Output chính:

```text
eda_pre_osmnx/osmnx_points.csv
eda_pre_osmnx/osmnx_bbox.json
```

## 7. Build OSMnx network

CLI chuẩn (khuyến nghị):

```bash
.venv/bin/python src/build_osmnx_network.py --precheck
.venv/bin/python src/build_osmnx_network.py
```

Notebook `notebooks/02_Build_OSMnx_Network.ipynb` chỉ là thin wrapper tương tác. Nếu dùng notebook, đặt `RUN_NETWORK = True`; giữ `FORCE_REDOWNLOAD = False` để ưu tiên graph cache.

Phải sinh:

```text
data/osmnx/study_graph.graphml
data/osmnx/point_node_map.csv
data/osmnx/snap_qa.csv
data/osmnx/incident_hospital_od.csv.gz
data/osmnx/hospital_charger_od.csv
data/osmnx/network_metadata.json
```

## 8. Chạy smoke test engine trước OSMnx

Chỉ dùng để test code:

```bash
./scripts/run_smoke.sh
python src/validate_runtime_outputs.py --experiment experiments/smoke_1rep
```

Không dùng các số metric từ smoke test trong paper.

## 9. Chạy 3-rep engineering test

```bash
python src/run_experiment.py \
  --root . \
  --output experiments/smoke_3rep_local \
  --replications-per-scenario 3 \
  --policies B0 B3 B4 B6 \
  --allow-haversine-fallback \
  --overwrite

python src/validate_runtime_outputs.py \
  --experiment experiments/smoke_3rep_local
```

## 10. Chạy experiment chính 30 paired replications

**Điều kiện:** `data/osmnx/*` phải tồn tại. Không truyền `--allow-haversine-fallback`.

```bash
./scripts/run_30_paired_osmnx.sh
```

Tương đương:

```bash
python src/run_experiment.py \
  --root . \
  --output experiments/paired_30 \
  --replications-per-scenario 30 \
  --policies B0 B3 B4 B6 \
  --overwrite
```

Sau đó:

```bash
python src/validate_runtime_outputs.py \
  --experiment experiments/paired_30
```

và:

```bash
python src/analyze_paired.py \
  --input experiments/paired_30/replication_metrics.csv \
  --output experiments/paired_30/analysis
```

## 11. Quy mô experiment chính

```text
3 scenarios
× 30 replications/scenario
= 90 exogenous realizations

90 realizations
× 4 policies
= 360 policy runs
```

Mỗi `(scenario_id, replication_id)` phải có:

```text
B0 / B3 / B4 / B6
```

với cùng:

```text
random_seed
common_random_group
```

## 12. Output mỗi run

```text
experiments/paired_30/runs/<scenario>/<rep>/<policy>/
├── dispatch_decisions.csv.gz
├── des_event_log.csv.gz
├── mission_metrics.csv.gz
├── constraint_violations.csv
└── replication_metrics.json
```

Aggregate:

```text
experiments/paired_30/
├── run_manifest.csv
├── replication_metrics.csv
├── run_failures.csv
├── run_summary.json
├── runtime_validation.json
└── analysis/
    ├── pairing_audit.csv
    ├── pairing_completeness.json
    └── paired_comparison_summary.csv
```

## 13. Statistical unit

Không dùng 9.000 incidents như 9.000 independent experiments.

Cho mỗi scenario, thống kê chính dựa trên 30 paired differences:

```text
B6(R001) - B0(R001)
...
B6(R030) - B0(R030)
```

Tương tự B6-B3 và B6-B4.

Phân tích hiện tại (`src/analyze_paired.py`) báo cáo cho từng scenario và metric:

- số paired replications;
- mean và median paired improvement, được orient sao cho số dương nghĩa là B6 tốt hơn;
- bootstrap 95% CI trên vector paired differences (5.000 bootstrap samples);
- paired standardized effect size `dz = mean(diff) / sd(diff)`.

Core analyzer hiện không sinh p-value. Manuscript không được tuyên bố statistical
significance nếu chưa bổ sung và chạy một paired hypothesis test được pre-specified.

## 14. Ranh giới khoa học hiện tại

- Hospital capacity là modeled synthetic receiving capacity, không phải bed data thực.
- Traffic OSMnx `speed_kph/travel_time` là network/free-flow baseline nếu chưa calibration local.
- Haversine fallback chỉ là engineering smoke test.
- `charge_trigger_pct`, `charge_target_pct`, handover time và occupancy hold time trong `configs/runtime_model.json` là explicit simulation assumptions cần sensitivity/calibration.
- B6 operational run dùng một ε operating rule; full Pareto frontier vẫn cần ε-grid sweep riêng nếu đưa vào paper.
- Energy stress hiện có thể còn yếu; cần sensitivity/low-SOC stress để chứng minh marginal value của B4/B6.
- Core B0/B3/B4/B6 ladder không phải controlled one-factor-at-a-time ablation. Nếu manuscript giữ RQ về ablation, phải chạy thêm các biến thể `Full-minus-component` riêng.
