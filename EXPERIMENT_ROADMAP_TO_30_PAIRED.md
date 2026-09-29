# Roadmap từ OSMnx đến 30 paired replications

## 02 — OSMnx network
Output: graph, point-node map, OD cache, routing QA.

## 03 — Traffic
Output: time-dependent travel-time layer.
Default hiện tại là free-flow reference γ=1.0 vì chưa có calibration local đủ mạnh.

## 04 — Energy + feasibility
Khóa hard constraints: availability, hospital capability/capacity, SOC reserve, SLA.

## 05 — Rolling DES + baselines
Triển khai event loop và policy-specific endogenous state.
B0/B3/B4 chạy trước; audit invariant.

## 06 — B6 ε-constraint MILP
Không thay bằng weighted-sum heuristic.
Log ε bounds, solver version, runtime, MIP gap, objective values.

## 07 — 30 paired replications
3 scenarios × 30 exogenous realizations = 90.
4 policies => 360 policy runs.
Cùng random seed/common_random_group giữa B0/B3/B4/B6.

## Statistical unit
Một scenario có 30 paired replication differences.
Không coi 100 incident/replication là 100 independent experiments.
